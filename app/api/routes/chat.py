import uuid

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ChatReference,
    ChatRequest,
    ChatResponse,
    ExecutionMetadata,
)
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.observability.node_runs import current_node_recorder
from app.persistence.conversations import (
    ConversationMemory,
    TurnRecord,
    apply_agent_memory_update,
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


def _safe_agent_result(result, policy: PolicyContext):
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
    if BM25_FALLBACK_DISCLOSURE in result.disclosures:
        disclosures = [
            item for item in disclosures if item != BM25_FALLBACK_DISCLOSURE
        ]
        disclosures.insert(0, BM25_FALLBACK_DISCLOSURE)

    quality = result.quality.model_copy(
        update={
            "citation_valid": citation_valid,
            "limited_answer": (
                result.quality.limited_answer or citation_valid is False
            ),
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


def _execution_for(result, *, evidence_count: int) -> ExecutionMetadata:
    execution = result.execution or ExecutionMetadata(
        status="succeeded",
        search_count=len(result.agent_trace.tool_calls) if result.agent_trace else 0,
        evidence_count=evidence_count,
    )
    return _with_node_runs(execution)


async def _save_turn(
    services,
    conversation_id,
    policy,
    payload,
    memory,
    answer,
    execution,
    disclosures=None,
    cited_evidence=None,
    trace_id=None,
    quality=None,
    agent_memory=None,
):
    if services.conversations is None:
        return True
    current_memory = memory or ConversationMemory()
    if agent_memory is not None:
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
            execution=execution,
            trace_id=trace_id,
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
        services,
        conversation_id,
        policy,
        supplied=supplied_id,
    )

    result = await services.agentic.invoke(payload, policy, memory)
    answer, references, quality, disclosures, owned = _safe_agent_result(
        result,
        policy,
    )
    execution = _execution_for(result, evidence_count=len(owned))
    public_answer = None if execution.status == "failed" else answer
    saved = await _save_turn(
        services,
        conversation_id,
        policy,
        payload,
        memory,
        public_answer,
        execution,
        disclosures,
        owned,
        trace_id=trace_id,
        quality=quality,
        agent_memory=(
            result.agent_memory if execution.status != "failed" else None
        ),
    )
    if not saved and CONTEXT_UNAVAILABLE_DISCLOSURE not in context_disclosures:
        context_disclosures.append(CONTEXT_UNAVAILABLE_DISCLOSURE)

    return ChatResponse(
        conversation_id=conversation_id,
        answer=public_answer,
        references=references,
        quality=quality,
        disclosures=[*context_disclosures, *disclosures],
        trace_id=trace_id,
        agent_trace=result.agent_trace,
        execution=execution,
    )
