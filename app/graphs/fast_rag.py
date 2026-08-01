import asyncio
import re
from time import perf_counter
from typing import Literal, TypedDict
import uuid

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from app.domain.chat import (
    BM25_FALLBACK_DISCLOSURE,
    ChatRequest,
    FastRAGResult,
    QualityStatus,
)
from app.domain.evidence import Evidence, SearchTask
from app.domain.policy import PolicyContext
from app.llm.prompts import (
    CONTEXTUALIZE_SYSTEM,
    GENERAL_SYSTEM,
    GENERATE_SYSTEM,
    GRADE_SYSTEM,
    PLAN_SYSTEM,
    REVISE_SYSTEM,
    REWRITE_SYSTEM,
)
from app.observability.tracing import TraceEvent, emit_trace, hash_trace_value
from app.security.citations import CitationValidator
from app.security.redaction import opaque_identifier, sanitize_text

MAX_SEARCHES = 6
MAX_REWRITES = 2
MAX_REVISIONS = 1
MAX_EVIDENCE = 8
MAX_EVIDENCE_TOKENS = 16_000
LIMITED_ANSWER = "확인 가능한 근거가 없어 답변할 수 없습니다."
INVALID_ANSWER = "근거로 확인된 내용만으로는 답변을 제공할 수 없습니다."
TIMEOUT_ANSWER = "답변 시간 한도를 초과해 확인된 답변을 제공할 수 없습니다."
INCOMPLETE_ANSWER_PREFIX = "제공된 근거가 불완전하여 확인된 범위만 답변합니다."


class RetrievalPlan(BaseModel):
    tasks: list[SearchTask] = Field(min_length=1, max_length=MAX_SEARCHES)


class EvidenceGrade(BaseModel):
    sufficient: bool
    missing_information: list[str] = Field(default_factory=list, max_length=6)


class ClaimSupportDecision(BaseModel):
    supported: bool
    unsupported_claims: list[str] = Field(default_factory=list, max_length=8)


class FastState(TypedDict, total=False):
    request: ChatRequest
    policy: PolicyContext
    conversation: object
    standalone_question: str
    tasks: list[SearchTask]
    evidence: list[Evidence]
    answer: str
    retrieval_mode: Literal["hybrid", "bm25"]
    sufficient: bool
    missing_information: list[str]
    searches: int
    rewrites: int
    revisions: int
    citation_valid: bool
    disclosures: list[str]


def _truncate_utf8(text: str, byte_limit: int) -> str:
    return text.encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")


def _evidence_context(evidence: list[Evidence]) -> str:
    """Bound context by UTF-8 bytes, a conservative upper bound on BPE tokens."""
    remaining = MAX_EVIDENCE_TOKENS
    sections = []
    for item in evidence[:MAX_EVIDENCE]:
        separator = "\n\n" if sections else ""
        separator_size = len(separator.encode("utf-8"))
        prefix = f"[{item.evidence_id}] {item.title}\n"
        prefix_size = len(prefix.encode("utf-8"))
        if separator_size + prefix_size >= remaining:
            break
        excerpt = _truncate_utf8(
            item.excerpt,
            remaining - separator_size - prefix_size,
        )
        section = separator + prefix + excerpt
        sections.append(section)
        remaining -= len(section.encode("utf-8"))
        if remaining <= 0:
            break
    return "".join(sections)


def _sanitize_evidence(item: Evidence) -> Evidence | None:
    excerpt = sanitize_text(item.excerpt)
    if not excerpt:
        return None
    updates = {
        "document_id": opaque_identifier(item.document_id),
        "parent_id": opaque_identifier(item.parent_id) if item.parent_id else None,
        "title": sanitize_text(item.title),
        "excerpt": excerpt,
        "team": sanitize_text(item.team) if item.team else None,
        "source_locator": (
            sanitize_text(item.source_locator) if item.source_locator else None
        ),
        "acl_decision_id": opaque_identifier(item.acl_decision_id),
        "content_hash": opaque_identifier(item.content_hash),
    }
    return Evidence.model_validate({**item.model_dump(), **updates})


def _safe_missing_information(state: FastState) -> list[str]:
    missing = []
    for item in state.get("missing_information", []):
        sanitized = sanitize_text(str(item))
        without_citations = re.sub(r"\[S\d+\]", "", sanitized).strip()
        if without_citations:
            missing.append(without_citations)
    return missing


def _with_limitation(answer: str, state: FastState) -> str:
    if state.get("sufficient", False):
        return answer
    if answer.startswith(INCOMPLETE_ANSWER_PREFIX):
        _, separator, answer = answer.partition("\n\n")
        if not separator:
            answer = ""
    limitation = INCOMPLETE_ANSWER_PREFIX
    missing = _safe_missing_information(state)
    if missing:
        limitation = f"{limitation}\n미확인 정보: {', '.join(missing)}"
    return f"{limitation}\n\n{answer}".rstrip()


class FastRAGWorkflow:
    def __init__(self, retrieval, llm, trace_sink=None, deadline_seconds: float = 20):
        self.retrieval = retrieval
        self.llm = llm
        self.validator = CitationValidator()
        self.trace_sink = trace_sink
        self.deadline_seconds = max(0.001, float(deadline_seconds))
        self.graph = self._build()

    def _build(self):
        graph = StateGraph(FastState)
        graph.add_node("contextualize", self._contextualize)
        graph.add_node("plan", self._plan)
        graph.add_node("retrieve", self._retrieve)
        graph.add_node("grade", self._grade)
        graph.add_node("rewrite", self._rewrite)
        graph.add_node("generate", self._generate)
        graph.add_node("validate", self._validate)
        graph.add_node("revise", self._revise)
        graph.add_edge(START, "contextualize")
        graph.add_edge("contextualize", "plan")
        graph.add_edge("plan", "retrieve")
        graph.add_edge("retrieve", "grade")
        graph.add_conditional_edges(
            "grade",
            self._after_grade,
            {"rewrite": "rewrite", "generate": "generate"},
        )
        graph.add_edge("rewrite", "retrieve")
        graph.add_edge("generate", "validate")
        graph.add_conditional_edges(
            "validate",
            self._after_validate,
            {"revise": "revise", "end": END},
        )
        graph.add_edge("revise", "validate")
        return graph.compile()

    async def _contextualize(self, state: FastState) -> dict:
        request = state["request"]
        memory = state.get("conversation")
        messages = getattr(memory, "messages", None)
        if not messages:
            return {
                "standalone_question": sanitize_text(request.message) or "메일 질문"
            }
        recent = "\n".join(
            f"{sanitize_text(str(item.get('role', '')))}: "
            f"{sanitize_text(str(item.get('content', '')))}"
            for item in messages[-6:]
            if isinstance(item, dict)
        )
        standalone = await self.llm.complete_text(
            CONTEXTUALIZE_SYSTEM,
            f"History:\n{recent}\nLatest:\n{sanitize_text(request.message)}",
        )
        return {
            "standalone_question": sanitize_text(standalone)
            or sanitize_text(request.message)
            or "메일 질문"
        }

    async def _plan(self, state: FastState) -> dict:
        request = state["request"]
        try:
            plan = await self.llm.complete_model(
                PLAN_SYSTEM,
                state["standalone_question"],
                RetrievalPlan,
            )
            planned_tasks = plan.tasks[:MAX_SEARCHES]
        except Exception:
            planned_tasks = [SearchTask(query=state["standalone_question"])]
        tasks = [
            SearchTask.model_validate(
                {
                    **task.model_dump(),
                    "query": sanitize_text(task.query) or "메일 질문",
                    "filters": request.filters.model_dump(),
                }
            )
            for task in planned_tasks
        ]
        return {
            "tasks": tasks,
            "evidence": [],
            "retrieval_mode": "hybrid",
            "searches": 0,
            "rewrites": 0,
            "revisions": 0,
            "disclosures": [],
        }

    async def _retrieve(self, state: FastState) -> dict:
        remaining = max(0, MAX_SEARCHES - state["searches"])
        tasks = state["tasks"][:remaining]
        results = await asyncio.gather(
            *(self.retrieval.search(task, state["policy"]) for task in tasks)
        )

        policy = state["policy"]
        previous = list(state.get("evidence", []))
        fresh: list[Evidence] = []
        mode = state.get("retrieval_mode", "hybrid")
        disclosures = list(state.get("disclosures", []))
        for result in results:
            for item in result.evidence:
                if item.user_id != policy.user_id:
                    continue
                sanitized = _sanitize_evidence(item)
                if sanitized:
                    fresh.append(sanitized)
            if result.mode == "bm25":
                mode = "bm25"
            if (
                result.embedding_error == "EMBEDDING_UNAVAILABLE"
                or BM25_FALLBACK_DISCLOSURE in result.disclosures
            ):
                if BM25_FALLBACK_DISCLOSURE not in disclosures:
                    disclosures.append(BM25_FALLBACK_DISCLOSURE)

        unique: dict[tuple[str, str], Evidence] = {}
        for item in [*fresh, *previous]:
            unique.setdefault((item.source_type, item.document_id), item)
        evidence = [
            item.model_copy(update={"evidence_id": f"S{index}"})
            for index, item in enumerate(list(unique.values())[:MAX_EVIDENCE], 1)
        ]
        return {
            "evidence": evidence,
            "retrieval_mode": mode,
            "searches": state["searches"] + len(tasks),
            "disclosures": disclosures,
        }

    async def _grade(self, state: FastState) -> dict:
        if not state["evidence"]:
            return {"sufficient": False, "missing_information": ["mail evidence"]}
        grade = await self.llm.complete_model(
            GRADE_SYSTEM,
            (
                f"Question: {state['standalone_question']}\nEvidence:\n"
                f"{_evidence_context(state['evidence'])}"
            ),
            EvidenceGrade,
        )
        return grade.model_dump()

    @staticmethod
    def _after_grade(state: FastState) -> Literal["rewrite", "generate"]:
        if (
            state["sufficient"]
            or state["rewrites"] >= MAX_REWRITES
            or state["searches"] >= MAX_SEARCHES
        ):
            return "generate"
        return "rewrite"

    async def _rewrite(self, state: FastState) -> dict:
        query = await self.llm.complete_text(
            REWRITE_SYSTEM,
            (
                f"Question: {state['standalone_question']}\nMissing: "
                f"{sanitize_text(str(state['missing_information']))}"
            ),
        )
        rewritten = (
            _truncate_utf8(sanitize_text(query), 1000) or state["standalone_question"]
        )
        tasks = [
            SearchTask.model_validate({**item.model_dump(), "query": rewritten})
            for item in state["tasks"]
        ]
        return {"tasks": tasks, "rewrites": state["rewrites"] + 1}

    async def _generate(self, state: FastState) -> dict:
        if not state["evidence"]:
            return {"answer": LIMITED_ANSWER}
        status = "complete" if state.get("sufficient", False) else "incomplete"
        missing = ", ".join(
            sanitize_text(str(item)) for item in state.get("missing_information", [])
        )
        answer = await self.llm.complete_text(
            GENERATE_SYSTEM,
            (
                f"Question: {sanitize_text(state['request'].message)}\n"
                f"Evidence status: {status}\nMissing information: {missing}\n"
                "Evidence:\n"
                f"{_evidence_context(state['evidence'])}"
            ),
        )
        return {"answer": sanitize_text(answer)}

    def _validate(self, state: FastState) -> dict:
        answer = _with_limitation(state["answer"], state)
        if not state["evidence"]:
            return {"answer": answer, "citation_valid": True}
        validation = self.validator.validate(
            answer,
            state["evidence"],
            state["policy"],
        )
        return {"answer": answer, "citation_valid": validation.valid}

    @staticmethod
    def _after_validate(state: FastState) -> Literal["revise", "end"]:
        if state["citation_valid"] or state["revisions"] >= MAX_REVISIONS:
            return "end"
        return "revise"

    async def _revise(self, state: FastState) -> dict:
        status = "complete" if state.get("sufficient", False) else "incomplete"
        missing = ", ".join(
            sanitize_text(str(item)) for item in state.get("missing_information", [])
        )
        answer = await self.llm.complete_text(
            REVISE_SYSTEM,
            (
                f"Draft: {state['answer']}\nEvidence status: {status}\n"
                f"Missing information: {missing}\nEvidence:\n"
                f"{_evidence_context(state['evidence'])}"
            ),
        )
        return {
            "answer": sanitize_text(answer),
            "revisions": state["revisions"] + 1,
        }

    async def invoke(
        self,
        request: ChatRequest,
        policy: PolicyContext,
        conversation: object | None,
    ) -> FastRAGResult:
        started = perf_counter()
        try:
            async with asyncio.timeout(self.deadline_seconds):
                result = await self._invoke(request, policy, conversation)
        except TimeoutError:
            result = FastRAGResult(
                answer=TIMEOUT_ANSWER,
                evidence=[],
                quality=QualityStatus(
                    citation_valid=False,
                    limited_answer=True,
                    retrieval_mode="hybrid",
                ),
            )
        except Exception as error:
            emit_trace(
                self.trace_sink,
                TraceEvent(
                    trace_id=uuid.uuid4().hex,
                    node_name="fast_rag.invoke",
                    duration_ms=int((perf_counter() - started) * 1000),
                    status="error",
                    index_version_hash=hash_trace_value("shared-retrieval"),
                    prompt_version_hash=hash_trace_value("fast-v1"),
                    model_hash=hash_trace_value(
                        str(getattr(self.llm, "model", "none"))
                    ),
                    owner_hash=hash_trace_value(policy.user_id),
                    query_hash=hash_trace_value(request.message),
                    route="fast",
                    error_class=type(error).__name__,
                ),
            )
            raise
        emit_trace(
            self.trace_sink,
            TraceEvent(
                trace_id=uuid.uuid4().hex,
                node_name="fast_rag.invoke",
                duration_ms=int((perf_counter() - started) * 1000),
                status="ok",
                index_version_hash=hash_trace_value("shared-retrieval"),
                prompt_version_hash=hash_trace_value("fast-v1"),
                model_hash=hash_trace_value(str(getattr(self.llm, "model", "none"))),
                owner_hash=hash_trace_value(policy.user_id),
                query_hash=hash_trace_value(request.message),
                document_hashes=[
                    hash_trace_value(item.document_id) for item in result.evidence
                ],
                evidence_count=len(result.evidence),
                retrieval_mode=result.quality.retrieval_mode,
                route="fast",
            ),
        )
        return result

    async def _invoke(
        self,
        request: ChatRequest,
        policy: PolicyContext,
        conversation: object | None,
    ) -> FastRAGResult:
        if request.user_id != policy.user_id:
            raise ValueError("request owner and policy owner must match exactly")
        try:
            async with asyncio.timeout(self.deadline_seconds):
                state = await self.graph.ainvoke(
                    {
                        "request": request,
                        "policy": policy,
                        "conversation": conversation,
                    }
                )
        except TimeoutError:
            return FastRAGResult(
                answer=TIMEOUT_ANSWER,
                evidence=[],
                quality=QualityStatus(
                    citation_valid=False, limited_answer=True, retrieval_mode="hybrid"
                ),
            )
        evidence = state.get("evidence", [])
        answer = sanitize_text(state["answer"])
        disclosures = state.get("disclosures", [])
        if disclosures:
            answer = f"{answer}\n\n" + "\n".join(disclosures)
        citation_valid = (
            self.validator.validate(answer, evidence, policy).valid
            if evidence
            else True
        )
        support_valid = citation_valid
        if evidence and citation_valid:
            try:
                support = await self.llm.complete_model(
                    "Check each factual claim against the supplied evidence. "
                    "Citations alone are not proof. Return supported=false for any "
                    "claim not entailed by the evidence.",
                    f"Draft:\n{answer}\nEvidence:\n{_evidence_context(evidence)}",
                    ClaimSupportDecision,
                )
                support_valid = support.supported
            except Exception:
                support_valid = False
            if not support_valid:
                answer = _with_limitation(INVALID_ANSWER, state)
                if disclosures:
                    answer = f"{answer}\n\n" + "\n".join(disclosures)
                citation_valid = False
        limited = (
            not evidence or not citation_valid or not state.get("sufficient", False)
        )
        if evidence and not citation_valid:
            answer = _with_limitation(INVALID_ANSWER, state)
            if disclosures:
                answer = f"{answer}\n\n" + "\n".join(disclosures)
        return FastRAGResult(
            answer=answer,
            evidence=evidence,
            quality=QualityStatus(
                citation_valid=citation_valid,
                limited_answer=limited,
                retrieval_mode=state.get("retrieval_mode", "hybrid"),
            ),
            disclosures=disclosures,
        )

    async def respond_general(self, request: ChatRequest) -> FastRAGResult:
        started = perf_counter()
        try:
            answer = await self.llm.complete_text(
                GENERAL_SYSTEM,
                sanitize_text(request.message),
            )
            result = FastRAGResult(
                answer=sanitize_text(answer),
                evidence=[],
                quality=QualityStatus(citation_valid=True, limited_answer=False),
            )
        except Exception as error:
            emit_trace(
                self.trace_sink,
                TraceEvent(
                    trace_id=uuid.uuid4().hex,
                    node_name="fast_rag.general",
                    duration_ms=int((perf_counter() - started) * 1000),
                    status="error",
                    index_version_hash=hash_trace_value("none"),
                    prompt_version_hash=hash_trace_value("general-v1"),
                    model_hash=hash_trace_value(
                        str(getattr(self.llm, "model", "none"))
                    ),
                    owner_hash=hash_trace_value(request.user_id),
                    query_hash=hash_trace_value(request.message),
                    route="general",
                    error_class=type(error).__name__,
                ),
            )
            raise
        emit_trace(
            self.trace_sink,
            TraceEvent(
                trace_id=uuid.uuid4().hex,
                node_name="fast_rag.general",
                duration_ms=int((perf_counter() - started) * 1000),
                status="ok",
                index_version_hash=hash_trace_value("none"),
                prompt_version_hash=hash_trace_value("general-v1"),
                model_hash=hash_trace_value(str(getattr(self.llm, "model", "none"))),
                owner_hash=hash_trace_value(request.user_id),
                query_hash=hash_trace_value(request.message),
                route="general",
            ),
        )
        return result
