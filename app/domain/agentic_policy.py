from app.domain.agentic import (
    JudgeDecision,
    Observation,
    QueryAnalysis,
    SearchDocument,
    SourceName,
    ToolAction,
    ToolName,
    normalize_stable_event_id,
)
from app.security.redaction import sanitize_text


SOURCE_TO_TOOL: dict[SourceName, ToolName] = {
    "mail": "search_mail",
    "calendar": "search_calendar",
    "domain_knowledge": "search_domain_knowledge",
}
TOOL_TO_SOURCE: dict[ToolName, SourceName] = {
    tool: source for source, tool in SOURCE_TO_TOOL.items()
}
SOURCE_LABELS: dict[SourceName, str] = {
    "mail": "관련 메일",
    "calendar": "관련 회의",
    "domain_knowledge": "기술적 의미",
}
ANSWER_SOURCE_LABELS: dict[SourceName, str] = {
    "mail": "메일",
    "calendar": "회의",
    "domain_knowledge": "기술적 의미",
}
CALENDAR_DETAIL_LABEL = "관련 회의 상세 내용"
MAX_ITERATIONS = 4


class TypedAgentPolicy:
    @staticmethod
    def required_sources(analysis: QueryAnalysis) -> list[SourceName]:
        if analysis.analysis_status != "ready":
            return []
        return [item.source for item in analysis.source_requests]

    @staticmethod
    def next_action(
        analysis: QueryAnalysis,
        observations: list[Observation],
        memory,
    ) -> ToolAction | None:
        if analysis.analysis_status != "ready":
            return None
        if analysis.event_reference == "previous_event" and not observations:
            previous = getattr(memory, "previous_event_reference", None)
            if previous is None:
                return None
            return ToolAction(
                tool="expand_calendar_event",
                event_id=previous.event_id,
                reason="validated previous event reference",
            )
        attempted = {
            TOOL_TO_SOURCE[item.action.tool]
            for item in observations
            if item.action.tool != "expand_calendar_event"
        }
        for request in analysis.source_requests:
            if request.source not in attempted:
                return ToolAction(
                    tool=SOURCE_TO_TOOL[request.source],
                    query=request.query,
                    reason=f"typed source request: {request.source}",
                )
        return None

    @staticmethod
    def missing_information(
        analysis: QueryAnalysis,
        documents: list[SearchDocument],
    ) -> list[str]:
        if analysis.analysis_status != "ready":
            return []
        found_sources = {item.source_type for item in documents}
        missing = [
            SOURCE_LABELS[request.source]
            for request in analysis.source_requests
            if request.source not in found_sources
        ]
        has_calendar_detail = any(
            item.source_type == "calendar"
            and item.content_kind == "attachment"
            and normalize_stable_event_id(item.parent_event_id) is not None
            for item in documents
        )
        if analysis.calendar_detail_required and not has_calendar_detail:
            missing.append(CALENDAR_DETAIL_LABEL)
        return list(dict.fromkeys(missing))

    def judge(
        self,
        analysis: QueryAnalysis,
        observations: list[Observation],
        documents: list[SearchDocument],
        memory,
        iteration_count: int,
    ) -> JudgeDecision:
        missing = self.missing_information(analysis, documents)
        action = None
        if missing and iteration_count < MAX_ITERATIONS:
            action = self.next_action(analysis, observations, memory)
        return JudgeDecision(
            sufficient=not missing,
            reason=(
                "all typed information requirements satisfied"
                if not missing
                else "typed information requirements remain"
            ),
            missing_information=missing,
            recommended_action=action,
        )

    @staticmethod
    def answer(documents: list[SearchDocument], missing: list[str]) -> str:
        if not documents:
            return "확인 가능한 검색 근거가 없어 답변할 수 없습니다."
        lines = ["확인된 근거입니다."]
        for index, item in enumerate(documents[:8], 1):
            text = sanitize_text(item.text) or "[REDACTED]"
            lines.append(
                f"- {ANSWER_SOURCE_LABELS[item.source_type]}: {text} [S{index}]"
            )
        safe_missing = [
            safe
            for item in missing
            if (safe := sanitize_text(str(item)))
        ]
        if safe_missing:
            lines.append(
                "\n확인하지 못한 항목: " + ", ".join(safe_missing)
            )
        return "\n".join(lines)
