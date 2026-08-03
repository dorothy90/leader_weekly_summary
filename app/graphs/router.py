import asyncio
import re

from app.domain.chat import ChatRequest, RouteDecision
from app.graphs.conversation import (
    build_conversation_state,
    message_dicts,
    render_messages,
)
from app.graphs.general_intents import (
    declared_name,
    is_identity_question,
    is_name_recall_question,
)
from app.llm.prompts import ROUTER_SYSTEM
from app.security.redaction import sanitize_text
from app.observability.node_runs import record_node


_MAIL_OBJECTS = (
    "메일",
    "이메일",
    "발신자",
    "수신자",
    "보낸 사람",
    "받은 사람",
    "수율",
    "주간 보고",
    "이슈",
    "현황",
)
_RETRIEVAL_ACTIONS = (
    "찾아",
    "검색",
    "알려",
    "보여",
    "요약",
    "비교",
    "분석",
    "최근",
    "지난주",
    "이번주",
)
_ENGLISH_MAIL_OBJECT = re.compile(r"\b(?:e-?mails?|mails?)\b")
_ENGLISH_REQUEST_PREFIX = (
    r"^\s*(?:(?:please|kindly)\s+|(?:can|could|would|will)\s+you\s+){0,2}"
)
_ENGLISH_RETRIEVAL_ACTION = re.compile(
    _ENGLISH_REQUEST_PREFIX
    + r"(?:find|search|summari[sz]e|show|list|retrieve|look\s+up)\b"
)
_KOREAN_ARTIFACT_REQUEST = re.compile(
    r"(?:보고서|리포트|ppt|프레젠테이션)(?:를|을|로)?\s*"
    r"(?:"
    r"(?:작성|제작|생성|준비)(?:해\s*줘|해주세요|해|하여\s*줘|부탁해|요청해)?"
    r"|만들(?:어\s*줘|어주세요|어줘)"
    r")\s*[.!?]?\s*$",
    re.IGNORECASE,
)
_KOREAN_ANALYSIS_REQUEST = re.compile(
    r"(?:추세|동향|원인(?:과|및)?\s*조치|종합).{0,24}?분석\s*"
    r"(?:해\s*줘|해주세요|해|하여\s*줘|부탁해|요청해)\s*[.!?]?\s*$"
)
_ENGLISH_ARTIFACT_REQUEST = re.compile(
    _ENGLISH_REQUEST_PREFIX + r"(?:create|make|draft|generate|prepare|build)\b.{0,32}?"
    r"\b(?:reports?|presentations?|ppt|slide\s+decks?)\b",
    re.IGNORECASE,
)
_ENGLISH_ANALYSIS_REQUEST = re.compile(
    _ENGLISH_REQUEST_PREFIX + r"(?:"
    r"(?:analy[sz]e|research|investigate)\b.{0,40}?"
    r"\b(?:trends?|root\s+causes?|corrective\s+actions?)\b"
    r"|(?:perform|conduct|create)\b.{0,24}?"
    r"\b(?:trend|root\s+cause|comprehensive)\s+analysis\b"
    r")",
    re.IGNORECASE,
)

_DIAGNOSTIC_PATTERNS = (
    re.compile(r"왜\s*(?:답변|검색|요청).{0,12}(?:못|실패|안\s*됐|안\s*되)"),
    re.compile(r"(?:뭐|무엇|어디)가\s*문제"),
    re.compile(r"(?:직전|이전)\s*요청\s*(?:상태|결과|오류)"),
    re.compile(r"\bwhy\b.{0,24}\b(?:request|answer|search)\b.{0,24}\b(?:fail|failed|failure|stop|stopped)\b"),
    re.compile(r"\b(?:previous|last)\s+(?:request|run)\s+(?:status|error|failure)\b"),
)
_CORPUS_PATTERNS = (
    re.compile(r"(?:뭐|무엇|어떤\s*(?:자료|내용|문서|메일)).{0,12}임베딩"),
    re.compile(r"임베딩.{0,12}(?:뭐|무엇|어떤\s*(?:자료|내용|문서|메일))"),
    re.compile(r"인덱스에.{0,16}(?:자료|내용|문서|메일)"),
    re.compile(r"검색\s*가능한.{0,12}(?:자료|내용|문서|메일)"),
    re.compile(r"\bwhat(?:'s| is)?\s+(?:embedded|indexed|searchable)\b"),
    re.compile(r"\bwhat\s+(?:documents|emails|content)\s+(?:are|is)\s+(?:embedded|indexed|searchable)\b"),
)
_CONVERSATIONAL_PATTERNS = (
    re.compile(r"^(?:안녕(?:하세요)?|하이|hello|hi|hey)[.!?~]*$"),
    re.compile(r"^(?:고마워|감사(?:해|합니다|해요)?|thanks|thank\s+you)[.!?~]*$"),
    re.compile(r"^(?:오늘\s*)?(?:기분|컨디션)(?:이)?\s*(?:어때|어떠니)[.!?~]*$"),
    re.compile(r"^(?:잘\s*지내|뭐\s*해|how\s+are\s+you)[.!?~]*$"),
    re.compile(r"^(?:다시\s*말해(?:\s*줘|주세요)?|반복해(?:\s*줘|주세요)?)[.!?~]*$"),
    re.compile(r"\bmy\s+name\s+is\b"),
    re.compile(r"(?:무슨|뭐|어떤)\s*일(?:을)?\s*할\s*수\s*있"),
    re.compile(r"(?:fast|deep).{0,20}(?:차이|기능|사용)"),
    re.compile(r"(?:차이|기능|사용).{0,20}(?:fast|deep)"),
    re.compile(r"(?:추세\s*분석|검색).{0,12}기능.{0,12}설명"),
    re.compile(r"\bwhat\s+can\s+you\s+do\b"),
    re.compile(r"\b(?:how\s+to\s+use|explain\s+how\s+to\s+search)\b"),
)


def _system_intent(text: str) -> RouteDecision | None:
    normalized = text.casefold()
    if any(pattern.search(normalized) for pattern in _DIAGNOSTIC_PATTERNS):
        return RouteDecision(
            route="diagnostic",
            reason_code="deterministic_diagnostic",
            confidence=1,
            estimated_searches=0,
        )
    if any(pattern.search(normalized) for pattern in _CORPUS_PATTERNS):
        return RouteDecision(
            route="corpus_info",
            reason_code="deterministic_corpus_info",
            confidence=1,
            estimated_searches=0,
        )
    return None


def _has_mail_retrieval_intent(request: ChatRequest) -> bool:
    if request.filters.teams or request.filters.weeks:
        return True
    text = request.message.casefold()
    korean_intent = any(term in text for term in _MAIL_OBJECTS) and any(
        term in text for term in _RETRIEVAL_ACTIONS
    )
    english_intent = bool(
        _ENGLISH_MAIL_OBJECT.search(text) and _ENGLISH_RETRIEVAL_ACTION.search(text)
    )
    return korean_intent or english_intent


def _is_conversational_intent(text: str) -> bool:
    normalized = text.casefold().strip()
    if (
        is_identity_question(normalized)
        or is_name_recall_question(normalized)
        or declared_name(normalized) is not None
    ):
        return True
    return any(pattern.search(normalized) for pattern in _CONVERSATIONAL_PATTERNS)


def _has_research_output_intent(text: str) -> bool:
    return any(
        pattern.search(text)
        for pattern in (
            _KOREAN_ARTIFACT_REQUEST,
            _KOREAN_ANALYSIS_REQUEST,
            _ENGLISH_ARTIFACT_REQUEST,
            _ENGLISH_ANALYSIS_REQUEST,
        )
    )


def _deterministic_fallback(request: ChatRequest) -> RouteDecision:
    text = request.message.casefold()
    requested_output = (
        "presentation"
        if "ppt" in text
        or "프레젠테이션" in text
        or re.search(r"\bpresentations?\b", text)
        else (
            "report"
            if "보고서" in text or re.search(r"\breports?\b", text)
            else "answer"
        )
    )
    if len(request.filters.weeks) >= 4:
        return RouteDecision(
            route="deep",
            reason_code="deterministic_long_period",
            confidence=1,
            estimated_searches=4,
            requested_output=requested_output,
        )
    if len(request.filters.teams) >= 3:
        return RouteDecision(
            route="deep",
            reason_code="deterministic_multi_team",
            confidence=1,
            estimated_searches=3,
            requested_output=requested_output,
        )
    if _has_research_output_intent(text):
        return RouteDecision(
            route="deep",
            reason_code="deterministic_research_output",
            confidence=1,
            estimated_searches=4,
            requested_output=requested_output,
        )
    if _is_conversational_intent(text):
        return RouteDecision(
            route="general",
            reason_code="deterministic_general",
            confidence=1,
            estimated_searches=0,
            requested_output=requested_output,
        )
    return RouteDecision(
        route="fast",
        reason_code="deterministic_fast",
        confidence=1,
        estimated_searches=1,
        requested_output=requested_output,
    )


def _deterministic_fallback_with_history(
    request: ChatRequest, conversation: object | None
) -> RouteDecision:
    turns = list(getattr(conversation, "turns", None) or [])
    latest_success = next(
        (
            turn
            for turn in reversed(turns)
            if turn.execution.status == "succeeded"
            and turn.execution.include_in_llm_history
        ),
        None,
    )
    if latest_success is not None and latest_success.executed_system == "clarification":
        combined = request.model_copy(
            update={
                "message": f"{latest_success.user_content}\n추가 범위: {request.message}"
            }
        )
        return _deterministic_fallback(combined)
    return _deterministic_fallback(request)


def _apply_deterministic_policy(
    request: ChatRequest, decision: RouteDecision
) -> RouteDecision:
    deterministic = _deterministic_fallback(request)
    if deterministic.route == "deep":
        return deterministic
    if deterministic.route == "general":
        if decision.route == "general":
            return decision.model_copy(update={"estimated_searches": 0})
        return deterministic
    if decision.route in {"general", "clarify"}:
        return deterministic
    return decision


async def route_request(
    request: ChatRequest,
    llm,
    conversation: object | None = None,
    timeout_seconds: float = 150.0,
) -> RouteDecision:
    history = len(getattr(conversation, "messages", None) or [])
    async with record_node(
        "router.route",
        input_metrics={"history_messages": min(20, history)},
    ) as run:
        decision = await _route_request(
            request,
            llm,
            conversation,
            timeout_seconds,
        )
        run.update(
            output_metrics={"task_count": decision.estimated_searches}
        )
        return decision


async def _route_request(
    request: ChatRequest,
    llm,
    conversation: object | None,
    timeout_seconds: float,
) -> RouteDecision:
    if request.response_mode in {"fast", "deep"}:
        return RouteDecision(
            route=request.response_mode,
            reason_code="explicit_mode",
            confidence=1,
            estimated_searches=1 if request.response_mode == "fast" else 6,
        )

    system_intent = _system_intent(request.message)
    if system_intent is not None:
        return system_intent

    try:
        messages = message_dicts(build_conversation_state(request, conversation))
        async with record_node(
            "router.model",
            input_metrics={"history_messages": min(20, len(messages) - 1)},
        ):
            async with asyncio.timeout(timeout_seconds):
                if conversation is not None and hasattr(
                    llm, "complete_messages_model"
                ):
                    decision = await llm.complete_messages_model(
                        ROUTER_SYSTEM, messages, RouteDecision
                    )
                else:
                    user = (
                        render_messages(messages)
                        if conversation is not None
                        else sanitize_text(request.message)
                    )
                    decision = await llm.complete_model(
                        ROUTER_SYSTEM, user, RouteDecision
                    )
        model_decision = RouteDecision.model_validate(decision)
        model_decision = model_decision.model_copy(
            update={"reason_code": f"model_{model_decision.route}"}
        )
        return _apply_deterministic_policy(request, model_decision)
    except Exception:
        fallback = _deterministic_fallback_with_history(request, conversation)
        normalized = _apply_deterministic_policy(request, fallback)
        return normalized.model_copy(
            update={"reason_code": f"router_error_{normalized.reason_code}"}
        )
