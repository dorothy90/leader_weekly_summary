import asyncio
import json
import re
from typing import Protocol

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
SAME_EVENT_REFERENCES = (
    "그 회의",
    "해당 회의",
    "이 회의",
    "그 미팅",
    "해당 미팅",
    "이 미팅",
    "그 일정",
    "해당 일정",
    "이 일정",
)
ISO_DATE = re.compile(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)")


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
    relative = next(
        (item for item in TIME_EXPRESSIONS if item in question),
        None,
    )
    if relative is not None:
        return relative
    explicit = ISO_DATE.search(question)
    return explicit.group(1) if explicit is not None else None


def _saved_event_action(question, analysis, observations, memory):
    previous = getattr(memory, "previous_event_reference", None)
    refers_to_saved_event = any(
        phrase in question for phrase in SAME_EVENT_REFERENCES
    )
    if (
        previous is not None
        and not observations
        and (
            refers_to_saved_event
            or analysis.question_type == "follow_up"
        )
    ):
        return ToolAction(
            tool="expand_calendar_event",
            event_id=previous.event_id,
            reason="이전 대화의 동일 사용자 회의 참조 우선 확장",
        )
    return None


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
        source_for_question_type = {
            "mail_search": "mail",
            "calendar_search": "calendar",
            "follow_up": "calendar",
            "domain_knowledge": "domain_knowledge",
        }
        required = source_for_question_type.get(analysis.question_type)
        sources = [required] if required else []
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

    @classmethod
    def deterministic_missing(cls, analysis, documents, question=""):
        found = {item.source_type for item in documents}
        required = cls._required_sources(analysis)
        missing_sources = [source for source in required if source not in found]
        detail_terms = ("action", "액션", "결정", "내용", "첨부")
        needs_calendar_detail = any(
            any(term in need.casefold() for term in detail_terms)
            for need in [*analysis.information_needs, question]
        )
        has_calendar_detail = any(
            item.source_type == "calendar"
            and any(
                term in f"{item.title} {item.text}".casefold()
                for term in ("action", "액션", "결정")
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
        return [NEED_FOR_SOURCE[source] for source in missing_sources]

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

        follow_up = any(phrase in question for phrase in SAME_EVENT_REFERENCES)
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
            entities=dict(list(entities.items())[-16:]),
            time_expression=expression,
            start_at_utc=resolved.start_at_utc if resolved else None,
            end_at_utc=resolved.end_at_utc if resolved else None,
            information_needs=needs,
        )

    async def plan(self, question, analysis, observations, memory):
        saved_event_action = _saved_event_action(
            question, analysis, observations, memory
        )
        if saved_event_action is not None:
            return saved_event_action
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
        request = state.get("request")
        missing = self.deterministic_missing(
            state["analysis"],
            documents,
            getattr(request, "message", ""),
        )
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

    @staticmethod
    def _merged_question_type(baseline, information_needs):
        probe = baseline.model_copy(
            update={
                "question_type": "general_chat",
                "information_needs": information_needs,
            }
        )
        sources = RuleBasedAgentModel._required_sources(probe)
        for source in RuleBasedAgentModel._required_sources(baseline):
            if source not in sources:
                sources.append(source)
        if baseline.question_type == "follow_up":
            return "follow_up"
        if len(sources) > 1:
            return "multi_source"
        if sources:
            return {
                "mail": "mail_search",
                "calendar": "calendar_search",
                "domain_knowledge": "domain_knowledge",
            }[sources[0]]
        return baseline.question_type

    async def analyze(self, question, memory, timezone_name):
        baseline = await RuleBasedAgentModel(now=self.now).analyze(
            question,
            memory,
            timezone_name,
        )
        safe_memory = {
            "entities": getattr(memory, "entities", {}),
            "current_topic": getattr(memory, "current_topic", None),
        }

        async def baseline_fallback():
            return baseline

        analysis = await self._structured(
            QueryAnalysis,
            (
                "질문을 구조화하되 employee_id, index, DSL을 "
                "출력하지 마세요."
            ),
            json.dumps(
                {"question": question, "memory": safe_memory}, ensure_ascii=False
            ),
            baseline_fallback,
        )
        information_needs = list(
            dict.fromkeys(
                [
                    *baseline.information_needs,
                    *analysis.information_needs,
                ]
            )
        )[:8]
        question_type = self._merged_question_type(
            baseline,
            information_needs,
        )
        merged_entities = {
            **analysis.entities,
            **baseline.entities,
        }
        return QueryAnalysis.model_validate(
            {
                **analysis.model_dump(),
                "question_type": question_type,
                "entities": dict(list(merged_entities.items())[-16:]),
                "information_needs": information_needs,
                "time_expression": baseline.time_expression,
                "start_at_utc": baseline.start_at_utc,
                "end_at_utc": baseline.end_at_utc,
            }
        )

    async def plan(self, question, analysis, observations, memory):
        saved_event_action = _saved_event_action(
            question, analysis, observations, memory
        )
        if saved_event_action is not None:
            return saved_event_action
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
        # A model cannot independently verify its own factual claims. Keep the
        # final answer extractive until an external verifier is available.
        return await self.fallback.answer(question, analysis, documents, missing)
