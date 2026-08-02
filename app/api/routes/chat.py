import uuid

from fastapi import APIRouter, Request, Response, status
from pydantic import ValidationError

from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ChatReference,
    ChatRequest,
    ChatResponse,
    QualityStatus,
    RoutingDiagnostics,
)
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.domain.research import ResearchStatus
from app.persistence.conversations import (
    ConversationMemory,
    sanitize_evidence_for_memory,
    sanitize_filters_for_memory,
)
from app.security.citations import CitationValidator
from app.security.redaction import opaque_identifier, sanitize_text

router = APIRouter()
_NOT_FOUND = "대화를 찾을 수 없습니다."
_INVALID_CITATIONS = "검증된 근거만으로 답변을 제공할 수 없습니다."
CONTEXT_UNAVAILABLE_DISCLOSURE = (
    "대화 저장소를 사용할 수 없어 이번 요청은 단일 턴으로 처리했습니다."
)


def _policy_for(payload: ChatRequest) -> PolicyContext:
    """The body owner is authoritative; an upstream gateway must authenticate it."""
    try:
        return PolicyContext.from_user_id(payload.user_id)
    except ValidationError:
        raise AppError(
            ErrorCode.INVALID_USER_ID,
            "유효하지 않은 사용자 식별자입니다.",
        ) from None


def _research_status(value) -> ResearchStatus:
    try:
        return ResearchStatus(value)
    except (TypeError, ValueError):
        raise AppError(
            ErrorCode.DEPENDENCY_UNAVAILABLE,
            "조사 작업 상태를 확인할 수 없습니다.",
            retryable=True,
        ) from None


def _routing_diagnostics(payload, decision, executed_system):
    return RoutingDiagnostics(
        requested_mode=payload.response_mode,
        route=decision.route,
        executed_system=executed_system,
        reason_code=sanitize_text(decision.reason_code) or "unspecified",
        confidence=decision.confidence,
        estimated_searches=decision.estimated_searches,
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
    answer = sanitize_text(result.answer)
    owned = [item for item in result.evidence if item.user_id == policy.user_id]
    references: list[ChatReference] = []
    citation_valid = result.quality.citation_valid

    validation = CitationValidator().validate(answer, owned, policy)
    requires_validation = bool(
        result.evidence or validation.cited_ids or not result.quality.citation_valid
    )
    if requires_validation:
        citation_valid = citation_valid and validation.valid
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
            "limited_answer": result.quality.limited_answer or not citation_valid,
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


async def _save_messages(
    services,
    conversation_id,
    policy,
    payload,
    memory,
    messages,
    cited_evidence=None,
):
    if services.conversations is None:
        return True
    prior = list(memory.messages if memory else [])
    prior.extend(
        {"role": role, "content": sanitize_text(content) or "[REDACTED]"}
        for role, content in messages
    )
    try:
        await services.conversations.save(
            conversation_id,
            policy,
            ConversationMemory(
                messages=prior[-20:],
                filters=sanitize_filters_for_memory(payload.filters),
                cited_evidence=(cited_evidence if cited_evidence is not None else [])[
                    :8
                ],
            ),
        )
    except AppError:
        raise
    except Exception:
        return False
    return True


@router.post(
    "/v1/chat",
    response_model=ChatResponse,
    description=(
        "The request-body user_id is authoritative. Deployment must place an "
        "authenticating gateway in front of this API and bind its verified "
        "identity to that field. Client-supplied identity headers are ignored."
    ),
)
async def chat(payload: ChatRequest, request: Request, response: Response):
    services = request.app.state.container
    trace_id = request.state.trace_id
    supplied_id = payload.conversation_id is not None
    conversation_id = payload.conversation_id or uuid.uuid4().hex
    policy = _policy_for(payload)
    memory, context_disclosures = await _load_memory(
        services, conversation_id, policy, supplied=supplied_id
    )
    decision = await services.router.route(payload)

    if decision.route == "clarify":
        answer = sanitize_text(
            decision.clarification_question
            or "조회할 팀이나 기간을 구체적으로 알려주세요."
        )
        saved = await _save_messages(
            services,
            conversation_id,
            policy,
            payload,
            memory,
            [("user", payload.message), ("assistant", answer)],
        )
        if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
            context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
        return ChatResponse(
            conversation_id=conversation_id,
            mode="fast_rag",
            answer=answer,
            quality=QualityStatus(
                citation_valid=True,
                limited_answer=False,
                retrieval_mode="not_used",
            ),
            disclosures=context_disclosures,
            trace_id=trace_id,
            routing=_routing_diagnostics(payload, decision, "clarification"),
        )

    if decision.route == "general":
        result = await services.fast.respond_general(payload)
        answer, references, quality, disclosures, owned = _safe_fast_result(
            result, policy
        )
        quality = quality.model_copy(update={"retrieval_mode": "not_used"})
        saved = await _save_messages(
            services,
            conversation_id,
            policy,
            payload,
            memory,
            [("user", payload.message), ("assistant", answer)],
            owned,
        )
        if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
            context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
        return ChatResponse(
            conversation_id=conversation_id,
            mode="fast_rag",
            answer=answer,
            references=references,
            quality=quality,
            disclosures=[*context_disclosures, *disclosures],
            trace_id=trace_id,
            routing=_routing_diagnostics(payload, decision, "general"),
        )

    if decision.route == "deep":
        if services.deep is None:
            raise AppError(
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "요청한 서비스를 현재 사용할 수 없습니다.",
                retryable=True,
            )
        job = await services.deep.enqueue(payload, policy, trace_id)
        saved = await _save_messages(
            services,
            conversation_id,
            policy,
            payload,
            memory,
            [("user", payload.message)],
        )
        if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
            context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
        response.status_code = status.HTTP_202_ACCEPTED
        return ChatResponse(
            conversation_id=conversation_id,
            mode="deep_research",
            trace_id=trace_id,
            routing=_routing_diagnostics(payload, decision, "deep_research"),
            job_id=opaque_identifier(job.job_id),
            status=_research_status(job.status),
            plan_summary=sanitize_text(job.plan_summary),
            disclosures=context_disclosures,
        )

    result = await services.fast.invoke(payload, policy, memory)
    answer, references, quality, disclosures, owned = _safe_fast_result(result, policy)
    saved = await _save_messages(
        services,
        conversation_id,
        policy,
        payload,
        memory,
        [("user", payload.message), ("assistant", answer)],
        owned,
    )
    if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
        context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)
    return ChatResponse(
        conversation_id=conversation_id,
        mode="fast_rag",
        answer=answer,
        references=references,
        quality=quality,
        disclosures=[*context_disclosures, *disclosures],
        trace_id=trace_id,
        routing=_routing_diagnostics(payload, decision, "fast_rag"),
    )
