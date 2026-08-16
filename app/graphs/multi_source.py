from datetime import datetime
from hashlib import sha256
import re
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from app.config.settings import Settings
from app.domain.agentic import (
    AgentMemoryUpdate,
    AgentTrace,
    EventReference,
    JudgeDecision,
    Observation,
    QueryAnalysis,
    SearchDocument,
    SearchResult,
    ToolAction,
)
from app.domain.chat import (
    ChatRequest,
    ExecutionMetadata,
    FastRAGResult,
    QualityStatus,
)
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.llm.agentic import AgentModel, RuleBasedAgentModel
from app.persistence.conversations import ConversationMemory
from app.retrieval.multi_source import MultiSourceSearch
from app.security.citations import CitationValidator
from app.security.redaction import sanitize_text


MAX_ITERATIONS = 4
MAX_EVIDENCE = 8
LIMIT_DISCLOSURE = "검색 한계 내에서 확인된 범위만 답변했습니다."


class MultiSourceState(TypedDict, total=False):
    request: ChatRequest
    policy: PolicyContext
    conversation: ConversationMemory
    analysis: QueryAnalysis
    candidates: list[str]
    current_action: ToolAction | None
    current_result: SearchResult | None
    observations: list[Observation]
    documents: list[SearchDocument]
    fingerprints: list[str]
    judge_result: JudgeDecision
    iteration_count: int
    force_finish: bool
    answer: str
    evidence: list[Evidence]
    citation_valid: bool | None
    limited_answer: bool
    memory_update: AgentMemoryUpdate
    disclosures: list[str]
    retrieval_mode: Literal["hybrid", "bm25"]
    trace: AgentTrace
    execution: ExecutionMetadata


class MultiSourceAgenticWorkflow:
    def __init__(
        self,
        search: MultiSourceSearch,
        model: AgentModel,
        *,
        timezone_name: str | None = None,
    ):
        self.search = search
        self.model = model
        self.timezone_name = (
            timezone_name or Settings().default_user_timezone
        )
        self.validator = CitationValidator()
        self.graph = self._build()

    def _build(self):
        graph = StateGraph(MultiSourceState)
        graph.add_node("query_analyzer", self._query_analyzer)
        graph.add_node("source_discovery", self._source_discovery)
        graph.add_node("planner", self._planner)
        graph.add_node("tool_executor", self._tool_executor)
        graph.add_node("observation", self._observation)
        graph.add_node("judge", self._judge)
        graph.add_node("replanner", self._replanner)
        graph.add_node("final_answer", self._final_answer)
        graph.add_node("save_memory", self._save_memory)
        graph.add_edge(START, "query_analyzer")
        graph.add_edge("query_analyzer", "source_discovery")
        graph.add_edge("source_discovery", "planner")
        graph.add_edge("planner", "tool_executor")
        graph.add_edge("tool_executor", "observation")
        graph.add_edge("observation", "judge")
        graph.add_conditional_edges(
            "judge",
            self._after_judge,
            {"continue": "replanner", "finish": "final_answer"},
        )
        graph.add_edge("replanner", "tool_executor")
        graph.add_edge("final_answer", "save_memory")
        graph.add_edge("save_memory", END)
        return graph.compile()

    async def _query_analyzer(self, state: MultiSourceState) -> dict:
        analysis = await self.model.analyze(
            state["request"].message,
            state["conversation"],
            self.timezone_name,
        )
        return {"analysis": QueryAnalysis.model_validate(analysis)}

    async def _source_discovery(self, state: MultiSourceState) -> dict:
        candidates = RuleBasedAgentModel._required_sources(state["analysis"])
        return {"candidates": candidates}

    async def _planner(self, state: MultiSourceState) -> dict:
        action = await self.model.plan(
            state["request"].message,
            state["analysis"],
            state.get("observations", []),
            state["conversation"],
        )
        return {
            "current_action": (
                ToolAction.model_validate(action) if action is not None else None
            )
        }

    async def _tool_executor(self, state: MultiSourceState) -> dict:
        raw_action = state.get("current_action")
        trace = AgentTrace.model_validate(state.get("trace") or {})
        if raw_action is None:
            return {
                "current_result": None,
                "force_finish": True,
                "trace": trace.model_copy(
                    update={
                        "judge_decisions": [
                            *trace.judge_decisions,
                            "no_action",
                        ][-8:]
                    }
                ),
            }

        action = ToolAction.model_validate(raw_action)
        analysis = QueryAnalysis.model_validate(state["analysis"])
        fingerprints = set(state.get("fingerprints", []))
        fingerprint = action.fingerprint(analysis)
        if fingerprint in fingerprints:
            return {
                "current_result": SearchResult(
                    tool=action.tool,
                    query=action.query,
                    error_code="DUPLICATE_SEARCH",
                ),
                "force_finish": True,
                "trace": trace.model_copy(
                    update={
                        "judge_decisions": [
                            *trace.judge_decisions,
                            "duplicate_search_blocked",
                        ][-8:]
                    }
                ),
            }

        result = SearchResult.model_validate(
            await self.search.execute(action, state["policy"], analysis)
        )
        fingerprints.add(fingerprint)
        semantic_increment = 0 if action.tool == "expand_calendar_event" else 1
        iteration_count = state.get("iteration_count", 0) + semantic_increment
        tool_calls = [*trace.tool_calls, action.tool]

        needs_detail = any(
            any(term in need for term in ("Action", "결정", "내용", "첨부"))
            for need in analysis.information_needs
        )
        documents = list(result.documents)
        if (
            action.tool == "search_calendar"
            and needs_detail
            and len(tool_calls) < MAX_ITERATIONS
        ):
            event_ids = list(
                dict.fromkeys(
                    item.parent_event_id or item.document_id
                    for item in documents
                    if item.content_kind in {"event", "attachment"}
                )
            )
            for event_id in event_ids[:1]:
                expansion = ToolAction(
                    tool="expand_calendar_event",
                    event_id=event_id,
                    reason="회의 내용 요구에 따른 deterministic 확장",
                )
                expansion_fingerprint = expansion.fingerprint(analysis)
                if expansion_fingerprint in fingerprints:
                    continue
                expanded = SearchResult.model_validate(
                    await self.search.execute(
                        expansion, state["policy"], analysis
                    )
                )
                fingerprints.add(expansion_fingerprint)
                tool_calls.append(expansion.tool)
                documents.extend(expanded.documents)
                deduplicated = self._deduplicate_documents(documents)
                result = result.model_copy(
                    update={
                        "documents": deduplicated,
                        "total_hits": len(deduplicated),
                        "disclosures": list(
                            dict.fromkeys(
                                [*result.disclosures, *expanded.disclosures]
                            )
                        )[:4],
                        "retrieval_mode": (
                            "bm25"
                            if "bm25"
                            in {result.retrieval_mode, expanded.retrieval_mode}
                            else result.retrieval_mode
                        ),
                    }
                )

        return {
            "current_result": result,
            "fingerprints": sorted(fingerprints),
            "iteration_count": min(iteration_count, MAX_ITERATIONS),
            "force_finish": (
                iteration_count >= MAX_ITERATIONS
                or len(tool_calls) >= MAX_ITERATIONS
            ),
            "trace": trace.model_copy(
                update={
                    "tool_calls": tool_calls[-8:],
                    "iteration_count": min(iteration_count, MAX_ITERATIONS),
                }
            ),
        }

    async def _observation(self, state: MultiSourceState) -> dict:
        action = state.get("current_action")
        result = state.get("current_result")
        if action is None or result is None:
            return {}
        observation = Observation(action=action, result=result)
        documents = self._deduplicate_documents(
            [*state.get("documents", []), *result.documents]
        )
        disclosures = []
        for raw in [*state.get("disclosures", []), *result.disclosures]:
            safe = sanitize_text(str(raw))[:1000]
            if safe and safe not in disclosures:
                disclosures.append(safe)
        return {
            "observations": [*state.get("observations", []), observation],
            "documents": documents,
            "disclosures": disclosures[:4],
            "retrieval_mode": (
                "bm25"
                if "bm25"
                in {state.get("retrieval_mode", "hybrid"), result.retrieval_mode}
                else "hybrid"
            ),
        }

    async def _judge(self, state: MultiSourceState) -> dict:
        decision = JudgeDecision.model_validate(await self.model.judge(state))
        trace = AgentTrace.model_validate(state.get("trace") or {})
        safe_reason = sanitize_text(decision.reason)[:500] or "judge_result_redacted"
        return {
            "judge_result": decision,
            "trace": trace.model_copy(
                update={
                    "judge_decisions": [
                        *trace.judge_decisions,
                        safe_reason,
                    ][-8:]
                }
            ),
        }

    async def _replanner(self, state: MultiSourceState) -> dict:
        decision = JudgeDecision.model_validate(state["judge_result"])
        action = decision.recommended_action
        if action is None:
            action = await self.model.replan(state)
        return {
            "current_action": (
                ToolAction.model_validate(action) if action is not None else None
            )
        }

    async def _final_answer(self, state: MultiSourceState) -> dict:
        documents = self._deduplicate_documents(state.get("documents", []))[
            :MAX_EVIDENCE
        ]
        decision = JudgeDecision.model_validate(state["judge_result"])
        missing = self._safe_missing_information(decision.missing_information)
        answer = sanitize_text(
            await self.model.answer(
                state["request"].message,
                state["analysis"],
                documents,
                missing,
            )
        )
        if missing and "확인하지 못한 항목:" not in answer:
            answer = (
                f"{answer}\n\n확인하지 못한 항목: "
                + ", ".join(missing)
            ).strip()

        evidence = [
            self._to_evidence(item, index, state["policy"])
            for index, item in enumerate(documents, 1)
        ]
        citation_valid: bool | None = None
        if evidence:
            answer = self.validator.normalize(answer, evidence)
            validation = self.validator.validate(
                answer, evidence, state["policy"]
            )
            if not validation.valid:
                answer = await RuleBasedAgentModel().answer(
                    state["request"].message,
                    state["analysis"],
                    documents,
                    missing,
                )
                answer = self.validator.normalize(sanitize_text(answer), evidence)
                validation = self.validator.validate(
                    answer, evidence, state["policy"]
                )
            citation_valid = validation.valid

        limited = bool(
            not evidence
            or not decision.sufficient
            or (state.get("force_finish") and not decision.sufficient)
            or citation_valid is False
        )
        disclosures = list(state.get("disclosures", []))
        if limited:
            if LIMIT_DISCLOSURE not in disclosures:
                disclosures.append(LIMIT_DISCLOSURE)
            if LIMIT_DISCLOSURE not in answer:
                answer = f"{answer}\n\n{LIMIT_DISCLOSURE}".strip()

        trace = AgentTrace.model_validate(state.get("trace") or {})
        if not evidence:
            execution = ExecutionMetadata(
                status="limited",
                failure_stage="retrieval",
                error_code="NO_EVIDENCE",
                search_count=len(trace.tool_calls),
                evidence_count=0,
                include_in_llm_history=False,
            )
        elif citation_valid is False:
            execution = ExecutionMetadata(
                status="limited",
                failure_stage="citation_validation",
                error_code="CITATION_INVALID",
                search_count=len(trace.tool_calls),
                evidence_count=len(evidence),
                include_in_llm_history=False,
            )
        elif limited:
            execution = ExecutionMetadata(
                status="limited",
                failure_stage="grading",
                error_code="INSUFFICIENT_EVIDENCE",
                search_count=len(trace.tool_calls),
                evidence_count=len(evidence),
                include_in_llm_history=False,
            )
        else:
            execution = ExecutionMetadata(
                status="succeeded",
                search_count=len(trace.tool_calls),
                evidence_count=len(evidence),
            )
        return {
            "answer": answer,
            "evidence": evidence,
            "citation_valid": citation_valid,
            "limited_answer": limited,
            "disclosures": disclosures[:4],
            "execution": execution,
        }

    async def _save_memory(self, state: MultiSourceState) -> dict:
        analysis = state["analysis"]
        documents = state.get("documents", [])
        event_reference = self._event_reference(documents)
        safe_entities = {}
        for raw_key, raw_value in list(analysis.entities.items())[:16]:
            key = sanitize_text(str(raw_key))[:100]
            value = sanitize_text(str(raw_value))[:500]
            if key and value:
                safe_entities[key] = value
        topic = " ".join(dict.fromkeys(safe_entities.values()))[:500] or None
        trace = AgentTrace.model_validate(state.get("trace") or {})
        update = AgentMemoryUpdate(
            entities=safe_entities,
            current_topic=topic,
            search_history=trace.tool_calls,
            previous_event_reference=event_reference,
            retrieved_source_refs=[item.document_id for item in documents[:16]],
            unresolved_information=self._safe_missing_information(
                state["judge_result"].missing_information
            ),
        )
        return {"memory_update": update}

    @staticmethod
    def _safe_missing_information(items) -> list[str]:
        missing = []
        for raw in items:
            safe = sanitize_text(str(raw))
            safe = re.sub(r"\[S\d+\]", "", safe).strip()[:500]
            if safe and safe not in missing:
                missing.append(safe)
        return missing[:8]

    @staticmethod
    def _safe_metadata(item: SearchDocument) -> dict:
        safe = {}
        for key in ("start_at_utc", "end_at_utc"):
            value = item.metadata.get(key)
            if isinstance(value, str):
                cleaned = sanitize_text(value)
                if cleaned:
                    safe[key] = cleaned
        return safe

    @classmethod
    def _normalize_document(cls, item: SearchDocument) -> SearchDocument:
        text = sanitize_text(item.text) or "[REDACTED]"
        return item.model_copy(
            update={
                "title": sanitize_text(item.title),
                "text": text,
                "metadata": cls._safe_metadata(item),
            }
        )

    @classmethod
    def _deduplicate_documents(cls, documents):
        unique = {}
        for raw_item in documents:
            item = cls._normalize_document(SearchDocument.model_validate(raw_item))
            existing = unique.get(item.document_id)
            if existing is None or item.score > existing.score:
                unique[item.document_id] = item
        return sorted(
            unique.values(), key=lambda item: (-item.score, item.document_id)
        )[:20]

    @staticmethod
    def _safe_locator(item: SearchDocument) -> str | None:
        raw_identifier = item.source_id or item.parent_event_id or item.document_id
        if not re.fullmatch(r"[A-Za-z0-9_.:@+-]{1,256}", raw_identifier):
            return None
        prefix = {
            "mail": "mail",
            "calendar": "calendar",
            "domain_knowledge": "domain",
        }[item.source_type]
        return f"{prefix}:{raw_identifier}"

    @classmethod
    def _to_evidence(
        cls, item: SearchDocument, index: int, policy: PolicyContext
    ) -> Evidence:
        return Evidence(
            evidence_id=f"S{index}",
            source_type=item.source_type,
            document_id=item.document_id,
            parent_id=item.parent_event_id,
            title=item.title,
            excerpt=item.text,
            source_locator=cls._safe_locator(item),
            score=item.score,
            user_id=policy.user_id,
            acl_decision_id=policy.decision_id,
            content_hash=sha256(item.text.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _event_reference(
        documents: list[SearchDocument],
    ) -> EventReference | None:
        event = next(
            (
                item
                for item in documents
                if item.source_type == "calendar"
                and item.content_kind == "event"
            ),
            None,
        )
        if event is None:
            return None

        def parse(key: str) -> datetime | None:
            raw = event.metadata.get(key)
            if not isinstance(raw, str):
                return None
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None

        return EventReference(
            event_id=event.parent_event_id or event.document_id,
            subject=event.title,
            start_at_utc=parse("start_at_utc"),
            end_at_utc=parse("end_at_utc"),
        )

    @staticmethod
    def _after_judge(
        state: MultiSourceState,
    ) -> Literal["continue", "finish"]:
        decision = JudgeDecision.model_validate(state["judge_result"])
        if (
            state.get("force_finish")
            or decision.sufficient
            or state.get("iteration_count", 0) >= MAX_ITERATIONS
            or decision.recommended_action is None
        ):
            return "finish"
        return "continue"

    async def invoke(
        self,
        request: ChatRequest,
        policy: PolicyContext,
        conversation: ConversationMemory | None,
    ) -> FastRAGResult:
        if request.user_id != policy.user_id:
            raise ValueError("request owner and policy owner must match exactly")
        state = await self.graph.ainvoke(
            {
                "request": request,
                "policy": policy,
                "conversation": conversation or ConversationMemory(),
                "observations": [],
                "documents": [],
                "fingerprints": [],
                "iteration_count": 0,
                "force_finish": False,
                "disclosures": [],
                "retrieval_mode": "hybrid",
                "trace": AgentTrace(),
            }
        )
        return FastRAGResult(
            answer=state["answer"],
            evidence=state["evidence"],
            quality=QualityStatus(
                citation_valid=state["citation_valid"],
                limited_answer=state["limited_answer"],
                retrieval_mode=state["retrieval_mode"],
            ),
            disclosures=state["disclosures"],
            execution=state["execution"],
            agent_memory=state["memory_update"],
            agent_trace=state["trace"],
        )
