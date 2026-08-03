from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages

from app.domain.chat import ChatRequest
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.llm.prompts import FOLLOWUP_SYSTEM
from app.security.redaction import sanitize_text

MAX_CONTEXT_MESSAGES = 12
MAX_CONTEXT_TOKENS = 4_000
MAX_REQUEST_CHARS = 4_000
MAX_FALLBACK_SUBJECT_CHARS = 1_500


class ConversationState(MessagesState):
    """Owner-scoped, bounded short-term state shared by every chat branch."""

    request: ChatRequest
    conversation: object | None


def _safe_message(item: Any) -> BaseMessage | None:
    if not isinstance(item, dict):
        return None
    role = item.get("role")
    content = sanitize_text(str(item.get("content", ""))) or "[REDACTED]"
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        return AIMessage(content=content)
    return None


def build_conversation_state(
    request: ChatRequest, conversation: object | None
) -> ConversationState:
    stored = getattr(conversation, "messages", None) or []
    history = [message for item in stored if (message := _safe_message(item))]
    latest = HumanMessage(content=sanitize_text(request.message) or "[REDACTED]")
    accumulated = add_messages([], [*history[-(MAX_CONTEXT_MESSAGES - 1) :], latest])
    bounded = trim_messages(
        accumulated,
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=MAX_CONTEXT_TOKENS,
        start_on="human",
        include_system=False,
        allow_partial=False,
    )
    if not bounded or bounded[-1].content != latest.content:
        bounded = [latest]
    return ConversationState(
        messages=bounded,
        request=request,
        conversation=conversation,
    )


def eligible_prior_evidence(
    conversation: object | None,
    policy: PolicyContext,
    *,
    limit: int = 8,
) -> list[Evidence]:
    """Return verified evidence from successful owner-scoped turns only."""
    selected: dict[tuple[str, str], Evidence] = {}
    turns = getattr(conversation, "turns", None) or []
    for turn in reversed(turns):
        execution = getattr(turn, "execution", None)
        if execution is None or execution.status != "succeeded":
            continue
        for item in getattr(turn, "cited_evidence", None) or []:
            if item.user_id != policy.user_id:
                continue
            selected.setdefault((item.source_type, item.document_id), item)
            if len(selected) >= limit:
                return list(selected.values())
    if not turns:
        for item in getattr(conversation, "cited_evidence", None) or []:
            if item.user_id == policy.user_id:
                selected.setdefault((item.source_type, item.document_id), item)
            if len(selected) >= limit:
                break
    return list(selected.values())


def message_dicts(state: ConversationState) -> list[dict[str, str]]:
    result = []
    for message in state["messages"]:
        role = "assistant" if isinstance(message, AIMessage) else "user"
        result.append({"role": role, "content": str(message.content)})
    return result


def render_messages(messages: list[dict[str, str]]) -> str:
    return "\n".join(f"{item['role']}: {item['content']}" for item in messages)


def _deterministic_contextualized_request(
    request: ChatRequest, conversation: object | None
) -> ChatRequest:
    prior_user = ""
    for item in reversed(getattr(conversation, "messages", None) or []):
        if isinstance(item, dict) and item.get("role") == "user":
            prior_user = sanitize_text(str(item.get("content", "")))
            if prior_user:
                break
    if not prior_user:
        return request

    separator = "\n후속 요청: "
    subject = prior_user[:MAX_FALLBACK_SUBJECT_CHARS]
    current = sanitize_text(request.message) or "[REDACTED]"
    current = current[: MAX_REQUEST_CHARS - len(subject) - len(separator)]
    return request.model_copy(update={"message": f"{subject}{separator}{current}"})


async def contextualize_request(
    llm,
    request: ChatRequest,
    conversation: object | None,
    *,
    system: str = FOLLOWUP_SYSTEM,
) -> ChatRequest:
    if not getattr(conversation, "messages", None):
        return request
    messages = message_dicts(build_conversation_state(request, conversation))
    try:
        if hasattr(llm, "complete_messages"):
            standalone = await llm.complete_messages(system, messages)
        else:
            standalone = await llm.complete_text(system, render_messages(messages))
    except Exception:
        return _deterministic_contextualized_request(request, conversation)
    safe_standalone = sanitize_text(standalone)
    if not safe_standalone:
        return _deterministic_contextualized_request(request, conversation)
    return request.model_copy(update={"message": safe_standalone[:MAX_REQUEST_CHARS]})
