import asyncio
import json
from typing import Protocol

from langchain_core.messages.utils import count_tokens_approximately
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


MAX_CONVERSATION_HISTORY_MESSAGES = 6
MAX_CONVERSATION_HISTORY_TOKENS = 4_000


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
        planner_llm=None,
        judge_llm=None,
        answer_llm=None,
        timeout_seconds=150,
        attempts=2,
        now=None,
    ):
        self.llm = llm
        self.routing_llm = llm
        self.planner_llm = planner_llm or llm
        self.judge_llm = judge_llm or llm
        self.answer_llm = answer_llm or llm
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
    def _history(memory: object) -> list[dict[str, str]]:
        history = []
        for item in (getattr(memory, "messages", None) or []):
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            if role not in {"user", "assistant"}:
                continue
            content = sanitize_text(str(item.get("content", ""))) or "[REDACTED]"
            history.append({"role": role, "content": content})
        history = history[-MAX_CONVERSATION_HISTORY_MESSAGES:]
        while (
            history
            and count_tokens_approximately(history)
            > MAX_CONVERSATION_HISTORY_TOKENS
        ):
            remove_count = (
                2
                if len(history) >= 2
                and history[0]["role"] == "user"
                and history[1]["role"] == "assistant"
                else 1
            )
            del history[:remove_count]
        while history and history[0]["role"] != "user":
            history.pop(0)
        return history

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

    async def _complete(self, llm, system: str, payload: dict, schema):
        user = json.dumps(payload, ensure_ascii=False)
        last_error = None
        for _attempt in range(self.attempts):
            try:
                result = await asyncio.wait_for(
                    llm.complete_model(system, user, schema),
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
            {
                "question": question,
                "conversation_history": self._history(memory),
                "memory": safe_memory,
            },
            ensure_ascii=False,
        )
        for _attempt in range(self.attempts):
            try:
                decision = await asyncio.wait_for(
                    self.routing_llm.complete_model(
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
            self.planner_llm,
            PLANNER_SYSTEM_PROMPT,
            {
                "question": question,
                "conversation_history": self._history(memory),
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
            self.judge_llm,
            JUDGE_SYSTEM_PROMPT,
            {
                "question": question,
                "conversation_history": self._history(memory),
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
            self.answer_llm,
            ANSWER_SYSTEM_PROMPT,
            {
                "question": question,
                "conversation_history": self._history(memory),
                "analysis": analysis.model_dump(mode="json"),
                "evidence": self._documents(documents),
                "missing_information": missing,
                "conversation_topic": getattr(memory, "current_topic", None),
            },
            AnswerDecision,
        )
        return decision.answer
