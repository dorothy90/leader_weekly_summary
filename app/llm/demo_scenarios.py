from collections import deque
from datetime import date, datetime
from typing import Iterable, Literal

from app.domain.agentic import IntentDecision, QueryAnalysis, SourceRequest


ScenarioName = Literal[
    "canonical",
    "weekly-calendar",
    "event-action",
    "followup",
]


SCENARIOS: dict[ScenarioName, tuple[IntentDecision, ...]] = {
    "canonical": (
        IntentDecision(
            intent="cross_source_investigation",
            source_requests=[
                SourceRequest(source="mail", query="김OO NAND 수율 Cell Leakage"),
                SourceRequest(source="calendar", query="NAND Yield Review"),
                SourceRequest(
                    source="domain_knowledge", query="NAND Cell Leakage"
                ),
            ],
            entities={
                "person": "김OO",
                "product": "NAND",
                "issue": "Cell Leakage",
            },
            time_scope="previous_week",
            calendar_detail_required=True,
        ),
    ),
    "weekly-calendar": (
        IntentDecision(
            intent="weekly_schedule",
            source_requests=[SourceRequest(source="calendar", query="일정")],
            time_scope="current_week",
        ),
    ),
    "event-action": (
        IntentDecision(
            intent="event_action",
            source_requests=[
                SourceRequest(source="calendar", query="NAND Yield Review")
            ],
            time_scope="exact_date",
            exact_date=date(2026, 8, 7),
            calendar_detail_required=True,
        ),
    ),
    "followup": (
        IntentDecision(
            intent="find_event",
            source_requests=[
                SourceRequest(source="calendar", query="NAND Yield Review")
            ],
        ),
        IntentDecision(
            intent="previous_event_detail",
            source_requests=[
                SourceRequest(source="calendar", query="previous event")
            ],
            event_reference="previous_event",
            calendar_detail_required=True,
        ),
    ),
}


def scenario_decisions(name: ScenarioName) -> tuple[IntentDecision, ...]:
    return SCENARIOS[name]


class StaticIntentAnalyzer:
    def __init__(
        self,
        decisions: Iterable[IntentDecision],
        *,
        now: datetime,
    ):
        self._decisions = deque(
            IntentDecision.model_validate(decision) for decision in decisions
        )
        self.now = now

    async def analyze(self, question, memory, timezone_name):
        del question, memory
        if not self._decisions:
            raise RuntimeError("no static intent decision remains")
        return QueryAnalysis.from_intent(
            self._decisions.popleft(),
            now=self.now,
            timezone_name=timezone_name,
        )


class UnavailableAnalyzer:
    async def analyze(self, question, memory, timezone_name):
        del question, memory, timezone_name
        return QueryAnalysis.unavailable()
