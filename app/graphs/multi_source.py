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
    SourceName,
    ToolAction,
    normalize_stable_event_id,
    search_document_identity,
)
from app.domain.chat import (
    ChatRequest,
    ExecutionMetadata,
    FastRAGResult,
    QualityStatus,
)
from app.domain.evidence import Evidence
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.llm.agentic import AgentAnalyzer
from app.persistence.conversations import ConversationMemory
from app.retrieval.multi_source import MultiSourceSearch
from app.security.citations import CitationValidator
from app.security.redaction import sanitize_text


MAX_ITERATIONS = 4
MAX_EVIDENCE = 8
LIMIT_DISCLOSURE = "검색 한계 내에서 확인된 범위만 답변했습니다."
SOURCE_UNAVAILABLE_DISCLOSURE = "일부 검색 소스를 사용할 수 없습니다."
ANALYSIS_UNAVAILABLE_MESSAGE = (
    "질문을 안전하게 구조화하지 못했습니다. 질문을 다시 표현해 주세요."
)
ANALYSIS_UNAVAILABLE_DISCLOSURE = (
    "질문 분석을 사용할 수 없어 검색을 수행하지 않았습니다."
)
RECOVERABLE_SOURCE_ERRORS = {
    ErrorCode.INDEX_UNAVAILABLE,
    ErrorCode.EMBEDDING_UNAVAILABLE,
    ErrorCode.RETRIEVAL_TIMEOUT,
    ErrorCode.DEPENDENCY_UNAVAILABLE,
}


class MultiSourceState(TypedDict, total=False):
    request: ChatRequest
    policy: PolicyContext
    conversation: ConversationMemory
    analysis: QueryAnalysis
    candidates: list[SourceName]
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
    source_error_code: str | None
    source_error_retryable: bool
    retrieval_mode: Literal["hybrid", "bm25", "deterministic"]
    trace: AgentTrace
    execution: ExecutionMetadata


class MultiSourceAgenticWorkflow:
    def __init__(
        self,
        search: MultiSourceSearch,
        analyzer: AgentAnalyzer,
        *,
        timezone_name: str | None = None,
    ):
        self.search = search
        self.analyzer = analyzer
        self.agent = analyzer
        self.timezone_name = (
            timezone_name or Settings().default_user_timezone
        )
        self.validator = CitationValidator()
        self.graph = self._build()

    @staticmethod
    def _combine_retrieval_modes(*modes: str) -> str:
        if "hybrid" in modes:
            return "hybrid"
        if "bm25" in modes:
            return "bm25"
        return "deterministic"

    @staticmethod
    def _calendar_relation_id(item: SearchDocument) -> str | None:
        if item.source_type != "calendar":
            return None
        if item.content_kind == "event":
            return normalize_stable_event_id(item.source_id)
        if item.content_kind == "attachment":
            return normalize_stable_event_id(item.parent_event_id)
        return None

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
        analysis = await self.analyzer.analyze(
            state["request"].message,
            state["conversation"],
            self.timezone_name,
        )
        trace = AgentTrace.model_validate(state.get("trace") or {})
        return {
            "analysis": QueryAnalysis.model_validate(analysis),
            "trace": trace.model_copy(
                update={"llm_calls": [*trace.llm_calls, "routing"][-16:]}
            ),
        }

    async def _source_discovery(self, state: MultiSourceState) -> dict:
        return {
            "candidates": [
                item.source for item in state["analysis"].source_requests
            ]
        }

    async def _planner(self, state: MultiSourceState) -> dict:
        analysis = QueryAnalysis.model_validate(state["analysis"])
        if analysis.analysis_status != "ready":
            return {"current_action": None}
        action = await self.agent.plan(
            state["request"].message,
            analysis,
            state.get("observations", []),
            state["conversation"],
        )
        trace = AgentTrace.model_validate(state.get("trace") or {})
        return {
            "current_action": (
                ToolAction.model_validate(action)
                if action is not None
                else None
            ),
            "trace": trace.model_copy(
                update={"llm_calls": [*trace.llm_calls, "planner"][-16:]}
            ),
        }

    async def _execute_source(
        self,
        action: ToolAction,
        state: MultiSourceState,
        analysis: QueryAnalysis,
    ) -> SearchResult:
        try:
            result = await self.search.execute(
                action,
                state["policy"],
                analysis,
                request_filters=state["request"].filters,
            )
        except AppError as error:
            if error.code not in RECOVERABLE_SOURCE_ERRORS:
                raise
            return SearchResult(
                tool=action.tool,
                query=action.query,
                retrieval_mode=state.get(
                    "retrieval_mode",
                    "deterministic",
                ),
                disclosures=[SOURCE_UNAVAILABLE_DISCLOSURE],
                error_code=error.code.value,
                retryable=error.retryable,
            )
        return SearchResult.model_validate(result)

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

        result = await self._execute_source(action, state, analysis)
        fingerprints.add(fingerprint)
        semantic_increment = 0 if action.tool == "expand_calendar_event" else 1
        iteration_count = state.get("iteration_count", 0) + semantic_increment
        tool_calls = [*trace.tool_calls, action.tool]

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
            "retrieval_mode": self._combine_retrieval_modes(
                state.get("retrieval_mode", "deterministic"),
                result.retrieval_mode,
            ),
            "source_error_code": (
                state.get("source_error_code")
                or (
                    result.error_code
                    if result.error_code
                    in {item.value for item in RECOVERABLE_SOURCE_ERRORS}
                    else None
                )
            ),
            "source_error_retryable": (
                state.get("source_error_retryable", False)
                or result.retryable
            ),
        }

    async def _judge(self, state: MultiSourceState) -> dict:
        analysis = QueryAnalysis.model_validate(state["analysis"])
        if analysis.analysis_status != "ready":
            decision = JudgeDecision(
                sufficient=False,
                reason="analysis unavailable",
                missing_information=[],
                recommended_action=None,
            )
        else:
            decision = JudgeDecision.model_validate(
                await self.agent.judge(
                    state["request"].message,
                    analysis,
                    state.get("observations", []),
                    state.get("documents", []),
                    state["conversation"],
                    state.get("iteration_count", 0),
                )
            )
        trace = AgentTrace.model_validate(state.get("trace") or {})
        safe_reason = sanitize_text(decision.reason)[:500] or "judge_result_redacted"
        return {
            "judge_result": decision,
            "trace": trace.model_copy(
                update={
                    "judge_decisions": [
                        *trace.judge_decisions,
                        safe_reason,
                    ][-8:],
                    "llm_calls": (
                        [*trace.llm_calls, "judge"][-16:]
                        if analysis.analysis_status == "ready"
                        else trace.llm_calls
                    ),
                }
            ),
        }

    async def _replanner(self, state: MultiSourceState) -> dict:
        decision = JudgeDecision.model_validate(state["judge_result"])
        action = decision.recommended_action
        return {
            "current_action": (
                ToolAction.model_validate(action) if action is not None else None
            )
        }

    async def _final_answer(self, state: MultiSourceState) -> dict:
        analysis = QueryAnalysis.model_validate(state["analysis"])
        if analysis.analysis_status == "unavailable":
            return {
                "answer": ANALYSIS_UNAVAILABLE_MESSAGE,
                "evidence": [],
                "citation_valid": None,
                "limited_answer": True,
                "disclosures": [ANALYSIS_UNAVAILABLE_DISCLOSURE],
                "execution": ExecutionMetadata(
                    status="limited",
                    failure_stage="planning",
                    error_code="ANALYSIS_UNAVAILABLE",
                    search_count=0,
                    evidence_count=0,
                    include_in_llm_history=False,
                ),
            }
        documents = self._deduplicate_documents(state.get("documents", []))[
            :MAX_EVIDENCE
        ]
        decision = JudgeDecision.model_validate(state["judge_result"])
        missing = self._safe_missing_information(decision.missing_information)
        answer = sanitize_text(
            await self.agent.answer(
                state["request"].message,
                analysis,
                documents,
                missing,
                state["conversation"],
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
            citation_valid = validation.valid

        trace = AgentTrace.model_validate(state.get("trace") or {})
        trace = trace.model_copy(
            update={"llm_calls": [*trace.llm_calls, "answer"][-16:]}
        )

        limited = bool(
            not evidence
            or not decision.sufficient
            or (state.get("force_finish") and not decision.sufficient)
            or citation_valid is False
            or state.get("source_error_code")
        )
        disclosures = list(state.get("disclosures", []))
        if limited:
            disclosures = [
                item for item in disclosures if item != LIMIT_DISCLOSURE
            ][:3]
            disclosures.append(LIMIT_DISCLOSURE)
            if LIMIT_DISCLOSURE not in answer:
                answer = f"{answer}\n\n{LIMIT_DISCLOSURE}".strip()

        source_error_code = state.get("source_error_code")
        if source_error_code:
            execution = ExecutionMetadata(
                status="limited",
                failure_stage="retrieval",
                error_code=source_error_code,
                retryable=state.get("source_error_retryable", False),
                search_count=len(trace.tool_calls),
                evidence_count=len(evidence),
                include_in_llm_history=False,
            )
        elif not evidence:
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
            "trace": trace,
        }

    async def _save_memory(self, state: MultiSourceState) -> dict:
        analysis = state["analysis"]
        documents = self._deduplicate_documents(state.get("documents", []))
        event_reference = self._event_reference(documents)
        evidence_text = "\n".join(
            f"{item.title} {item.text}" for item in documents
        ).casefold()
        safe_entities = {}
        for raw_key, raw_value in list(analysis.entities.items())[:16]:
            key = sanitize_text(str(raw_key))[:100]
            value = sanitize_text(str(raw_value))[:500]
            if key and value and value.casefold() in evidence_text:
                safe_entities[key] = value
        topic = next(
            (
                sanitize_text(item.title)[:500]
                for item in documents
                if sanitize_text(item.title)
            ),
            None,
        )
        judged_missing = self._safe_missing_information(
            JudgeDecision.model_validate(state["judge_result"]).missing_information
        )
        unresolved = judged_missing or list(
            state["conversation"].unresolved_information
        )
        trace = AgentTrace.model_validate(state.get("trace") or {})
        update = AgentMemoryUpdate(
            entities=safe_entities,
            current_topic=topic,
            search_history=trace.tool_calls,
            previous_event_reference=event_reference,
            retrieved_source_refs=[item.document_id for item in documents[:16]],
            unresolved_information=unresolved,
        )
        return {"memory_update": update}

    @staticmethod
    def _safe_missing_information(items) -> list[str]:
        missing = []
        for raw in items:
            safe = sanitize_text(str(raw))
            safe = re.sub(r"\[S\d+\]", "", safe).strip()[:500]
            safe = re.sub(r"\[REDACTED_[A-Z_]+\]", "", safe)
            safe = safe.strip(" ,;:")
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
            key = search_document_identity(item)
            existing = unique.get(key)
            if existing is None or item.score > existing.score:
                unique[key] = item
        return sorted(
            unique.values(),
            key=lambda item: (
                -item.score,
                item.source_type,
                item.document_id,
                item.content_kind or "",
                item.source_id or "",
                item.parent_event_id or "",
            ),
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
        selected = next(
            (
                item
                for item in documents
                if item.source_type == "calendar"
                and item.content_kind == "event"
                and normalize_stable_event_id(item.source_id) is not None
            ),
            None,
        )
        event_id = (
            normalize_stable_event_id(selected.source_id)
            if selected is not None
            else None
        )
        if selected is None:
            selected = next(
                (
                    item
                    for item in documents
                    if item.source_type == "calendar"
                    and item.content_kind == "attachment"
                    and normalize_stable_event_id(item.parent_event_id)
                    is not None
                ),
                None,
            )
            event_id = (
                normalize_stable_event_id(selected.parent_event_id)
                if selected is not None
                else None
            )
        if selected is None or event_id is None:
            return None

        def parse(key: str) -> datetime | None:
            raw = selected.metadata.get(key)
            if not isinstance(raw, str):
                return None
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                return None

        return EventReference(
            event_id=event_id,
            subject=selected.title,
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
                "source_error_code": None,
                "source_error_retryable": False,
                "retrieval_mode": "deterministic",
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
