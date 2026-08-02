import re

from app.domain.chat import ChatRequest, RouteDecision
from app.llm.prompts import ROUTER_SYSTEM
from app.security.redaction import sanitize_text


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
_ENGLISH_MAIL_OBJECT = re.compile(r"\b(?:e-?mails?|mails?|messages?)\b")
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
    if _has_mail_retrieval_intent(request):
        return RouteDecision(
            route="fast",
            reason_code="deterministic_fast",
            confidence=1,
            estimated_searches=1,
            requested_output=requested_output,
        )
    return RouteDecision(
        route="general",
        reason_code="deterministic_general",
        confidence=1,
        estimated_searches=0,
        requested_output=requested_output,
    )


def _apply_deterministic_policy(
    request: ChatRequest, decision: RouteDecision
) -> RouteDecision:
    deterministic = _deterministic_fallback(request)
    if deterministic.route == "deep":
        return deterministic
    if decision.route in {"general", "clarify"}:
        return decision.model_copy(update={"estimated_searches": 0})
    return decision


async def route_request(request: ChatRequest, llm) -> RouteDecision:
    if request.response_mode in {"fast", "deep"}:
        return RouteDecision(
            route=request.response_mode,
            reason_code="explicit_mode",
            confidence=1,
            estimated_searches=1 if request.response_mode == "fast" else 6,
        )

    try:
        decision = await llm.complete_model(
            ROUTER_SYSTEM,
            sanitize_text(request.message),
            RouteDecision,
        )
        model_decision = RouteDecision.model_validate(decision)
        model_decision = model_decision.model_copy(
            update={"reason_code": f"model_{model_decision.route}"}
        )
        return _apply_deterministic_policy(request, model_decision)
    except Exception:
        fallback = _deterministic_fallback(request)
        normalized = _apply_deterministic_policy(request, fallback)
        return normalized.model_copy(
            update={"reason_code": f"router_error_{normalized.reason_code}"}
        )
