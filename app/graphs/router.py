from app.domain.chat import ChatRequest, RouteDecision
from app.llm.prompts import ROUTER_SYSTEM
from app.security.redaction import sanitize_text


def _deterministic_fallback(request: ChatRequest) -> RouteDecision:
    text = request.message.casefold()
    requested_output = (
        "presentation"
        if "ppt" in text or "프레젠테이션" in text
        else "report" if "보고서" in text else "answer"
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
    if any(
        term in text for term in ("보고서", "ppt", "추세", "원인과 조치", "종합 분석")
    ):
        return RouteDecision(
            route="deep",
            reason_code="deterministic_research_output",
            confidence=1,
            estimated_searches=4,
            requested_output=requested_output,
        )
    return RouteDecision(
        route="fast",
        reason_code="deterministic_fast",
        confidence=1,
        estimated_searches=1,
        requested_output=requested_output,
    )


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
        return RouteDecision.model_validate(decision)
    except Exception:
        return _deterministic_fallback(request)
