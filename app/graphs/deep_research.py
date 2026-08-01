import asyncio
from typing import Literal, TypedDict

import langchain
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ChatRequest,
    normalize_bm25_fallback,
)
from app.domain.evidence import Evidence, RetrievalFilters, SearchTask
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.domain.research import MAX_RESEARCH_REPORT_BYTES, RESEARCH_ABSTENTION
from app.persistence.conversations import sanitize_evidence_for_memory
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
MAX_ELAPSED_SECONDS = 120

INVALID_REPORT = "조사 근거의 인용을 검증하지 못해 보고서를 제공할 수 없습니다."


class ResearchPlan(BaseModel):
    # The workflow applies the authoritative cap after parsing. Keeping the
    # parser tolerant lets an over-producing model degrade deterministically.
    sub_questions: list[str] = Field(min_length=1, max_length=24)


class GapDecision(BaseModel):
    complete: bool
    follow_up_questions: list[str] = Field(default_factory=list, max_length=24)


class BranchResult(BaseModel):
    question: str
    evidence: list[Evidence] = Field(default_factory=list)
    failed: bool = False
    embedding_fallback: bool = False


class DeepResearchResult(BaseModel):
    report: str
    evidence: list[Evidence] = Field(default_factory=list, max_length=MAX_EVIDENCE)
    completed_sub_questions: int = Field(ge=0, le=MAX_SEARCHES)
    rounds: int = Field(ge=0, le=MAX_ROUNDS)
    disclosures: list[str] = Field(default_factory=list, max_length=4)
    citation_valid: bool = False


class DeepState(TypedDict, total=False):
    question: str
    policy: PolicyContext
    queries: list[str]
    branch_results: list[BranchResult]
    searches: int
    rounds: int
    complete: bool
    evidence: list[Evidence]
    report: str
    disclosures: list[str]
    citation_valid: bool
    filters: RetrievalFilters


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

    def __init__(self, retrieval, llm):
        self.retrieval = retrieval
        self.llm = llm
        self.validator = CitationValidator()
        self.graph = self._build()

    def _build(self):
        graph = StateGraph(DeepState)
        graph.add_node("plan", self._plan)
        graph.add_node("research", self._research)
        graph.add_node("gap", self._gap)
        graph.add_node("synthesize", self._synthesize)
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
            except Exception:
                return BranchResult(question=question, failed=True)
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
        queries = state["queries"][:remaining]
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        fresh = await asyncio.gather(
            *(
                self._research_one(
                    item,
                    state["policy"],
                    state["filters"],
                    semaphore,
                )
                for item in queries
            )
        )
        return {
            "branch_results": [*state["branch_results"], *fresh],
            "searches": state["searches"] + len(queries),
            "rounds": state["rounds"] + 1,
        }

    async def _gap(self, state: DeepState) -> dict:
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
        queries = []
        for item in decision.follow_up_questions:
            safe = _truncate_utf8(sanitize_text(str(item)), 1000)
            if safe and safe not in queries:
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
    ) -> DeepResearchResult:
        if not isinstance(policy, PolicyContext):
            raise AppError(
                ErrorCode.UNAUTHORIZED_RESOURCE, "조사 소유자를 확인할 수 없습니다."
            )
        had_debug = hasattr(langchain, "debug")
        if not had_debug:
            langchain.debug = False
        try:
            async with asyncio.timeout(MAX_ELAPSED_SECONDS):
                state = await self.graph.ainvoke(
                    {
                        "question": _truncate_utf8(sanitize_text(question), 4000),
                        "policy": policy,
                        "filters": filters or RetrievalFilters(),
                    }
                )
        except TimeoutError:
            raise AppError(
                ErrorCode.BUDGET_EXCEEDED,
                "조사 시간 한도를 초과했습니다.",
            ) from None
        finally:
            if not had_debug and hasattr(langchain, "debug"):
                delattr(langchain, "debug")
        return DeepResearchResult(
            report=state["report"],
            evidence=state.get("evidence", []),
            completed_sub_questions=sum(
                not item.failed for item in state.get("branch_results", [])
            ),
            rounds=state["rounds"],
            disclosures=state.get("disclosures", []),
            citation_valid=state.get("citation_valid", False),
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
