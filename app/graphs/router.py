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


def _apply_deterministic_policy(
    request: ChatRequest, decision: RouteDecision
) -> RouteDecision:
    deterministic = _deterministic_fallback(request)
    if deterministic.route == "deep":
        return deterministic
    text = request.message.casefold().strip()
    non_mail = text in {
        "안녕",
        "안녕하세요",
        "hello",
        "hi",
        "도움말",
        "사용법",
    }
    if non_mail:
        return decision.model_copy(
            update={
                "route": "general",
                "reason_code": "deterministic_general",
                "estimated_searches": 0,
            }
        )
    if decision.route == "general":
        return decision.model_copy(
            update={
                "route": "fast",
                "reason_code": "deterministic_mail",
                "estimated_searches": max(1, decision.estimated_searches),
            }
        )
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
        return _apply_deterministic_policy(
            request, RouteDecision.model_validate(decision)
        )
    except Exception:
        fallback = _deterministic_fallback(request)
        normalized = _apply_deterministic_policy(request, fallback)
        return normalized.model_copy(
            update={"reason_code": f"router_error_{normalized.reason_code}"}
        )
