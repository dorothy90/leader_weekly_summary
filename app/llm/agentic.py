import asyncio
import json
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.agentic import (
    IntentDecision,
    JudgeDecision,
    Observation,
    QueryAnalysis,
    SearchDocument,
    ToolAction,
)
from app.llm.prompts import (
    ANSWER_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
)
from app.security.redaction import sanitize_text


class PlanningDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: ToolAction | None
    reason: str = Field(min_length=1, max_length=500)


class AnswerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    answer: str = Field(min_length=1, max_length=8000)


class AgentAnalyzer(Protocol):
    async def analyze(
        self, question: str, memory: object, timezone_name: str
    ) -> QueryAnalysis: ...

    async def plan(
        self,
        question: str,
        analysis: QueryAnalysis,
        observations: list[Observation],
        memory: object,
    ) -> ToolAction | None: ...

    async def judge(
        self,
        question: str,
        analysis: QueryAnalysis,
        observations: list[Observation],
        documents: list[SearchDocument],
        memory: object,
        iteration_count: int,
    ) -> JudgeDecision: ...

    async def answer(
        self,
        question: str,
        analysis: QueryAnalysis,
        documents: list[SearchDocument],
        missing: list[str],
        memory: object,
    ) -> str: ...


class StructuredAgentModel:
    def __init__(
        self,
        llm,
        *,
        timeout_seconds=150,
        attempts=2,
        now=None,
    ):
        self.llm = llm
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.attempts = max(1, int(attempts))
        self.now = now

    @staticmethod
    def _memory(memory: object) -> dict:
        previous = getattr(memory, "previous_event_reference", None)
        return {
            "entities": dict(
                list((getattr(memory, "entities", {}) or {}).items())[-16:]
            ),
            "current_topic": getattr(memory, "current_topic", None),
            "previous_event_reference": (
                previous.model_dump(mode="json") if previous is not None else None
            ),
        }

    @staticmethod
    def _documents(documents: list[SearchDocument]) -> list[dict]:
        result = []
        for index, item in enumerate(documents[:8], 1):
            result.append(
                {
                    "evidence_id": f"S{index}",
                    "source_type": item.source_type,
                    "content_kind": item.content_kind,
                    "source_id": item.source_id,
                    "parent_event_id": item.parent_event_id,
                    "title": sanitize_text(item.title)[:500],
                    "text": (sanitize_text(item.text) or "[REDACTED]")[:4000],
                    "metadata": item.metadata,
                }
            )
        return result

    @classmethod
    def _observations(cls, observations: list[Observation]) -> list[dict]:
        return [
            {
                "action": item.action.model_dump(mode="json"),
                "result": {
                    "tool": item.result.tool,
                    "query": item.result.query,
                    "total_hits": item.result.total_hits,
                    "error_code": item.result.error_code,
                    "documents": cls._documents(item.result.documents),
                },
            }
            for item in observations[-4:]
        ]

    async def _complete(self, system: str, payload: dict, schema):
        user = json.dumps(payload, ensure_ascii=False)
        last_error = None
        for _attempt in range(self.attempts):
            try:
                result = await asyncio.wait_for(
                    self.llm.complete_model(system, user, schema),
                    timeout=self.timeout_seconds,
                )
                return schema.model_validate(result)
            except Exception as error:
                last_error = error
        raise RuntimeError("agent_model_unavailable") from last_error

    async def analyze(self, question, memory, timezone_name):
        safe_memory = self._memory(memory)
        safe_memory.pop("previous_event_reference", None)
        user = json.dumps(
            {"question": question, "memory": safe_memory},
            ensure_ascii=False,
        )
        for _attempt in range(self.attempts):
            try:
                decision = await asyncio.wait_for(
                    self.llm.complete_model(
                        INTENT_SYSTEM_PROMPT,
                        user,
                        IntentDecision,
                    ),
                    timeout=self.timeout_seconds,
                )
                validated = IntentDecision.model_validate(decision)
                return QueryAnalysis.from_intent(
                    validated,
                    now=self.now,
                    timezone_name=timezone_name,
                )
            except Exception:
                continue
        return QueryAnalysis.unavailable()

    async def plan(self, question, analysis, observations, memory):
        decision = await self._complete(
            PLANNER_SYSTEM_PROMPT,
            {
                "question": question,
                "analysis": analysis.model_dump(mode="json"),
                "observations": self._observations(observations),
                "memory": self._memory(memory),
            },
            PlanningDecision,
        )
        return decision.action

    async def judge(
        self,
        question,
        analysis,
        observations,
        documents,
        memory,
        iteration_count,
    ):
        return await self._complete(
            JUDGE_SYSTEM_PROMPT,
            {
                "question": question,
                "analysis": analysis.model_dump(mode="json"),
                "observations": self._observations(observations),
                "evidence": self._documents(documents),
                "memory": self._memory(memory),
                "iteration_count": iteration_count,
            },
            JudgeDecision,
        )

    async def answer(self, question, analysis, documents, missing, memory):
        decision = await self._complete(
            ANSWER_SYSTEM_PROMPT,
            {
                "question": question,
                "analysis": analysis.model_dump(mode="json"),
                "evidence": self._documents(documents),
                "missing_information": missing,
                "conversation_topic": getattr(memory, "current_topic", None),
            },
            AnswerDecision,
        )
        return decision.answer
