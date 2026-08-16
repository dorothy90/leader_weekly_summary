import asyncio
import json
import re
from typing import Protocol

from pydantic import BaseModel

from app.domain.agentic import (
    JudgeDecision,
    QueryAnalysis,
    ToolAction,
)
from app.retrieval.dates import resolve_time_range


TOOL_ORDER = (
    "search_mail",
    "search_calendar",
    "search_domain_knowledge",
)
SOURCE_FOR_TOOL = {
    "search_mail": "mail",
    "search_calendar": "calendar",
    "expand_calendar_event": "calendar",
    "search_domain_knowledge": "domain_knowledge",
}
NEED_FOR_SOURCE = {
    "mail": "관련 메일",
    "calendar": "관련 회의와 Action",
    "domain_knowledge": "기술적 의미",
}
TIME_EXPRESSIONS = ("지난주", "어제", "이번주", "지난달")


class _AnswerSupportDecision(BaseModel):
    supported: bool


class AgentModel(Protocol):
    async def analyze(self, question, memory, timezone_name) -> QueryAnalysis:
        raise NotImplementedError

    async def plan(
        self, question, analysis, observations, memory
    ) -> ToolAction | None:
        raise NotImplementedError

    async def judge(self, state) -> JudgeDecision:
        raise NotImplementedError

    async def replan(self, state) -> ToolAction | None:
        raise NotImplementedError

    async def answer(
        self, question, analysis, documents, missing
    ) -> str:
        raise NotImplementedError


def _time_expression(question: str) -> str | None:
    return next((item for item in TIME_EXPRESSIONS if item in question), None)


class RuleBasedAgentModel:
    def __init__(self, *, now=None):
        self.now = now

    @staticmethod
    def _query(question, analysis):
        values = [
            analysis.entities[key]
            for key in ("product", "issue", "meeting", "person")
            if key in analysis.entities
        ]
        return " ".join(dict.fromkeys(values)) or question

    @staticmethod
    def _required_sources(analysis):
        sources = []
        for need in analysis.information_needs:
            if "메일" in need and "mail" not in sources:
                sources.append("mail")
            if (
                any(word in need for word in ("회의", "Action", "일정"))
                and "calendar" not in sources
            ):
                sources.append("calendar")
            if (
                any(word in need for word in ("기술", "의미", "원인"))
                and "domain_knowledge" not in sources
            ):
                sources.append("domain_knowledge")
        return sources

    async def analyze(self, question, memory, timezone_name):
        text = question.casefold()
        entities = dict(getattr(memory, "entities", {}) or {})
        if "nand" in text:
            entities["product"] = "NAND"
        if "cell leakage" in text:
            entities["issue"] = "Cell Leakage"
        if "yield review" in text:
            entities["meeting"] = "NAND Yield Review"
        if "김oo" in text:
            entities["person"] = "김OO"

        follow_up = "그 회의" in question
        wants_mail = "메일" in question
        wants_calendar = any(
            term in question for term in ("회의", "일정", "Action", "액션")
        )
        wants_event_detail = any(
            term in question
            for term in (
                "Action",
                "액션",
                "결정",
                "무슨 얘기",
                "회의 내용",
                "첨부",
            )
        )
        wants_domain = any(
            term in question
            for term in (
                "기술적",
                "기술적으로",
                "무슨 의미",
                "뭐야",
                "원인",
            )
        )
        if not any((wants_mail, wants_calendar, wants_domain)):
            wants_domain = True

        needs = []
        if wants_mail:
            needs.append(NEED_FOR_SOURCE["mail"])
        if wants_calendar or follow_up:
            needs.append(
                "관련 회의와 Action"
                if wants_event_detail or follow_up
                else "관련 회의"
            )
        if wants_domain:
            needs.append(NEED_FOR_SOURCE["domain_knowledge"])

        expression = _time_expression(question)
        resolved = resolve_time_range(
            expression, now=self.now, timezone_name=timezone_name
        )
        count = sum((wants_mail, wants_calendar or follow_up, wants_domain))
        if follow_up:
            question_type = "follow_up"
        elif count > 1:
            question_type = "multi_source"
        elif wants_mail:
            question_type = "mail_search"
        elif wants_calendar:
            question_type = "calendar_search"
        else:
            question_type = "domain_knowledge"
        return QueryAnalysis(
            intent="knowledge_query",
            question_type=question_type,
            entities=entities,
            time_expression=expression,
            start_at_utc=resolved.start_at_utc if resolved else None,
            end_at_utc=resolved.end_at_utc if resolved else None,
            information_needs=needs,
        )

    async def plan(self, question, analysis, observations, memory):
        previous = getattr(memory, "previous_event_reference", None)
        if analysis.question_type == "follow_up" and previous and not observations:
            return ToolAction(
                tool="expand_calendar_event",
                event_id=previous.event_id,
                reason="이전 대화의 동일 사용자 회의 참조 우선 확장",
            )
        used = {
            SOURCE_FOR_TOOL[item.action.tool]
            for item in observations
            if item.result.documents
        }
        for tool in TOOL_ORDER:
            source = SOURCE_FOR_TOOL[tool]
            if source in self._required_sources(analysis) and source not in used:
                return ToolAction(
                    tool=tool,
                    query=self._query(question, analysis),
                    reason=f"미확보 정보 source 검색: {source}",
                )
        return None

    async def judge(self, state):
        documents = state.get("documents", [])
        found = {item.source_type for item in documents}
        required = self._required_sources(state["analysis"])
        missing_sources = [source for source in required if source not in found]
        needs_calendar_detail = any(
            any(
                term in need
                for term in ("Action", "액션", "결정", "내용", "첨부")
            )
            for need in state["analysis"].information_needs
        )
        has_calendar_detail = any(
            item.source_type == "calendar"
            and any(
                term in f"{item.title} {item.text}"
                for term in ("Action", "액션", "결정")
            )
            for item in documents
        )
        if (
            "calendar" in required
            and needs_calendar_detail
            and not has_calendar_detail
            and "calendar" not in missing_sources
        ):
            missing_sources.append("calendar")
        missing = [NEED_FOR_SOURCE[source] for source in missing_sources]
        action = None
        if missing and state.get("iteration_count", 0) < 4:
            action = await self.replan(state)
        return JudgeDecision(
            sufficient=not missing,
            reason=(
                "모든 정보 요구 충족" if not missing else "추가 source 필요"
            ),
            missing_information=missing,
            recommended_action=action,
        )

    async def replan(self, state):
        return await self.plan(
            state["request"].message,
            state["analysis"],
            state.get("observations", []),
            state.get("conversation"),
        )

    async def answer(self, question, analysis, documents, missing):
        if not documents:
            return (
                "확인 가능한 검색 근거가 없어 "
                "답변할 수 없습니다."
            )
        labels = {
            "mail": "메일",
            "calendar": "회의",
            "domain_knowledge": "기술적 의미",
        }
        lines = ["확인된 근거입니다."]
        for index, item in enumerate(documents[:8], 1):
            lines.append(f"- {labels[item.source_type]}: {item.text} [S{index}]")
        if missing:
            lines.append("\n확인하지 못한 항목: " + ", ".join(missing))
        return "\n".join(lines)

    async def complete_text(self, system, user):
        return "더미 모드의 안전한 일반 안내입니다."

    async def complete_messages(self, system, messages):
        return "더미 모드의 안전한 일반 안내입니다."


class StructuredAgentModel:
    def __init__(self, llm, *, fallback=None, timeout_seconds=150, now=None):
        self.llm = llm
        self.fallback = fallback or RuleBasedAgentModel(now=now)
        self.timeout_seconds = max(0.001, float(timeout_seconds))
        self.now = now

    async def _structured(self, schema, system, user, fallback_call):
        for _attempt in range(2):
            try:
                value = await asyncio.wait_for(
                    self.llm.complete_model(system, user, schema),
                    timeout=self.timeout_seconds,
                )
                return schema.model_validate(value)
            except Exception:
                continue
        return await fallback_call()

    async def analyze(self, question, memory, timezone_name):
        safe_memory = {
            "entities": getattr(memory, "entities", {}),
            "current_topic": getattr(memory, "current_topic", None),
        }
        analysis = await self._structured(
            QueryAnalysis,
            (
                "질문을 구조화하되 employee_id, index, DSL을 "
                "출력하지 마세요."
            ),
            json.dumps(
                {"question": question, "memory": safe_memory}, ensure_ascii=False
            ),
            lambda: self.fallback.analyze(question, memory, timezone_name),
        )
        expression = _time_expression(question)
        resolved = resolve_time_range(
            expression, now=self.now, timezone_name=timezone_name
        )
        return analysis.model_copy(
            update={
                "time_expression": expression,
                "start_at_utc": resolved.start_at_utc if resolved else None,
                "end_at_utc": resolved.end_at_utc if resolved else None,
            }
        )

    async def plan(self, question, analysis, observations, memory):
        previous = getattr(memory, "previous_event_reference", None)
        if (
            analysis.question_type == "follow_up"
            and previous is not None
            and not observations
        ):
            return ToolAction(
                tool="expand_calendar_event",
                event_id=previous.event_id,
                reason="이전 대화의 동일 사용자 회의 참조 우선 확장",
            )
        fallback = lambda: self.fallback.plan(
            question, analysis, observations, memory
        )
        return await self._structured(
            ToolAction,
            "허용된 네 도구 중 다음 한 동작만 선택하세요.",
            json.dumps(
                {
                    "question": question,
                    "analysis": analysis.model_dump(mode="json"),
                    "observations": [
                        item.model_dump(mode="json") for item in observations
                    ],
                },
                ensure_ascii=False,
            ),
            fallback,
        )

    async def judge(self, state):
        safe = {
            "analysis": state["analysis"].model_dump(mode="json"),
            "sources": [item.source_type for item in state.get("documents", [])],
            "iteration_count": state.get("iteration_count", 0),
        }
        return await self._structured(
            JudgeDecision,
            (
                "정보 요구 충족 여부만 판단하고 숨은 추론은 "
                "출력하지 마세요."
            ),
            json.dumps(safe, ensure_ascii=False),
            lambda: self.fallback.judge(state),
        )

    async def replan(self, state):
        decision = state.get("judge_result")
        if decision and decision.recommended_action:
            return decision.recommended_action
        return await self.fallback.replan(state)

    async def answer(self, question, analysis, documents, missing):
        allowed = {f"S{index}" for index in range(1, len(documents[:8]) + 1)}
        context = [
            {"id": f"S{index}", "source": item.source_type, "text": item.text}
            for index, item in enumerate(documents[:8], 1)
        ]
        try:
            answer = await asyncio.wait_for(
                self.llm.complete_text(
                    (
                        "제공된 근거 ID만 인용하고 근거 없는 "
                        "사실을 만들지 마세요."
                    ),
                    json.dumps(
                        {"question": question, "evidence": context},
                        ensure_ascii=False,
                    ),
                ),
                timeout=self.timeout_seconds,
            )
            cited = set(re.findall(r"\[(S\d+)\]", answer))
            if cited and cited <= allowed:
                async def unsupported():
                    return _AnswerSupportDecision(supported=False)

                support = await self._structured(
                    _AnswerSupportDecision,
                    (
                        "초안의 모든 사실 주장이 제공된 근거에서 "
                        "도출되는지 판단하세요. 인용 표시만으로는 "
                        "지지된 주장이 아닙니다."
                    ),
                    json.dumps(
                        {"draft": answer, "evidence": context},
                        ensure_ascii=False,
                    ),
                    unsupported,
                )
                if support.supported:
                    return answer
        except Exception:
            pass
        return await self.fallback.answer(question, analysis, documents, missing)
