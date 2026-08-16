import uuid
from time import perf_counter

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ChatReference,
    ChatRequest,
    ChatResponse,
    ExecutionMetadata,
    FastRAGResult,
    QualityStatus,
    RoutingDiagnostics,
)
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.graphs.conversation import build_conversation_state
from app.persistence.conversations import (
    ConversationMemory,
    TurnRecord,
    sanitize_evidence_for_memory,
    sanitize_filters_for_memory,
)
from app.observability.node_runs import current_node_recorder
from app.security.citations import CitationValidator
from app.security.redaction import opaque_identifier, sanitize_text

router = APIRouter()
_NOT_FOUND = "대화를 찾을 수 없습니다."
_INVALID_CITATIONS = "검증된 근거만으로 답변을 제공할 수 없습니다."
CONTEXT_UNAVAILABLE_DISCLOSURE = (
    "대화 저장소를 사용할 수 없어 이번 요청은 단일 턴으로 처리했습니다."
)
NO_EVIDENCE_GENERAL_DISCLOSURE = (
    "메일 검색에서 관련 근거를 찾지 못해 일반 정보로 답변했습니다."
)
_PUBLIC_ROUTING_REASONS = {
    "explicit_mode",
    "deterministic_fast",
    "deterministic_general",
    "deterministic_long_period",
    "deterministic_mail",
    "deterministic_multi_team",
    "deterministic_research_output",
    "router_error_deterministic_fast",
    "router_error_deterministic_general",
    "router_error_deterministic_long_period",
    "router_error_deterministic_mail",
    "router_error_deterministic_multi_team",
    "router_error_deterministic_research_output",
    "deterministic_diagnostic",
    "deterministic_corpus_info",
}


def _policy_for(payload: ChatRequest) -> PolicyContext:
    """The body owner is authoritative; an upstream gateway must authenticate it."""
    try:
        return PolicyContext.from_user_id(payload.user_id)
    except ValidationError:
        raise AppError(
            ErrorCode.INVALID_USER_ID,
            "유효하지 않은 사용자 식별자입니다.",
        ) from None


def _routing_diagnostics(payload, decision, executed_system, memory=None):
    reason_code = sanitize_text(decision.reason_code)
    if reason_code not in _PUBLIC_ROUTING_REASONS:
        reason_code = f"model_{decision.route}"
    history_count = len(getattr(memory, "messages", None) or [])
    used_history = 0
    if history_count:
        state = build_conversation_state(payload, memory)
        used_history = max(0, len(state["messages"]) - 1)
    return RoutingDiagnostics(
        requested_mode=payload.response_mode,
        route=decision.route,
        executed_system=executed_system,
        reason_code=reason_code,
        confidence=decision.confidence,
        estimated_searches=decision.estimated_searches,
        context_used=used_history > 0,
        history_message_count=history_count,
        history_trimmed=used_history < history_count,
    )


def _safe_reference(item) -> ChatReference:
    return ChatReference(
        evidence_id=item.evidence_id,
        source_type=item.source_type,
        document_id=opaque_identifier(item.document_id),
        title=sanitize_text(item.title),
        excerpt=sanitize_text(item.excerpt) or "[REDACTED]",
        team=sanitize_text(item.team) if item.team else None,
        week=item.week,
    )


def _safe_fast_result(result, policy: PolicyContext):
    owned = [item for item in result.evidence if item.user_id == policy.user_id]
    validator = CitationValidator()
    answer = validator.normalize(sanitize_text(result.answer), owned)
    references: list[ChatReference] = []
    citation_valid = result.quality.citation_valid

    validation = validator.validate(answer, owned, policy)
    requires_validation = bool(
        result.evidence
        or validation.cited_ids
        or result.quality.citation_valid is False
    )
    if requires_validation:
        citation_valid = bool(citation_valid and validation.valid)
        if citation_valid:
            owned_by_id = {item.evidence_id: item for item in owned}
            safe_evidence = [
                sanitize_evidence_for_memory(owned_by_id[evidence_id], policy)
                for evidence_id in validation.cited_ids[:8]
            ]
            references = [_safe_reference(item) for item in safe_evidence]
        else:
            answer = _INVALID_CITATIONS
            safe_evidence = []
    else:
        safe_evidence = []

    disclosures = []
    for disclosure in result.disclosures:
        safe = sanitize_text(disclosure)
        if safe and safe not in disclosures:
            disclosures.append(safe)
    # Preserve the required wording byte-for-byte when fallback occurred.
    if BM25_FALLBACK_DISCLOSURE in result.disclosures:
        disclosures = [item for item in disclosures if item != BM25_FALLBACK_DISCLOSURE]
        disclosures.insert(0, BM25_FALLBACK_DISCLOSURE)

    quality = result.quality.model_copy(
        update={
            "citation_valid": citation_valid,
            "limited_answer": result.quality.limited_answer
            or citation_valid is False,
        }
    )
    return answer, references, quality, disclosures, safe_evidence


async def _load_memory(services, conversation_id, policy, supplied):
    if services.conversations is None:
        if supplied:
            raise AppError(
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "대화 저장소를 사용할 수 없습니다.",
                retryable=True,
            )
        return None, []
    try:
        memory = await services.conversations.load(conversation_id, policy)
    except AppError:
        raise
    except Exception:
        return None, [CONTEXT_UNAVAILABLE_DISCLOSURE]
    if supplied and memory is None:
        raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE, _NOT_FOUND)
    return memory, []


def _with_node_runs(execution: ExecutionMetadata) -> ExecutionMetadata:
    recorder = current_node_recorder()
    if recorder is None:
        return execution
    return execution.model_copy(update={"node_runs": recorder.snapshot()})


def _execution_for(result=None, *, search_count=0, evidence_count=0):
    return _with_node_runs(
        result.execution
        if result is not None and result.execution is not None
        else ExecutionMetadata(
            status="succeeded",
            search_count=search_count,
            evidence_count=evidence_count,
        )
    )


def _should_use_no_evidence_fallback(
    payload: ChatRequest, result: FastRAGResult
) -> bool:
    execution = result.execution
    return bool(
        payload.response_mode == "auto"
        and result.agent_trace is None
        and execution is not None
        and execution.status == "limited"
        and execution.failure_stage == "retrieval"
        and execution.error_code == "NO_EVIDENCE"
        and not result.evidence
    )


async def _save_turn(
    services,
    conversation_id,
    policy,
    payload,
    memory,
    answer,
    route,
    executed_system,
    execution,
    disclosures=None,
    cited_evidence=None,
    trace_id=None,
    reason_code=None,
    quality=None,
    agent_memory=None,
):
    if services.conversations is None:
        return True
    current_memory = memory or ConversationMemory()
    if agent_memory is not None:
        from app.persistence.conversations import apply_agent_memory_update

        current_memory = apply_agent_memory_update(
            current_memory,
            agent_memory,
            policy,
        )
    safe_user = sanitize_text(payload.message) or "[REDACTED]"
    safe_answer = sanitize_text(answer) if answer is not None else None
    prior = list(current_memory.messages)
    prior.append({"role": "user", "content": safe_user})
    if safe_answer:
        prior.append({"role": "assistant", "content": safe_answer})
    turns = list(current_memory.turns)
    turns.append(
        TurnRecord(
            user_content=safe_user,
            assistant_content=safe_answer,
            route=route,
            executed_system=executed_system,
            execution=execution,
            trace_id=trace_id,
            reason_code=reason_code,
            quality=quality,
            disclosures=disclosures or [],
            cited_evidence=cited_evidence or [],
        )
    )
    try:
        await services.conversations.save(
            conversation_id,
            policy,
            current_memory.model_copy(
                update={
                    "messages": prior[-20:],
                    "turns": turns[-20:],
                    "filters": sanitize_filters_for_memory(payload.filters),
                    "cited_evidence": (
                        cited_evidence if cited_evidence is not None else []
                    )[:8],
                }
            ),
        )
    except AppError:
        raise
    except Exception:
        return False
    return True


def _diagnostic_answer(memory) -> str:
    turns = list(getattr(memory, "turns", None) or [])
    prior = next(
        (
            turn
            for turn in reversed(turns)
            if turn.executed_system not in {"diagnostic", "corpus_info"}
        ),
        None,
    )
    if prior is None:
        return "진단할 이전 실행 기록이 없습니다."
    execution = prior.execution
    if execution.status == "succeeded":
        return (
            f"직전 {prior.executed_system} 실행은 성공했습니다. "
            f"검색 {execution.search_count}회, 근거 {execution.evidence_count}개, "
            f"소요 {execution.duration_ms}ms입니다."
        )
    stage = execution.failure_stage or "unknown"
    code = execution.error_code or "UNKNOWN"
    return (
        f"직전 {prior.executed_system} 실행 상태는 {execution.status}입니다. "
        f"중단 단계: {stage}, 오류 코드: {code}, 재시도 가능: "
        f"{'예' if execution.retryable else '아니오'}, 검색 {execution.search_count}회, "
        f"근거 {execution.evidence_count}개, 소요 {execution.duration_ms}ms입니다."
    )


def _corpus_answer(info) -> str:
    teams = ", ".join(f"{name} {count}개" for name, count in info.teams.items()) or "없음"
    mail_types = ", ".join(
        f"{name} {count}개" for name, count in info.mail_types.items()
    ) or "없음"
    weeks = (
        f"{info.first_week} ~ {info.last_week}"
        if info.first_week and info.last_week
        else "확인되지 않음"
    )
    models = ", ".join(info.embedding_models) or "메타데이터 없음"
    recent = ", ".join(info.recent_titles) or "없음"
    return (
        f"현재 사용자에게 검색 가능한 인덱스 문서는 {info.document_count}개입니다.\n"
        f"- 팀: {teams}\n- 기간: {weeks}\n- 메일 유형: {mail_types}\n"
        f"- 임베딩 모델: {models}\n- 최근 문서: {recent}"
    )


@router.post(
    "/v1/chat",
    response_model=ChatResponse,
    description=(
        "The request-body user_id is authoritative. Deployment must place an "
        "authenticating gateway in front of this API and bind its verified "
        "identity to that field. Client-supplied identity headers are ignored."
    ),
)
async def chat(payload: ChatRequest, request: Request):
    services = request.app.state.container
    trace_id = request.state.trace_id
    supplied_id = payload.conversation_id is not None
    conversation_id = payload.conversation_id or uuid.uuid4().hex
    policy = _policy_for(payload)
    memory, context_disclosures = await _load_memory(
        services, conversation_id, policy, supplied=supplied_id
    )
    decision = await services.router.route(payload, memory)

    if decision.route == "diagnostic":
        answer = _diagnostic_answer(memory)
        execution = _execution_for()
        quality = QualityStatus(citation_valid=None, retrieval_mode="not_used")
        saved = await _save_turn(
            services, conversation_id, policy, payload, memory, answer,
            "diagnostic", "diagnostic", execution,
            trace_id=trace_id, reason_code=decision.reason_code, quality=quality,
        )
        if not saved:
            context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
        return ChatResponse(
            conversation_id=conversation_id,
            mode="diagnostic",
            answer=answer,
            quality=quality,
            disclosures=context_disclosures,
            trace_id=trace_id,
            routing=_routing_diagnostics(payload, decision, "diagnostic", memory),
            execution=execution,
        )

    if decision.route == "corpus_info":
        if services.corpus_info is None:
            raise AppError(
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "인덱스 정보를 현재 조회할 수 없습니다.",
                retryable=True,
            )
        info = await services.corpus_info.inspect(policy)
        answer = _corpus_answer(info)
        execution = _execution_for()
        quality = QualityStatus(citation_valid=None, retrieval_mode="not_used")
        saved = await _save_turn(
            services, conversation_id, policy, payload, memory, answer,
            "corpus_info", "corpus_info", execution,
            trace_id=trace_id, reason_code=decision.reason_code, quality=quality,
        )
        if not saved:
            context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
        return ChatResponse(
            conversation_id=conversation_id,
            mode="corpus_info",
            answer=answer,
            quality=quality,
            disclosures=context_disclosures,
            trace_id=trace_id,
            routing=_routing_diagnostics(payload, decision, "corpus_info", memory),
            execution=execution,
        )

    if decision.route == "clarify":
        answer = sanitize_text(
            decision.clarification_question
            or "조회할 팀이나 기간을 구체적으로 알려주세요."
        )
        execution = _execution_for()
        quality = QualityStatus(
            citation_valid=True, limited_answer=False, retrieval_mode="not_used"
        )
        saved = await _save_turn(
            services, conversation_id, policy, payload, memory, answer,
            "clarify", "clarification", execution,
            trace_id=trace_id, reason_code=decision.reason_code, quality=quality,
        )
        if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
            context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
        return ChatResponse(
            conversation_id=conversation_id,
            mode="fast_rag",
            answer=answer,
            quality=quality,
            disclosures=context_disclosures,
            trace_id=trace_id,
            routing=_routing_diagnostics(payload, decision, "clarification", memory),
            execution=execution,
        )

    if decision.route == "general":
        result = await services.fast.respond_general(payload, memory)
        answer, references, quality, disclosures, owned = _safe_fast_result(
            result, policy
        )
        quality = quality.model_copy(update={"retrieval_mode": "not_used"})
        execution = _execution_for(result, evidence_count=len(owned))
        public_answer = None if execution.status == "failed" else answer
        saved = await _save_turn(
            services, conversation_id, policy, payload, memory, public_answer,
            "general", "general", execution, disclosures, owned,
            trace_id=trace_id, reason_code=decision.reason_code, quality=quality,
        )
        if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
            context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
        return ChatResponse(
            conversation_id=conversation_id,
            mode="fast_rag",
            answer=public_answer,
            references=references,
            quality=quality,
            disclosures=[*context_disclosures, *disclosures],
            trace_id=trace_id,
            routing=_routing_diagnostics(payload, decision, "general", memory),
            execution=execution,
        )

    if decision.route == "deep":
        if services.deep is None:
            raise AppError(
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "요청한 서비스를 현재 사용할 수 없습니다.",
                retryable=True,
            )
        deep_started = perf_counter()
        contextualized = await services.router.contextualize_request(payload, memory)
        try:
            deep_result = await services.deep.invoke(
                contextualized.message,
                policy,
                contextualized.filters,
            )
        except AppError as error:
            retrieval_codes = {
                ErrorCode.INDEX_UNAVAILABLE,
                ErrorCode.RETRIEVAL_TIMEOUT,
                ErrorCode.NO_EVIDENCE,
                ErrorCode.EMBEDDING_UNAVAILABLE,
            }
            execution = ExecutionMetadata(
                status="failed",
                failure_stage=(
                    "retrieval" if error.code in retrieval_codes else "generation"
                ),
                error_code=error.code.value,
                retryable=(
                    error.retryable or error.code == ErrorCode.BUDGET_EXCEEDED
                ),
                duration_ms=int((perf_counter() - deep_started) * 1000),
                include_in_llm_history=False,
            )
            execution = _with_node_runs(execution)
            saved = await _save_turn(
                services, conversation_id, policy, payload, memory, None,
                "deep", "deep_research", execution,
                trace_id=trace_id, reason_code=decision.reason_code,
                quality=QualityStatus(
                    citation_valid=None,
                    limited_answer=True,
                    retrieval_mode="not_started",
                ),
            )
            if not saved:
                context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
            return ChatResponse(
                conversation_id=conversation_id,
                mode="deep_research",
                answer=None,
                quality=QualityStatus(
                    citation_valid=None,
                    limited_answer=True,
                    retrieval_mode="not_started",
                ),
                disclosures=context_disclosures,
                trace_id=trace_id,
                routing=_routing_diagnostics(
                    payload, decision, "deep_research", memory
                ),
                execution=execution,
            )
        deep_execution = deep_result.execution or ExecutionMetadata(
            status=("succeeded" if deep_result.citation_valid else "limited"),
            failure_stage=(None if deep_result.citation_valid else "citation_validation"),
            error_code=(None if deep_result.citation_valid else "CITATION_INVALID"),
            search_count=deep_result.rounds,
            evidence_count=len(deep_result.evidence),
            include_in_llm_history=deep_result.citation_valid,
        )
        adapted = FastRAGResult(
            answer=deep_result.report,
            evidence=deep_result.evidence,
            quality=QualityStatus(
                citation_valid=(
                    deep_result.citation_valid if deep_result.evidence else None
                ),
                limited_answer=deep_execution.status != "succeeded",
                retrieval_mode=(
                    "bm25"
                    if BM25_FALLBACK_DISCLOSURE in deep_result.disclosures
                    else "hybrid"
                ),
            ),
            disclosures=deep_result.disclosures,
            execution=deep_execution,
        )
        answer, references, quality, disclosures, owned = _safe_fast_result(
            adapted, policy
        )
        execution = _execution_for(adapted, evidence_count=len(owned))
        public_answer = None if execution.status == "failed" else answer
        saved = await _save_turn(
            services, conversation_id, policy, payload, memory, public_answer,
            "deep", "deep_research", execution, disclosures, owned,
            trace_id=trace_id, reason_code=decision.reason_code, quality=quality,
        )
        if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
            context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
        return ChatResponse(
            conversation_id=conversation_id,
            mode="deep_research",
            answer=public_answer,
            references=references,
            quality=quality,
            disclosures=[*context_disclosures, *disclosures],
            trace_id=trace_id,
            routing=_routing_diagnostics(payload, decision, "deep_research", memory),
            execution=execution,
        )

    result = await services.fast.invoke(payload, policy, memory)
    if _should_use_no_evidence_fallback(payload, result):
        fallback = await services.fast.respond_without_evidence(payload, memory)
        if fallback.execution is not None and fallback.execution.status == "succeeded":
            result = result.model_copy(
                update={
                    "answer": fallback.answer,
                    "disclosures": [
                        *result.disclosures,
                        NO_EVIDENCE_GENERAL_DISCLOSURE,
                    ],
                    "execution": result.execution.model_copy(
                        update={
                            "duration_ms": (
                                result.execution.duration_ms
                                + fallback.execution.duration_ms
                            ),
                            "include_in_llm_history": True,
                        }
                    ),
                }
            )
    answer, references, quality, disclosures, owned = _safe_fast_result(result, policy)
    execution = _execution_for(result, evidence_count=len(owned))
    public_answer = None if execution.status == "failed" else answer
    saved = await _save_turn(
        services, conversation_id, policy, payload, memory, public_answer,
        "fast", "fast_rag", execution, disclosures, owned,
        trace_id=trace_id,
        reason_code=decision.reason_code,
        quality=quality,
        agent_memory=(result.agent_memory if execution.status != "failed" else None),
    )
    if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
        context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
    return ChatResponse(
        conversation_id=conversation_id,
        mode="fast_rag",
        answer=public_answer,
        references=references,
        quality=quality,
        disclosures=[*context_disclosures, *disclosures],
        trace_id=trace_id,
        routing=_routing_diagnostics(payload, decision, "fast_rag", memory),
        execution=execution,
    )
