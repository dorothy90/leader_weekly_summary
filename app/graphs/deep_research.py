import asyncio
from time import perf_counter
from typing import Literal, TypedDict
import uuid

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ChatRequest,
    ExecutionMetadata,
    normalize_bm25_fallback,
)
from app.domain.evidence import Evidence, RetrievalFilters, SearchTask
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.domain.research import MAX_RESEARCH_REPORT_BYTES, RESEARCH_ABSTENTION
from app.persistence.conversations import sanitize_evidence_for_memory
from app.observability.tracing import TraceEvent, emit_trace, hash_trace_value
from app.observability.node_runs import ensure_node_recorder, instrument_node
from app.security.citations import CitationValidator
from app.security.redaction import sanitize_text

MAX_SUBQUESTIONS = 8
MAX_FOLLOW_UP_QUESTIONS = 4
MAX_SEARCHES = 12
MAX_ROUNDS = 2
MAX_CONCURRENCY = 4
MAX_EVIDENCE = 32
MAX_MODEL_INPUT_BYTES = 32_000
MAX_CONTEXT_TOKENS = MAX_MODEL_INPUT_BYTES
MAX_REPORT_BYTES = MAX_RESEARCH_REPORT_BYTES
MAX_REPORT_REVISIONS = 1
INVALID_REPORT = "조사 근거의 인용을 검증하지 못해 보고서를 제공할 수 없습니다."


class ResearchPlan(BaseModel):
    # The workflow applies the authoritative cap after parsing. Keeping the
    # parser tolerant lets an over-producing model degrade deterministically.
    sub_questions: list[str] = Field(min_length=1, max_length=24)


class GapDecision(BaseModel):
    complete: bool
    follow_up_questions: list[str] = Field(default_factory=list, max_length=24)


class ClaimSupportDecision(BaseModel):
    supported: bool
    unsupported_claims: list[str] = Field(default_factory=list, max_length=16)


class BranchResult(BaseModel):
    question: str
    evidence: list[Evidence] = Field(default_factory=list)
    failed: bool = False
    embedding_fallback: bool = False
    error_code: str | None = None


class DeepResearchResult(BaseModel):
    report: str
    evidence: list[Evidence] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    completed_sub_questions: int = Field(ge=0, le=MAX_SEARCHES)
    rounds: int = Field(ge=0, le=MAX_ROUNDS)
    disclosures: list[str] = Field(default_factory=list, max_length=4)
    citation_valid: bool = False
    execution: ExecutionMetadata | None = None


class DeepState(TypedDict, total=False):
    question: str
    policy: PolicyContext
    queries: list[str]
    branch_results: list[BranchResult]
    searches: int
    rounds: int
    round_id: str | None
    outage_pending: bool
    complete: bool
    evidence: list[Evidence]
    report: str
    disclosures: list[str]
    citation_valid: bool
    filters: RetrievalFilters
    checkpoint: dict
    progress_callback: object


def _truncate_utf8(text: str, byte_limit: int) -> str:
    return text.encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")


def _evidence_context(
    evidence: list[Evidence], byte_limit: int = MAX_CONTEXT_TOKENS
) -> str:
    remaining = byte_limit
    sections = []
    for item in evidence[:MAX_EVIDENCE]:
        prefix = f"[{item.evidence_id}] {item.title}\n"
        separator = "\n\n" if sections else ""
        overhead = len((separator + prefix).encode("utf-8"))
        if overhead >= remaining:
            break
        excerpt = _truncate_utf8(item.excerpt, remaining - overhead)
        section = f"{separator}{prefix}{excerpt}"
        sections.append(section)
        remaining -= len(section.encode("utf-8"))
        if remaining <= 0:
            break
    return "".join(sections)


def _bounded_model_input(system: str, sections: list[str]) -> str:
    remaining = max(0, MAX_MODEL_INPUT_BYTES - len(system.encode("utf-8")))
    output = []
    for section in sections:
        if remaining <= 0:
            break
        bounded = _truncate_utf8(section, remaining)
        output.append(bounded)
        remaining -= len(bounded.encode("utf-8"))
    return "".join(output)


class DeepResearchWorkflow:
    """Finite Deep-only research graph using the shared retrieval service."""

    def __init__(
        self,
        retrieval,
        llm,
        trace_sink=None,
    ):
        self.retrieval = retrieval
        self.llm = llm
        self.validator = CitationValidator()
        self.trace_sink = trace_sink
        self.graph = self._build()

    def _build(self):
        graph = StateGraph(DeepState)
        graph.add_node("plan", instrument_node("deep.plan", self._plan))
        graph.add_node("research", instrument_node("deep.research", self._research))
        graph.add_node("gap", instrument_node("deep.gap", self._gap))
        graph.add_node(
            "synthesize",
            instrument_node("deep.synthesize", self._synthesize),
        )
        graph.add_edge(START, "plan")
        graph.add_edge("plan", "research")
        graph.add_edge("research", "gap")
        graph.add_conditional_edges(
            "gap",
            self._after_gap,
            {"research": "research", "synthesize": "synthesize"},
        )
        graph.add_edge("synthesize", END)
        return graph.compile()

    async def _plan(self, state: DeepState) -> dict:
        question = sanitize_text(state["question"]) or "메일 조사"
        checkpoint = state.get("checkpoint") or {}
        if checkpoint:
            branches = [
                BranchResult.model_validate(item)
                for item in checkpoint.get("branch_results", [])
            ]
            completed = {item.question for item in branches if not item.failed}
            pending_queries = [
                item
                for item in checkpoint.get("pending_queries", [])
                if item not in completed
            ]
            return {
                "question": question,
                "queries": pending_queries,
                "branch_results": branches,
                "searches": int(checkpoint.get("searches", 0)),
                "rounds": int(checkpoint.get("rounds", 0)),
                "round_id": checkpoint.get("round_id") if pending_queries else None,
                "outage_pending": bool(checkpoint.get("outage_pending", False)),
            }
        try:
            plan = await self.llm.complete_model(
                "Create objective, independent mail-research sub-questions.",
                question,
                ResearchPlan,
            )
            candidates = plan.sub_questions
        except Exception:
            candidates = [question]
        queries = []
        for item in candidates:
            safe = _truncate_utf8(sanitize_text(str(item)), 1000)
            if safe and safe not in queries:
                queries.append(safe)
            if len(queries) == MAX_SUBQUESTIONS:
                break
        return {
            "question": question,
            "queries": queries or [question],
            "branch_results": [],
            "searches": 0,
            "rounds": 0,
            "round_id": None,
            "outage_pending": False,
        }

    async def _research_one(
        self,
        question: str,
        policy: PolicyContext,
        filters: RetrievalFilters,
        semaphore: asyncio.Semaphore,
    ) -> BranchResult:
        async with semaphore:
            try:
                result = await self.retrieval.search(
                    SearchTask(query=question, filters=filters, top_k=8),
                    policy,
                )
            except AppError as error:
                return BranchResult(
                    question=question, failed=True, error_code=error.code.value
                )
            except Exception:
                return BranchResult(
                    question=question,
                    failed=True,
                    error_code=ErrorCode.INDEX_UNAVAILABLE.value,
                )
        owned = [item for item in result.evidence if item.user_id == policy.user_id]
        fallback = (
            result.embedding_error == "EMBEDDING_UNAVAILABLE"
            or BM25_FALLBACK_DISCLOSURE in result.disclosures
        )
        return BranchResult(
            question=question,
            evidence=owned,
            embedding_fallback=fallback,
        )

    async def _research(self, state: DeepState) -> dict:
        remaining = max(0, MAX_SEARCHES - state["searches"])
        all_pending = list(state["queries"])
        queries = all_pending[:remaining]
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        if not queries:
            return {}

        round_id = state.get("round_id") or uuid.uuid4().hex
        reserved_searches = state["searches"] + len(queries)
        successful = [item for item in state["branch_results"] if not item.failed]
        pending = list(all_pending)
        callback = state.get("progress_callback")

        async def checkpoint_progress(stage: str, rounds: int) -> None:
            if callback is None:
                return
            checkpoint_state = {**state, "branch_results": successful}
            compressed = self._safe_evidence(checkpoint_state)
            safe_branches = [
                item.model_copy(
                    update={
                        "evidence": [
                            sanitize_evidence_for_memory(evidence, state["policy"])
                            for evidence in item.evidence
                            if evidence.user_id == state["policy"].user_id
                        ][:MAX_EVIDENCE]
                    }
                )
                for item in successful
            ]
            await callback(
                {
                    "stage": stage,
                    "progress": min(90, 10 + len(successful) * 8),
                    "completed_sub_questions": [item.question for item in successful],
                    "rounds_completed": rounds,
                    "compressed_evidence": compressed,
                    "checkpoint": {
                        "pending_queries": list(pending),
                        "branch_results": [
                            item.model_dump(mode="json") for item in safe_branches
                        ],
                        "searches": reserved_searches,
                        "rounds": rounds,
                        "round_id": round_id,
                        "outage_pending": bool(pending and not successful),
                    },
                }
            )

        # Reserve the whole batch durably before starting it. A process crash can
        # then over-count an unstarted call, but can never execute more retrievals
        # than the hard budget across lease reclaims.
        await checkpoint_progress("research_in_progress", state["rounds"])

        async def run_one(item):
            return item, await self._research_one(
                item, state["policy"], state["filters"], semaphore
            )

        tasks = [asyncio.create_task(run_one(item)) for item in queries]
        fresh = []
        try:
            for future in asyncio.as_completed(tasks):
                question, branch = await future
                fresh.append(branch)
                if not branch.failed:
                    successful.append(branch)
                    pending.remove(question)
                completed_rounds = min(MAX_ROUNDS, state["rounds"] + (not pending))
                await checkpoint_progress(
                    "research_in_progress" if pending else "research_complete",
                    completed_rounds,
                )
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        updated = {
            "branch_results": [*state["branch_results"], *fresh],
            "searches": reserved_searches,
            "rounds": min(MAX_ROUNDS, state["rounds"] + (not pending)),
            "round_id": round_id if pending else None,
            "outage_pending": bool(pending and not successful),
        }
        return updated

    async def _gap(self, state: DeepState) -> dict:
        if state.get("round_id"):
            return {"complete": True, "queries": []}
        if state["rounds"] >= MAX_ROUNDS or state["searches"] >= MAX_SEARCHES:
            return {"complete": True, "queries": []}
        summary = "\n".join(
            f"{item.question}: {len(item.evidence)} evidence"
            for item in state["branch_results"]
        )
        try:
            decision = await self.llm.complete_model(
                "Identify only material evidence gaps; stop when sufficient.",
                _truncate_utf8(summary, MAX_CONTEXT_TOKENS),
                GapDecision,
            )
        except Exception:
            return {"complete": True, "queries": []}
        completed = {
            item.question for item in state["branch_results"] if not item.failed
        }
        queries = []
        for item in decision.follow_up_questions:
            safe = _truncate_utf8(sanitize_text(str(item)), 1000)
            if safe and safe not in completed and safe not in queries:
                queries.append(safe)
            if len(queries) == MAX_FOLLOW_UP_QUESTIONS:
                break
        return {"complete": decision.complete or not queries, "queries": queries}

    @staticmethod
    def _after_gap(state: DeepState) -> Literal["research", "synthesize"]:
        if (
            state.get("complete", True)
            or state["rounds"] >= MAX_ROUNDS
            or state["searches"] >= MAX_SEARCHES
            or not state.get("queries")
        ):
            return "synthesize"
        return "research"

    @staticmethod
    def _safe_evidence(state: DeepState) -> list[Evidence]:
        policy = state["policy"]
        unique: dict[tuple[str, str], Evidence] = {}
        for branch in state["branch_results"]:
            if branch.failed:
                continue
            for item in branch.evidence:
                if item.user_id != policy.user_id:
                    continue
                unique.setdefault((item.source_type, item.document_id), item)
        safe = []
        for item in list(unique.values())[:MAX_EVIDENCE]:
            sanitized = sanitize_evidence_for_memory(item, policy)
            if sanitized.excerpt:
                safe.append(
                    sanitized.model_copy(update={"evidence_id": f"S{len(safe) + 1}"})
                )
        return safe

    async def _synthesize(self, state: DeepState) -> dict:
        branches = state["branch_results"]
        if (branches and all(item.failed for item in branches)) or (
            state.get("outage_pending")
            and not any(not item.failed for item in branches)
        ):
            raise AppError(
                ErrorCode.INDEX_UNAVAILABLE,
                "검색 인덱스를 사용할 수 없습니다.",
                retryable=True,
            )
        evidence = self._safe_evidence(state)
        fallback = any(item.embedding_fallback for item in state["branch_results"])
        disclosures = [BM25_FALLBACK_DISCLOSURE] if fallback else []
        if not evidence:
            report, disclosures = normalize_bm25_fallback(
                RESEARCH_ABSTENTION,
                disclosures,
                max_bytes=MAX_REPORT_BYTES,
            )
            return {
                "report": report,
                "evidence": [],
                "disclosures": disclosures,
                "citation_valid": True,
            }

        context = _evidence_context(evidence)
        report = ""
        validation = None
        for revision in range(MAX_REPORT_REVISIONS + 1):
            instruction = (
                "Synthesize only supported claims and retain exact [S#] citations."
                if revision == 0
                else "Revise the draft so every claim uses only the supplied [S#] evidence."
            )
            prompt = _bounded_model_input(
                instruction,
                (
                    [f"Question: {state['question']}\n", "Evidence:\n", context]
                    if revision == 0
                    else [f"Draft: {report}\n", "Evidence:\n", context]
                ),
            )
            report = _truncate_utf8(
                sanitize_text(await self.llm.complete_text(instruction, prompt)),
                MAX_REPORT_BYTES,
            )
            report = self.validator.normalize(report, evidence)
            validation = self.validator.validate(report, evidence, state["policy"])
            if validation.valid:
                break
        if validation is None or not validation.valid:
            report, disclosures = normalize_bm25_fallback(
                INVALID_REPORT,
                disclosures,
                max_bytes=MAX_REPORT_BYTES,
            )
            return {
                "report": report,
                "evidence": [],
                "disclosures": disclosures,
                "citation_valid": False,
            }
        try:
            support = await self.llm.complete_model(
                "Check every factual claim against the supplied evidence. "
                "A syntactically valid citation is not sufficient support.",
                _bounded_model_input(
                    "claim-support",
                    [f"Draft:\n{report}\n", "Evidence:\n", context],
                ),
                ClaimSupportDecision,
            )
        except Exception:
            support = ClaimSupportDecision(supported=False)
        if not support.supported:
            report, disclosures = normalize_bm25_fallback(
                INVALID_REPORT, disclosures, max_bytes=MAX_REPORT_BYTES
            )
            return {
                "report": report,
                "evidence": [],
                "disclosures": disclosures,
                "citation_valid": False,
            }
        evidence_by_id = {item.evidence_id: item for item in evidence}
        cited = [evidence_by_id[item] for item in validation.cited_ids]
        report, disclosures = normalize_bm25_fallback(
            report,
            disclosures,
            max_bytes=MAX_REPORT_BYTES,
        )
        return {
            "report": report,
            "evidence": cited,
            "disclosures": disclosures,
            "citation_valid": True,
        }

    async def invoke(
        self,
        question: str,
        policy: PolicyContext,
        filters: RetrievalFilters | None = None,
        checkpoint: dict | None = None,
        progress_callback=None,
    ) -> DeepResearchResult:
        with ensure_node_recorder() as recorder:
            result = await self._invoke_recorded(
                question,
                policy,
                filters,
                checkpoint,
                progress_callback,
            )
            if result.execution is not None:
                result = result.model_copy(
                    update={
                        "execution": result.execution.model_copy(
                            update={"node_runs": recorder.snapshot()}
                        )
                    }
                )
            return result

    async def _invoke_recorded(
        self,
        question: str,
        policy: PolicyContext,
        filters: RetrievalFilters | None = None,
        checkpoint: dict | None = None,
        progress_callback=None,
    ) -> DeepResearchResult:
        started = perf_counter()
        try:
            result = await self._invoke(
                question, policy, filters, checkpoint, progress_callback
            )
        except Exception as error:
            emit_trace(
                self.trace_sink,
                TraceEvent(
                    trace_id=uuid.uuid4().hex,
                    node_name="deep_research.invoke",
                    duration_ms=int((perf_counter() - started) * 1000),
                    status="error",
                    index_version_hash=hash_trace_value("shared-retrieval"),
                    prompt_version_hash=hash_trace_value("deep-v1"),
                    model_hash=hash_trace_value(
                        str(getattr(self.llm, "model", "none"))
                    ),
                    owner_hash=hash_trace_value(policy.user_id),
                    query_hash=hash_trace_value(question),
                    route="deep",
                    error_class=type(error).__name__,
                ),
            )
            raise
        elapsed_ms = int((perf_counter() - started) * 1000)
        if result.execution is not None:
            result = result.model_copy(
                update={
                    "execution": result.execution.model_copy(
                        update={"duration_ms": elapsed_ms}
                    )
                }
            )
        emit_trace(
            self.trace_sink,
            TraceEvent(
                trace_id=uuid.uuid4().hex,
                node_name="deep_research.invoke",
                duration_ms=int((perf_counter() - started) * 1000),
                status="ok",
                index_version_hash=hash_trace_value("shared-retrieval"),
                prompt_version_hash=hash_trace_value("deep-v1"),
                model_hash=hash_trace_value(str(getattr(self.llm, "model", "none"))),
                owner_hash=hash_trace_value(policy.user_id),
                query_hash=hash_trace_value(question),
                document_hashes=[
                    hash_trace_value(item.document_id) for item in result.evidence
                ],
                evidence_count=len(result.evidence),
                retrieval_mode=(
                    "bm25"
                    if BM25_FALLBACK_DISCLOSURE in result.disclosures
                    else "hybrid"
                ),
                route="deep",
            ),
        )
        return result

    async def _invoke(
        self,
        question: str,
        policy: PolicyContext,
        filters: RetrievalFilters | None = None,
        checkpoint: dict | None = None,
        progress_callback=None,
    ) -> DeepResearchResult:
        if not isinstance(policy, PolicyContext):
            raise AppError(
                ErrorCode.UNAUTHORIZED_RESOURCE, "조사 소유자를 확인할 수 없습니다."
            )
        state = await self.graph.ainvoke(
            {
                "question": _truncate_utf8(sanitize_text(question), 4000),
                "policy": policy,
                "filters": filters or RetrievalFilters(),
                "checkpoint": checkpoint or {},
                "progress_callback": progress_callback,
            }
        )
        evidence = state.get("evidence", [])
        citation_valid = state.get("citation_valid", False)
        if not evidence:
            execution = ExecutionMetadata(
                status="limited",
                failure_stage="retrieval",
                error_code="NO_EVIDENCE",
                search_count=state.get("searches", 0),
                evidence_count=0,
                include_in_llm_history=False,
            )
        elif not citation_valid:
            execution = ExecutionMetadata(
                status="limited",
                failure_stage="citation_validation",
                error_code="CITATION_INVALID",
                search_count=state.get("searches", 0),
                evidence_count=len(evidence),
                include_in_llm_history=False,
            )
        else:
            execution = ExecutionMetadata(
                status="succeeded",
                search_count=state.get("searches", 0),
                evidence_count=len(evidence),
            )
        return DeepResearchResult(
            report=state["report"],
            evidence=evidence,
            completed_sub_questions=sum(
                not item.failed for item in state.get("branch_results", [])
            ),
            rounds=state["rounds"],
            disclosures=state.get("disclosures", []),
            citation_valid=citation_valid,
            execution=execution,
        )


class DeepCoordinator:
    def __init__(self, jobs):
        self.jobs = jobs

    async def enqueue(
        self,
        request: ChatRequest,
        policy: PolicyContext,
        trace_id: str,
    ):
        if request.user_id != policy.user_id:
            raise AppError(
                ErrorCode.UNAUTHORIZED_RESOURCE,
                "조사 소유자를 확인할 수 없습니다.",
            )
        scope = []
        if request.filters.teams:
            scope.append(f"{len(request.filters.teams)}개 팀")
        if request.filters.weeks:
            scope.append(f"{len(request.filters.weeks)}주")
        summary = ", ".join(scope) or "질문 범위 조사"
        return await self.jobs.create(
            policy,
            trace_id,
            request.message,
            summary,
            request.filters,
        )
