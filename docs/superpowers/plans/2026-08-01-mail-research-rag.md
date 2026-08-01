# Mail Research RAG Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a new owner-filtered mail chatbot with synchronous Fast RAG and persistent asynchronous Deep Agent research over the existing OpenSearch embeddings.

**Architecture:** A new `app/` package owns typed domain contracts, the only OpenSearch retrieval boundary, two bounded LangGraph workflows, Mongo-backed conversation/research persistence, and FastAPI routes. Vector and BM25 rankings are fused with RRF, legacy chunks are expanded by mail/part identity until parent/child v2 aliases are available, and every lookup plus final citation is scoped to request-body `user_id`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, LangGraph, OpenSearch 2.x, AsyncOpenAI, Motor/MongoDB, pytest

## Global Constraints

- Do not import or call `rag_api_opensearch_v3.py` from the new application.
- `user_id` comes from every relevant request body; the application does not authenticate it and deployment trusts an upstream gateway.
- Every mail, Wiki, parent, statistic, evidence-restore, conversation, and research-job access is fail-closed on exact `user_id` ownership.
- `team` is a search facet, never an authorization boundary.
- Documents without `user_id` remain invisible until explicit backfill.
- Fast RAG limits: six search tasks, two rewrites, one answer revision, eight evidence objects, and 16,000 context tokens.
- Deep Agent has finite sub-question, breadth, depth, concurrency, token, elapsed-time, and evidence budgets.
- When embeddings fail, continue with BM25 and include exactly: `임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다.`
- Never return unsupported or differently owned citations, raw filesystem paths, credentials, or chain-of-thought.
- Owner leakage greater than zero is a release failure.

---

## File Structure

```text
app/
├── __init__.py
├── api/
│   ├── dependencies.py
│   ├── main.py
│   └── routes/{chat,health,research}.py
├── config/{__init__,settings}.py
├── domain/{__init__,chat,errors,evidence,policy,research}.py
├── graphs/{__init__,deep_research,fast_rag,router}.py
├── llm/{__init__,gateway,prompts}.py
├── observability/{__init__,tracing}.py
├── persistence/{__init__,conversations,research_jobs}.py
├── retrieval/{__init__,embedding,filters,fusion,opensearch,service}.py
├── security/{__init__,citations}.py
└── workers/{__init__,research}.py
index_templates/{weekly_mail_child_v2,weekly_mail_parent_v2,wiki_summaries_v2}.json
evals/datasets/mail_rag_gold.json
scripts/{backfill_parent_child,backfill_user_id,evaluate_mail_rag,shadow_retrieval}.py
tests/mail_rag/
docs/{api,operations}.md
```

Files under each package `__init__.py` only mark the package and export no service singletons. Dependency construction happens in `app/api/dependencies.py`, which keeps tests deterministic.

---

### Task 1: Domain contracts and settings

**Files:**
- Create: `app/__init__.py`
- Create: `app/config/__init__.py`
- Create: `app/config/settings.py`
- Create: `app/domain/__init__.py`
- Create: `app/domain/errors.py`
- Create: `app/domain/policy.py`
- Create: `app/domain/evidence.py`
- Create: `app/domain/chat.py`
- Create: `app/domain/research.py`
- Test: `tests/mail_rag/test_domain_contracts.py`

**Interfaces:**
- Produces: `Settings.from_env() -> Settings`
- Produces: `PolicyContext.from_user_id(user_id: str) -> PolicyContext`
- Produces: `Evidence`, `RetrievalFilters`, `SearchTask`, `ChatRequest`, `ChatResponse`, `RouteDecision`, `ResearchJob`
- Produces: `AppError(code: ErrorCode, message: str, retryable: bool = False)`

- [ ] **Step 1: Write failing validation and serialization tests**

```python
# tests/mail_rag/test_domain_contracts.py
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.domain.chat import ChatRequest
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.domain.research import ResearchJob, ResearchStatus


def test_policy_context_normalizes_bounded_request_owner():
    policy = PolicyContext.from_user_id("  kim.dh  ")
    assert policy.user_id == "kim.dh"
    assert len(policy.decision_id) == 32


@pytest.mark.parametrize("value", ["", "   ", "a" * 129, "kim/dh", "kim dh"])
def test_policy_context_rejects_invalid_owner(value):
    with pytest.raises(ValidationError):
        PolicyContext.from_user_id(value)


def test_chat_request_keeps_user_id_in_body_and_normalizes_weeks():
    request = ChatRequest(user_id="kim", message="최근 이슈", filters={"weeks": ["2026-8"]})
    assert request.user_id == "kim"
    assert request.filters.weeks == ["2026-08"]


def test_evidence_requires_owner_and_bounded_excerpt():
    with pytest.raises(ValidationError):
        Evidence(
            evidence_id="S1", source_type="mail", document_id="doc-1",
            title="title", excerpt="x" * 8001, score=1.0,
            user_id="kim", acl_decision_id="d1", content_hash="h1",
        )


def test_research_job_serializes_utc_state():
    job = ResearchJob(
        job_id="research-1", user_id="kim", trace_id="trace-1",
        question="12주 추세", status=ResearchStatus.QUEUED,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    )
    assert job.model_dump(mode="json")["status"] == "queued"
```

- [ ] **Step 2: Run tests and verify missing-package failure**

Run: `python -m pytest tests/mail_rag/test_domain_contracts.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 3: Implement strict Pydantic contracts and environment settings**

```python
# app/domain/policy.py
from hashlib import sha256
from pydantic import BaseModel, ConfigDict, Field


class PolicyContext(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")
    user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.@+-]+$")
    decision_id: str = Field(min_length=32, max_length=32)

    @classmethod
    def from_user_id(cls, user_id: str) -> "PolicyContext":
        normalized = user_id.strip()
        decision = sha256(f"owner:{normalized}".encode()).hexdigest()[:32]
        return cls(user_id=normalized, decision_id=decision)
```

```python
# app/domain/evidence.py
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    teams: list[str] = Field(default_factory=list, max_length=10)
    weeks: list[str] = Field(default_factory=list, max_length=52)
    mail_type: Literal["weekly_report", "daily_report", "other"] | None = None

    @field_validator("weeks")
    @classmethod
    def normalize_weeks(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            year, separator, week = value.strip().partition("-")
            if separator != "-" or not year.isdigit() or not week.isdigit() or not 1 <= int(week) <= 53:
                raise ValueError("weeks must contain YYYY-WW values")
            item = f"{int(year):04d}-{int(week):02d}"
            if item not in normalized:
                normalized.append(item)
        return normalized


class SearchTask(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    source: Literal["mail", "wiki", "statistics"] = "mail"
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    top_k: int = Field(default=8, ge=1, le=20)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    evidence_id: str = Field(min_length=1, max_length=32)
    source_type: Literal["mail", "wiki", "statistic"]
    document_id: str = Field(min_length=1, max_length=256)
    parent_id: str | None = Field(default=None, max_length=256)
    title: str = Field(default="", max_length=500)
    excerpt: str = Field(min_length=1, max_length=8000)
    team: str | None = Field(default=None, max_length=100)
    week: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    source_locator: str | None = Field(default=None, max_length=1000)
    score: float
    rerank_score: float | None = None
    user_id: str = Field(min_length=1, max_length=128)
    acl_decision_id: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(min_length=1, max_length=128)
```

```python
# app/domain/chat.py
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from app.domain.evidence import Evidence, RetrievalFilters

BM25_FALLBACK_DISCLOSURE = "임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다."

class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    user_id: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=128)
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    response_mode: Literal["auto", "fast", "deep"] = "auto"

class RouteDecision(BaseModel):
    route: Literal["fast", "deep", "clarify", "general"]
    reason_code: str
    confidence: float = Field(ge=0, le=1)
    estimated_searches: int = Field(ge=0, le=24)
    clarification_question: str | None = None
    requested_output: Literal["answer", "table", "report", "presentation"] = "answer"

class QualityStatus(BaseModel):
    citation_valid: bool
    limited_answer: bool = False
    retrieval_mode: Literal["hybrid", "bm25"] = "hybrid"

class FastRAGResult(BaseModel):
    answer: str
    evidence: list[Evidence] = Field(default_factory=list)
    quality: QualityStatus
    disclosures: list[str] = Field(default_factory=list)

class ChatResponse(BaseModel):
    conversation_id: str
    mode: Literal["fast_rag", "deep_research"]
    answer: str | None = None
    references: list[Evidence] = Field(default_factory=list)
    quality: QualityStatus | None = None
    disclosures: list[str] = Field(default_factory=list)
    trace_id: str
    job_id: str | None = None
    status: str | None = None
    plan_summary: str | None = None
```

```python
# app/domain/research.py
from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, Field

class ResearchStatus(StrEnum):
    QUEUED="queued"; RUNNING="running"; COMPLETED="completed"; FAILED="failed"; CANCELLED="cancelled"; CANCELLING="cancelling"

class ResearchJob(BaseModel):
    job_id: str; user_id: str; trace_id: str; question: str
    status: ResearchStatus; created_at: datetime; updated_at: datetime
    plan_summary: str = ""; progress: int = Field(default=0, ge=0, le=100)
    attempts: int = Field(default=0, ge=0); result_markdown: str | None = None
    error_code: str | None = None; lease_until: datetime | None = None
```

```python
# app/domain/errors.py
from enum import StrEnum

class ErrorCode(StrEnum):
    INVALID_USER_ID="INVALID_USER_ID"; UNAUTHORIZED_RESOURCE="UNAUTHORIZED_RESOURCE"
    INDEX_UNAVAILABLE="INDEX_UNAVAILABLE"; EMBEDDING_UNAVAILABLE="EMBEDDING_UNAVAILABLE"
    RETRIEVAL_TIMEOUT="RETRIEVAL_TIMEOUT"; NO_EVIDENCE="NO_EVIDENCE"
    BUDGET_EXCEEDED="BUDGET_EXCEEDED"; JOB_CANCELLED="JOB_CANCELLED"

class AppError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str, retryable: bool=False):
        super().__init__(message); self.code=code; self.message=message; self.retryable=retryable
```

```python
# app/config/settings.py
from functools import lru_cache
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    opensearch_host: str = "localhost"; opensearch_port: int = 9200
    opensearch_user: str = ""; opensearch_password: SecretStr = SecretStr("")
    opensearch_use_ssl: bool = False; opensearch_verify_certs: bool = True
    mail_child_index: str = "weekly_mail"; mail_parent_index: str = "weekly_mail_parent_read"
    wiki_index: str = "wiki_summaries_v2"; embedding_model: str = "qwen/qwen3-embedding-8b"
    llm_model: str = "gpt-oss-120b"; openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = ""; mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "weekly_mail_agent"; fast_deadline_seconds: int = Field(default=20, ge=1, le=120)

    @classmethod
    def from_env(cls) -> "Settings": return cls()

@lru_cache
def get_settings() -> Settings: return Settings.from_env()
```

- [ ] **Step 4: Run contract tests**

Run: `python -m pytest tests/mail_rag/test_domain_contracts.py -q`

Expected: PASS with 9 tests.

- [ ] **Step 5: Commit the contracts**

```bash
git add app tests/mail_rag/test_domain_contracts.py
git commit -m "feat(rag): add typed domain contracts"
```

---

### Task 2: Owner filtering, RRF, and citation validation

**Files:**
- Create: `app/retrieval/__init__.py`
- Create: `app/retrieval/filters.py`
- Create: `app/retrieval/fusion.py`
- Create: `app/security/__init__.py`
- Create: `app/security/citations.py`
- Test: `tests/mail_rag/test_retrieval_primitives.py`

**Interfaces:**
- Consumes: `PolicyContext`, `RetrievalFilters`, `Evidence`
- Produces: `build_owner_filters(policy, filters) -> list[dict]`
- Produces: `reciprocal_rank_fusion(rankings, k=60) -> list[FusedHit]`
- Produces: `CitationValidator.validate(answer, evidence, policy) -> CitationValidation`

- [ ] **Step 1: Write failing security and fusion tests**

```python
# tests/mail_rag/test_retrieval_primitives.py
import pytest
from app.domain.evidence import Evidence, RetrievalFilters
from app.domain.policy import PolicyContext
from app.retrieval.filters import build_owner_filters
from app.retrieval.fusion import RankedHit, reciprocal_rank_fusion
from app.security.citations import CitationValidator

def test_owner_filter_is_first_and_cannot_be_omitted():
    policy = PolicyContext.from_user_id("kim")
    filters = build_owner_filters(policy, RetrievalFilters(teams=["YIELD팀"], weeks=["2026-08"]))
    assert filters[0] == {"term": {"user_id": "kim"}}
    assert {"terms": {"team": ["YIELD팀"]}} in filters
    assert {"terms": {"week": ["2026-08"]}} in filters

def test_rrf_combines_rankings_without_raw_score_addition():
    vector = [RankedHit(document_id="a", rank=1, raw={}), RankedHit(document_id="b", rank=2, raw={})]
    bm25 = [RankedHit(document_id="b", rank=1, raw={}), RankedHit(document_id="c", rank=2, raw={})]
    result = reciprocal_rank_fusion([vector, bm25], k=60)
    assert [item.document_id for item in result] == ["b", "a", "c"]

def test_citation_validator_rejects_unknown_and_cross_owner_sources():
    evidence = [Evidence(evidence_id="S1", source_type="mail", document_id="d1", title="t", excerpt="fact", score=1, user_id="lee", acl_decision_id="x", content_hash="h")]
    result = CitationValidator().validate("사실입니다 [S1] [S9]", evidence, PolicyContext.from_user_id("kim"))
    assert result.valid is False
    assert result.unknown_ids == ["S9"]
    assert result.unauthorized_ids == ["S1"]
```

- [ ] **Step 2: Run tests and confirm import failures**

Run: `python -m pytest tests/mail_rag/test_retrieval_primitives.py -q`

Expected: FAIL because `app.retrieval.filters` does not exist.

- [ ] **Step 3: Implement fail-closed filters, deterministic RRF, and citation parsing**

```python
# app/retrieval/filters.py
from app.domain.evidence import RetrievalFilters
from app.domain.policy import PolicyContext

def build_owner_filters(policy: PolicyContext, facets: RetrievalFilters) -> list[dict]:
    filters: list[dict] = [{"term": {"user_id": policy.user_id}}]
    if facets.teams: filters.append({"terms": {"team": facets.teams}})
    if facets.weeks: filters.append({"terms": {"week": facets.weeks}})
    if facets.mail_type: filters.append({"term": {"mail_type": facets.mail_type}})
    return filters
```

```python
# app/retrieval/fusion.py
from dataclasses import dataclass
from typing import Any, Sequence

@dataclass(frozen=True)
class RankedHit: document_id: str; rank: int; raw: dict[str, Any]
@dataclass(frozen=True)
class FusedHit: document_id: str; score: float; raw: dict[str, Any]

def reciprocal_rank_fusion(rankings: Sequence[Sequence[RankedHit]], k: int=60) -> list[FusedHit]:
    scores: dict[str, float] = {}; raw: dict[str, dict] = {}
    for ranking in rankings:
        for hit in ranking:
            scores[hit.document_id] = scores.get(hit.document_id, 0.0) + 1.0 / (k + hit.rank)
            raw.setdefault(hit.document_id, hit.raw)
    return [FusedHit(key, score, raw[key]) for key, score in sorted(scores.items(), key=lambda item: (-item[1], item[0]))]
```

```python
# app/security/citations.py
import re
from pydantic import BaseModel, Field
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext

class CitationValidation(BaseModel):
    valid: bool; cited_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list); unauthorized_ids: list[str] = Field(default_factory=list)

class CitationValidator:
    def validate(self, answer: str, evidence: list[Evidence], policy: PolicyContext) -> CitationValidation:
        cited = list(dict.fromkeys(re.findall(r"\[(S\d+)\]", answer)))
        by_id = {item.evidence_id: item for item in evidence}
        unknown = [item for item in cited if item not in by_id]
        unauthorized = [item for item in cited if item in by_id and by_id[item].user_id != policy.user_id]
        return CitationValidation(valid=bool(cited) and not unknown and not unauthorized, cited_ids=cited, unknown_ids=unknown, unauthorized_ids=unauthorized)
```

- [ ] **Step 4: Run primitive tests**

Run: `python -m pytest tests/mail_rag/test_retrieval_primitives.py -q`

Expected: PASS with 3 tests.

- [ ] **Step 5: Commit retrieval primitives**

```bash
git add app/retrieval app/security tests/mail_rag/test_retrieval_primitives.py
git commit -m "feat(rag): enforce owner-scoped evidence"
```

---

### Task 3: OpenSearch hybrid retrieval and legacy parent expansion

**Files:**
- Create: `app/retrieval/embedding.py`
- Create: `app/retrieval/opensearch.py`
- Create: `app/retrieval/service.py`
- Test: `tests/mail_rag/test_retrieval_service.py`

**Interfaces:**
- Produces: `EmbeddingGateway.embed(text: str) -> list[float]`
- Produces: `OpenSearchGateway.search(index, body) -> dict`
- Produces: `RetrievalService.search(task, policy) -> RetrievalResult`
- `RetrievalResult.mode` is `hybrid` or `bm25`; `embedding_error` records fallback without exposing secrets.

- [ ] **Step 1: Write failing query-body, fallback, and cross-owner tests**

```python
# tests/mail_rag/test_retrieval_service.py
import pytest
from app.domain.evidence import RetrievalFilters, SearchTask
from app.domain.policy import PolicyContext
from app.retrieval.service import RetrievalService

class FakeSearch:
    def __init__(self): self.calls=[]
    async def search(self, index, body):
        self.calls.append((index, body))
        return {"hits":{"hits":[{"_id":"d1","_score":9,"_source":{"text":"근거","user_id":"kim","mail_id":"m1","part_index":0,"team":"YIELD팀","week":"2026-08"}}]}}

class FakeEmbedding:
    async def embed(self, text): return [0.1, 0.2]

class BrokenEmbedding:
    async def embed(self, text): raise TimeoutError("embedding timeout")

@pytest.mark.asyncio
async def test_hybrid_queries_both_contain_owner_filter():
    backend=FakeSearch(); service=RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")
    result=await service.search(SearchTask(query="수율", filters=RetrievalFilters()), PolicyContext.from_user_id("kim"))
    assert result.mode == "hybrid"
    assert len(backend.calls) == 3
    for _, body in backend.calls[:2]: assert {"term":{"user_id":"kim"}} in body["query"]["bool"]["filter"]
    assert all(item.user_id == "kim" for item in result.evidence)

@pytest.mark.asyncio
async def test_embedding_failure_runs_bm25_only():
    backend=FakeSearch(); service=RetrievalService(backend, BrokenEmbedding(), child_index="weekly_mail")
    result=await service.search(SearchTask(query="수율"), PolicyContext.from_user_id("kim"))
    assert result.mode == "bm25" and result.embedding_error == "EMBEDDING_UNAVAILABLE"
    assert len(backend.calls) == 2
```

- [ ] **Step 2: Run tests and verify missing service failure**

Run: `python -m pytest tests/mail_rag/test_retrieval_service.py -q`

Expected: FAIL because `RetrievalService` is not defined.

- [ ] **Step 3: Implement async gateways and the retrieval pipeline**

```python
# app/retrieval/opensearch.py
import asyncio
from typing import Protocol
from opensearchpy import OpenSearch

class OpenSearchGateway(Protocol):
    async def search(self, index: str, body: dict) -> dict: ...

class AsyncOpenSearchGateway:
    def __init__(self, client: OpenSearch): self.client=client
    async def search(self, index: str, body: dict) -> dict:
        return await asyncio.to_thread(self.client.search, index=index, body=body)
```

```python
# app/retrieval/embedding.py
from typing import Protocol
from openai import AsyncOpenAI

class EmbeddingGateway(Protocol):
    async def embed(self, text: str) -> list[float]: ...

class OpenAIEmbeddingGateway:
    def __init__(self, client: AsyncOpenAI, model: str): self.client=client; self.model=model
    async def embed(self, text: str) -> list[float]:
        response=await self.client.embeddings.create(model=self.model, input=text[:8000])
        return list(response.data[0].embedding)
```

```python
# app/retrieval/service.py
from hashlib import sha256
from typing import Literal
from pydantic import BaseModel, Field
from app.domain.evidence import Evidence, SearchTask
from app.domain.policy import PolicyContext
from app.retrieval.filters import build_owner_filters
from app.retrieval.fusion import RankedHit, reciprocal_rank_fusion

class RetrievalResult(BaseModel):
    evidence: list[Evidence] = Field(default_factory=list)
    mode: Literal["hybrid","bm25"]="hybrid"; embedding_error: str|None=None

class RetrievalService:
    def __init__(self, search, embeddings, child_index: str, parent_index: str|None=None, wiki_index:str="wiki_summaries_v2"):
        self.backend=search; self.embeddings=embeddings; self.child_index=child_index; self.parent_index=parent_index; self.wiki_index=wiki_index
    async def search(self, task: SearchTask, policy: PolicyContext) -> RetrievalResult:
        filters=build_owner_filters(policy, task.filters)
        if task.source=="statistics": return await self._statistics(filters,policy)
        index_name=self.wiki_index if task.source=="wiki" else self.child_index
        bm25={"size":task.top_k*3,"query":{"bool":{"filter":filters,"must":[{"match":{"text":{"query":task.query,"analyzer":"korean"}}}]}}}
        rankings=[]; embedding_error=None
        try:
            vector=await self.embeddings.embed(task.query)
            knn={"size":task.top_k*3,"query":{"bool":{"filter":filters,"must":[{"knn":{"embedding":{"vector":vector,"k":task.top_k*3}}}]}}}
            import asyncio
            vector_response,bm25_response=await asyncio.gather(self.backend.search(index_name,knn),self.backend.search(index_name,bm25))
            rankings.append(self._rank(vector_response)); rankings.append(self._rank(bm25_response))
            mode="hybrid"
        except Exception:
            bm25_response=await self.backend.search(index_name,bm25)
            rankings.append(self._rank(bm25_response)); mode="bm25"; embedding_error="EMBEDDING_UNAVAILABLE"
        fused=reciprocal_rank_fusion(rankings)
        raw_hits=[item.raw for item in fused[:task.top_k]]
        expanded=await self._expand_legacy(raw_hits, policy, task) if task.source=="mail" else raw_hits
        evidence=[]
        for index, hit in enumerate(expanded[:task.top_k],1):
            source=hit.get("_source",{}); text=str(source.get("text") or source.get("content") or "")
            if source.get("user_id") != policy.user_id or not text: continue
            evidence.append(Evidence(evidence_id=f"S{index}",source_type="wiki" if task.source=="wiki" else "mail",document_id=str(hit.get("_id")),parent_id=source.get("parent_id"),title=str(source.get("subject") or source.get("title") or ""),excerpt=text[:8000],team=source.get("team"),week=source.get("week"),source_locator=source.get("document_locator"),score=float(hit.get("_rrf_score",hit.get("_score",0))),user_id=policy.user_id,acl_decision_id=policy.decision_id,content_hash=str(source.get("content_hash") or sha256(text.encode()).hexdigest())))
        return RetrievalResult(evidence=evidence,mode=mode,embedding_error=embedding_error)
    def _rank(self,response):
        return [RankedHit(str(hit["_id"]),rank,hit) for rank,hit in enumerate(response.get("hits",{}).get("hits",[]),1)]
    async def _expand_legacy(self,hits,policy,task):
        if not hits: return []
        mail_ids=list(dict.fromkeys(str(hit.get("_source",{}).get("mail_id")) for hit in hits if hit.get("_source",{}).get("mail_id")))
        if not mail_ids: return hits
        filters=build_owner_filters(policy,task.filters)+[{"terms":{"mail_id":mail_ids}}]
        body={"size":min(100,len(mail_ids)*6),"sort":[{"mail_id":"asc"},{"part_index":"asc"}],"query":{"bool":{"filter":filters}}}
        response=await self.backend.search(self.child_index,body)
        by_mail={}
        for hit in response.get("hits",{}).get("hits",[]): by_mail.setdefault(hit.get("_source",{}).get("mail_id"),[]).append(hit)
        return [max(parts,key=lambda item:len(str(item.get("_source",{}).get("text") or ""))) for parts in by_mail.values()] or hits
    async def _statistics(self,filters,policy):
        body={"size":0,"query":{"bool":{"filter":filters}},"aggs":{"by_team":{"terms":{"field":"team","size":100}},"by_mail_type":{"terms":{"field":"mail_type","size":10}}}}
        response=await self.backend.search(self.child_index,body)
        import json
        excerpt=json.dumps(response.get("aggregations",{}),ensure_ascii=False,sort_keys=True)
        evidence=Evidence(evidence_id="S1",source_type="statistic",document_id=f"statistics:{policy.decision_id}",title="메일 통계",excerpt=excerpt or "{}",score=1,user_id=policy.user_id,acl_decision_id=policy.decision_id,content_hash=sha256(excerpt.encode()).hexdigest())
        return RetrievalResult(evidence=[evidence],mode="bm25")
```

- [ ] **Step 4: Run retrieval service tests**

Run: `python -m pytest tests/mail_rag/test_retrieval_service.py -q`

Expected: PASS with 2 tests and every recorded query owner-scoped.

- [ ] **Step 5: Commit the shared retrieval service**

```bash
git add app/retrieval tests/mail_rag/test_retrieval_service.py
git commit -m "feat(rag): add hybrid owner retrieval"
```

---

### Task 4: LLM gateway, router, and Fast RAG graph

**Files:**
- Create: `app/llm/__init__.py`
- Create: `app/llm/gateway.py`
- Create: `app/llm/prompts.py`
- Create: `app/graphs/__init__.py`
- Create: `app/graphs/router.py`
- Create: `app/graphs/fast_rag.py`
- Test: `tests/mail_rag/test_router.py`
- Test: `tests/mail_rag/test_fast_rag.py`

**Interfaces:**
- Produces: `LLMGateway.complete_text(system, user) -> str`
- Produces: `LLMGateway.complete_model(system, user, schema) -> BaseModel`
- Produces: `route_request(request, llm) -> RouteDecision`
- Produces: `FastRAGWorkflow.invoke(request, policy, conversation) -> FastRAGResult`

- [ ] **Step 1: Write router override and Fast RAG fallback tests**

```python
# tests/mail_rag/test_router.py
import pytest
from app.domain.chat import ChatRequest, RouteDecision
from app.graphs.router import route_request

class FastBiasedLLM:
    async def complete_model(self, system, user, schema):
        return RouteDecision(route="fast",reason_code="model_fast",confidence=.9,estimated_searches=1)

@pytest.mark.asyncio
async def test_four_week_trend_overrides_model_to_deep():
    request=ChatRequest(user_id="kim",message="4주 추세 보고서",filters={"weeks":["2026-05","2026-06","2026-07","2026-08"]})
    decision=await route_request(request,FastBiasedLLM())
    assert decision.route == "deep" and decision.reason_code == "long_period"
```

```python
# tests/mail_rag/test_fast_rag.py
import pytest
from app.domain.chat import BM25_FALLBACK_DISCLOSURE, ChatRequest
from app.domain.policy import PolicyContext
from app.graphs.fast_rag import FastRAGWorkflow
from app.retrieval.service import RetrievalResult

class BM25Retrieval:
    async def search(self,task,policy): return RetrievalResult(mode="bm25",embedding_error="EMBEDDING_UNAVAILABLE",evidence=[])
class LimitedLLM:
    async def complete_model(self,system,user,schema):
        if schema.__name__=="RetrievalPlan": return schema.model_validate({"tasks":[{"query":"수율","source":"mail"}]})
        return schema.model_validate({"sufficient":False,"missing_information":["mail evidence"]})
    async def complete_text(self,system,user): return "확인 가능한 근거가 없습니다."

@pytest.mark.asyncio
async def test_fast_rag_includes_embedding_fallback_disclosure():
    result=await FastRAGWorkflow(BM25Retrieval(),LimitedLLM()).invoke(ChatRequest(user_id="kim",message="수율"),PolicyContext.from_user_id("kim"),None)
    assert BM25_FALLBACK_DISCLOSURE in result.answer
    assert result.quality.retrieval_mode == "bm25"
```

- [ ] **Step 2: Run tests and verify missing graph failure**

Run: `python -m pytest tests/mail_rag/test_router.py tests/mail_rag/test_fast_rag.py -q`

Expected: FAIL because router and workflow modules do not exist.

- [ ] **Step 3: Implement JSON-bound LLM gateway and deterministic routing**

```python
# app/llm/gateway.py
import json
from typing import Protocol, TypeVar
from openai import AsyncOpenAI
from pydantic import BaseModel
T=TypeVar("T",bound=BaseModel)
class LLMGateway(Protocol):
    async def complete_text(self,system:str,user:str)->str: ...
    async def complete_model(self,system:str,user:str,schema:type[T])->T: ...
class OpenAILLMGateway:
    def __init__(self,client:AsyncOpenAI,model:str): self.client=client; self.model=model
    async def complete_text(self,system,user):
        result=await self.client.chat.completions.create(model=self.model,messages=[{"role":"system","content":system},{"role":"user","content":user}],temperature=0)
        return str(result.choices[0].message.content or "")
    async def complete_model(self,system,user,schema):
        text=await self.complete_text(system+"\nReturn one JSON object matching this schema:\n"+json.dumps(schema.model_json_schema(),ensure_ascii=False),user)
        return schema.model_validate_json(text)
```

```python
# app/graphs/router.py
from app.domain.chat import ChatRequest,RouteDecision
async def route_request(request:ChatRequest,llm)->RouteDecision:
    if request.response_mode in {"fast","deep"}: return RouteDecision(route=request.response_mode,reason_code="explicit_mode",confidence=1,estimated_searches=1 if request.response_mode=="fast" else 6)
    try: output=await llm.complete_model("Classify a mail question as fast, deep, clarify, or general.",request.message,RouteDecision)
    except Exception: output=RouteDecision(route="fast",reason_code="router_fallback",confidence=0,estimated_searches=1)
    text=request.message.lower()
    if len(request.filters.weeks)>=4: return output.model_copy(update={"route":"deep","reason_code":"long_period","estimated_searches":max(4,output.estimated_searches)})
    if len(request.filters.teams)>=3: return output.model_copy(update={"route":"deep","reason_code":"multi_team","estimated_searches":max(3,output.estimated_searches)})
    if any(word in text for word in ("보고서","ppt","추세","원인과 조치","종합 분석")): return output.model_copy(update={"route":"deep","reason_code":"research_output","estimated_searches":max(4,output.estimated_searches)})
    return output
```

- [ ] **Step 4: Implement a bounded LangGraph Fast RAG workflow**

```python
# app/graphs/fast_rag.py
from typing import TypedDict
from pydantic import BaseModel,Field
from langgraph.graph import END,START,StateGraph
from app.domain.chat import BM25_FALLBACK_DISCLOSURE,ChatRequest,FastRAGResult,QualityStatus
from app.domain.evidence import Evidence,SearchTask
from app.domain.policy import PolicyContext
from app.security.citations import CitationValidator

class RetrievalPlan(BaseModel): tasks:list[SearchTask]=Field(min_length=1,max_length=6)
class EvidenceGrade(BaseModel): sufficient:bool; missing_information:list[str]=Field(default_factory=list,max_length=6)
class FastState(TypedDict,total=False):
    request:ChatRequest; policy:PolicyContext; conversation:object; standalone_question:str; tasks:list[SearchTask]; evidence:list[Evidence]
    answer:str; mode:str; sufficient:bool; missing_information:list[str]; rewrites:int; revisions:int; citation_valid:bool; disclosures:list[str]

class FastRAGWorkflow:
    def __init__(self,retrieval,llm):
        self.retrieval=retrieval; self.llm=llm; self.validator=CitationValidator(); self.graph=self._build()
    def _build(self):
        graph=StateGraph(FastState)
        graph.add_node("contextualize",self._contextualize); graph.add_node("plan",self._plan); graph.add_node("retrieve",self._retrieve); graph.add_node("grade",self._grade); graph.add_node("rewrite",self._rewrite); graph.add_node("generate",self._generate); graph.add_node("revise",self._revise); graph.add_node("validate",self._validate)
        graph.add_edge(START,"contextualize"); graph.add_edge("contextualize","plan"); graph.add_edge("plan","retrieve"); graph.add_edge("retrieve","grade"); graph.add_conditional_edges("grade",self._after_grade,{"rewrite":"rewrite","generate":"generate"}); graph.add_edge("rewrite","retrieve"); graph.add_edge("generate","validate"); graph.add_conditional_edges("validate",self._after_validate,{"revise":"revise","end":END}); graph.add_edge("revise","validate")
        return graph.compile()
    async def _contextualize(self,state):
        memory=state.get("conversation")
        if not memory or not getattr(memory,"messages",None): return {"standalone_question":state["request"].message}
        recent="\n".join(f"{item['role']}: {item['content']}" for item in memory.messages[-6:])
        standalone=await self.llm.complete_text("Rewrite the latest user turn as one standalone mail-search question. Preserve explicit teams and weeks and output only the question.",f"History:\n{recent}\nLatest:\n{state['request'].message}")
        return {"standalone_question":standalone.strip() or state["request"].message}
    async def _plan(self,state):
        plan=await self.llm.complete_model("Create one to six bounded mail search tasks.",state["standalone_question"],RetrievalPlan)
        tasks=[item.model_copy(update={"filters":state["request"].filters}) for item in plan.tasks[:6]]
        return {"tasks":tasks,"rewrites":0,"revisions":0,"disclosures":[]}
    async def _retrieve(self,state):
        evidence=[]; mode="hybrid"; disclosures=[]
        for task in state["tasks"]:
            result=await self.retrieval.search(task,state["policy"]); evidence.extend(result.evidence)
            if result.embedding_error=="EMBEDDING_UNAVAILABLE": mode="bm25"
        if mode=="bm25": disclosures=[BM25_FALLBACK_DISCLOSURE]
        unique={item.document_id:item for item in evidence}
        renumbered=[item.model_copy(update={"evidence_id":f"S{index}"}) for index,item in enumerate(list(unique.values())[:8],1)]
        return {"evidence":renumbered,"mode":mode,"disclosures":disclosures}
    async def _grade(self,state):
        if not state["evidence"]: return {"sufficient":False,"missing_information":["mail evidence"]}
        summary="\n".join(f"[{item.evidence_id}] {item.excerpt[:800]}" for item in state["evidence"])
        grade=await self.llm.complete_model("Judge whether the evidence is sufficient for the question.",f"Question: {state['standalone_question']}\nEvidence:\n{summary}",EvidenceGrade)
        return grade.model_dump()
    def _after_grade(self,state): return "generate" if state["sufficient"] or state["rewrites"]>=2 else "rewrite"
    async def _rewrite(self,state):
        query=await self.llm.complete_text("Rewrite the search query to target only the missing evidence. Output only the query.",f"Question: {state['standalone_question']}\nMissing: {state['missing_information']}")
        tasks=[item.model_copy(update={"query":query.strip() or item.query}) for item in state["tasks"]]
        return {"tasks":tasks,"rewrites":state["rewrites"]+1}
    async def _generate(self,state):
        context="\n\n".join(f"[{item.evidence_id}] {item.excerpt}" for item in state["evidence"])
        answer=await self.llm.complete_text("Answer only from evidence and cite every factual claim with [S#].",f"Question: {state['request'].message}\nEvidence:\n{context}")
        if not state["evidence"]: answer="확인 가능한 근거가 없어 답변할 수 없습니다."
        return {"answer":answer}
    def _validate(self,state):
        validation=self.validator.validate(state["answer"],state["evidence"],state["policy"])
        answer=state["answer"] if validation.valid or not state["evidence"] else "근거 인용을 검증하지 못해 확인된 답변을 제공할 수 없습니다."
        return {"answer":answer,"citation_valid":validation.valid or not state["evidence"]}
    def _after_validate(self,state): return "end" if state["citation_valid"] or state["revisions"]>=1 else "revise"
    async def _revise(self,state):
        context="\n".join(f"[{item.evidence_id}] {item.excerpt}" for item in state["evidence"])
        answer=await self.llm.complete_text("Rewrite the answer so every factual claim has an existing [S#] citation. Remove unsupported claims.",f"Draft: {state['answer']}\nEvidence:\n{context}")
        return {"answer":answer,"revisions":state["revisions"]+1}
    async def invoke(self,request,policy,conversation):
        state=await self.graph.ainvoke({"request":request,"policy":policy,"conversation":conversation})
        valid=self.validator.validate(state["answer"],state.get("evidence",[]),policy).valid if state.get("evidence") else True
        answer=state["answer"]
        if state.get("disclosures"): answer=f"{answer}\n\n"+"\n".join(state["disclosures"])
        return FastRAGResult(answer=answer,evidence=state.get("evidence",[]),quality=QualityStatus(citation_valid=valid,limited_answer=not bool(state.get("evidence")),retrieval_mode=state.get("mode","hybrid")),disclosures=state.get("disclosures",[]))
    async def respond_general(self,request):
        answer=await self.llm.complete_text("Respond briefly to greetings or usage questions. Do not make mail claims without retrieval.",request.message)
        return FastRAGResult(answer=answer,evidence=[],quality=QualityStatus(citation_valid=True,limited_answer=False))
```

- [ ] **Step 5: Run router and Fast RAG tests**

Run: `python -m pytest tests/mail_rag/test_router.py tests/mail_rag/test_fast_rag.py -q`

Expected: PASS with deterministic route override and BM25 disclosure.

- [ ] **Step 6: Commit Fast RAG**

```bash
git add app/llm app/graphs tests/mail_rag/test_router.py tests/mail_rag/test_fast_rag.py
git commit -m "feat(rag): add bounded fast workflow"
```

---

### Task 5: Owner-scoped conversations and FastAPI chat endpoint

**Files:**
- Create: `app/persistence/__init__.py`
- Create: `app/persistence/conversations.py`
- Create: `app/api/__init__.py`
- Create: `app/api/dependencies.py`
- Create: `app/api/routes/__init__.py`
- Create: `app/api/routes/chat.py`
- Create: `app/api/routes/health.py`
- Create: `app/api/main.py`
- Test: `tests/mail_rag/test_conversations.py`
- Test: `tests/mail_rag/test_chat_api.py`

**Interfaces:**
- Produces: `ConversationStore.load(conversation_id, policy) -> ConversationMemory | None`
- Produces: `ConversationStore.save(conversation_id, policy, memory) -> None`
- Produces: `create_app(container: ServiceContainer) -> FastAPI`

- [ ] **Step 1: Write failing ownership and API tests**

```python
# tests/mail_rag/test_conversations.py
import pytest
from app.domain.errors import AppError,ErrorCode
from app.domain.policy import PolicyContext
from app.persistence.conversations import ConversationMemory,InMemoryConversationStore

@pytest.mark.asyncio
async def test_conversation_cannot_cross_owner_boundary():
    store=InMemoryConversationStore(); await store.save("c1",PolicyContext.from_user_id("kim"),ConversationMemory())
    with pytest.raises(AppError) as error: await store.load("c1",PolicyContext.from_user_id("lee"))
    assert error.value.code == ErrorCode.UNAUTHORIZED_RESOURCE
```

```python
# tests/mail_rag/test_chat_api.py
from fastapi.testclient import TestClient
from app.api.dependencies import ServiceContainer
from app.api.main import create_app
from app.domain.chat import FastRAGResult,QualityStatus

class FakeRouter:
    async def route(self,request):
        from app.domain.chat import RouteDecision
        return RouteDecision(route="fast",reason_code="test",confidence=1,estimated_searches=1)
class FakeFast:
    async def invoke(self,request,policy,conversation): return FastRAGResult(answer="답 [S1]",evidence=[],quality=QualityStatus(citation_valid=True))

def test_chat_reads_user_id_from_body_and_returns_trace():
    app=create_app(ServiceContainer(router=FakeRouter(),fast=FakeFast(),deep=None,conversations=None,jobs=None))
    response=TestClient(app).post("/v1/chat",json={"user_id":"kim","message":"질문"})
    assert response.status_code == 200
    assert response.json()["mode"] == "fast_rag" and response.json()["trace_id"]
```

- [ ] **Step 2: Run tests and confirm missing persistence/API failures**

Run: `python -m pytest tests/mail_rag/test_conversations.py tests/mail_rag/test_chat_api.py -q`

Expected: FAIL during imports.

- [ ] **Step 3: Implement owner-keyed conversation persistence and dependency container**

```python
# app/persistence/conversations.py
from pydantic import BaseModel,Field
from app.domain.evidence import Evidence,RetrievalFilters
from app.domain.errors import AppError,ErrorCode
class ConversationMemory(BaseModel):
    messages:list[dict[str,str]]=Field(default_factory=list,max_length=20)
    filters:RetrievalFilters=Field(default_factory=RetrievalFilters)
    cited_evidence:list[Evidence]=Field(default_factory=list,max_length=8)
class InMemoryConversationStore:
    def __init__(self): self.records={}
    async def load(self,conversation_id,policy):
        record=self.records.get(conversation_id)
        if not record:return None
        if record[0]!=policy.user_id: raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE,"대화 소유자가 일치하지 않습니다.")
        return record[1]
    async def save(self,conversation_id,policy,memory): self.records[conversation_id]=(policy.user_id,memory)

class MongoConversationStore:
    def __init__(self,collection): self.collection=collection
    async def load(self,conversation_id,policy):
        document=await self.collection.find_one({"conversation_id":conversation_id})
        if not document:return None
        if document.get("user_id")!=policy.user_id: raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE,"대화 소유자가 일치하지 않습니다.")
        return ConversationMemory.model_validate(document.get("memory") or {})
    async def save(self,conversation_id,policy,memory):
        existing=await self.collection.find_one({"conversation_id":conversation_id},{"user_id":1})
        if existing and existing.get("user_id")!=policy.user_id: raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE,"대화 소유자가 일치하지 않습니다.")
        await self.collection.update_one({"conversation_id":conversation_id,"user_id":policy.user_id},{"$set":{"memory":memory.model_dump(mode="json"),"updated_at":__import__("datetime").datetime.now(__import__("datetime").UTC)},"$setOnInsert":{"conversation_id":conversation_id,"user_id":policy.user_id}},upsert=True)
```

```python
# app/api/dependencies.py
from dataclasses import dataclass
from typing import Any
@dataclass
class ServiceContainer:
    router:Any; fast:Any; deep:Any; conversations:Any; jobs:Any

def build_opensearch_client(settings=None):
    from opensearchpy import OpenSearch
    from app.config.settings import get_settings
    current=settings or get_settings()
    return OpenSearch(hosts=[{"host":current.opensearch_host,"port":current.opensearch_port}],http_auth=(current.opensearch_user,current.opensearch_password.get_secret_value()),use_ssl=current.opensearch_use_ssl,verify_certs=current.opensearch_verify_certs,ssl_show_warn=current.opensearch_verify_certs)

def build_container(settings=None):
    from motor.motor_asyncio import AsyncIOMotorClient
    from openai import AsyncOpenAI
    from app.config.settings import get_settings
    from app.graphs.deep_research import DeepCoordinator
    from app.graphs.fast_rag import FastRAGWorkflow
    from app.graphs.router import route_request
    from app.llm.gateway import OpenAILLMGateway
    from app.persistence.conversations import MongoConversationStore
    from app.persistence.research_jobs import MongoResearchJobStore
    from app.retrieval.embedding import OpenAIEmbeddingGateway
    from app.retrieval.opensearch import AsyncOpenSearchGateway
    from app.retrieval.service import RetrievalService
    current=settings or get_settings(); ai=AsyncOpenAI(api_key=current.openrouter_api_key.get_secret_value(),base_url=current.openrouter_base_url or None)
    llm=OpenAILLMGateway(ai,current.llm_model); search=AsyncOpenSearchGateway(build_opensearch_client(current)); embeddings=OpenAIEmbeddingGateway(ai,current.embedding_model)
    retrieval=RetrievalService(search,embeddings,current.mail_child_index,current.mail_parent_index,current.wiki_index); mongo=AsyncIOMotorClient(current.mongo_uri)[current.mongo_db]
    class RouterService:
        async def route(self,request): return await route_request(request,llm)
    jobs=MongoResearchJobStore(mongo.research_jobs)
    return ServiceContainer(router=RouterService(),fast=FastRAGWorkflow(retrieval,llm),deep=DeepCoordinator(jobs),conversations=MongoConversationStore(mongo.conversations),jobs=jobs)
```

- [ ] **Step 4: Implement the chat and health routes with FastAPI DI**

```python
# app/api/main.py
from fastapi import FastAPI
from app.api.routes.chat import router as chat_router
from app.api.routes.health import router as health_router
def create_app(container):
    app=FastAPI(title="Mail Research RAG",version="1.0.0")
    app.state.container=container; app.include_router(chat_router); app.include_router(health_router)
    return app
```

```python
# app/api/routes/chat.py
import uuid
from fastapi import APIRouter,Request
from app.domain.chat import ChatRequest,ChatResponse
from app.domain.policy import PolicyContext
from app.persistence.conversations import ConversationMemory
router=APIRouter()
@router.post("/v1/chat",response_model=ChatResponse)
async def chat(payload:ChatRequest,request:Request):
    services=request.app.state.container; trace_id=uuid.uuid4().hex; conversation_id=payload.conversation_id or str(uuid.uuid4())
    policy=PolicyContext.from_user_id(payload.user_id); memory=await services.conversations.load(conversation_id,policy) if services.conversations else None
    decision=await services.router.route(payload)
    if decision.route=="clarify":
        return ChatResponse(conversation_id=conversation_id,mode="fast_rag",answer=decision.clarification_question or "조회할 팀이나 기간을 구체적으로 알려주세요.",references=[],quality={"citation_valid":True,"limited_answer":True,"retrieval_mode":"hybrid"},trace_id=trace_id)
    if decision.route=="general":
        result=await services.fast.respond_general(payload)
        return ChatResponse(conversation_id=conversation_id,mode="fast_rag",answer=result.answer,references=[],quality=result.quality,trace_id=trace_id)
    if decision.route=="deep":
        job=await services.deep.enqueue(payload,policy,trace_id)
        if services.conversations:
            prior=list(memory.messages if memory else []); prior.append({"role":"user","content":payload.message})
            await services.conversations.save(conversation_id,policy,ConversationMemory(messages=prior[-20:],filters=payload.filters,cited_evidence=list(memory.cited_evidence if memory else [])))
        return ChatResponse(conversation_id=conversation_id,mode="deep_research",trace_id=trace_id,job_id=job.job_id,status=job.status,plan_summary=job.plan_summary)
    result=await services.fast.invoke(payload,policy,memory)
    if services.conversations:
        prior=list(memory.messages if memory else []); prior.extend([{"role":"user","content":payload.message},{"role":"assistant","content":result.answer}])
        await services.conversations.save(conversation_id,policy,ConversationMemory(messages=prior[-20:],filters=payload.filters,cited_evidence=result.evidence[:8]))
    return ChatResponse(conversation_id=conversation_id,mode="fast_rag",answer=result.answer,references=result.evidence,quality=result.quality,disclosures=result.disclosures,trace_id=trace_id)
```

```python
# app/api/routes/health.py
from fastapi import APIRouter
router=APIRouter()
@router.get("/health")
async def health(): return {"status":"ok"}
```

- [ ] **Step 5: Run persistence and API tests**

Run: `python -m pytest tests/mail_rag/test_conversations.py tests/mail_rag/test_chat_api.py -q`

Expected: PASS with request-body owner and generated trace ID.

- [ ] **Step 6: Commit API and conversation support**

```bash
git add app/api app/persistence tests/mail_rag/test_conversations.py tests/mail_rag/test_chat_api.py
git commit -m "feat(rag): expose owner-scoped chat api"
```

---

### Task 6: Persistent Deep Agent graph, worker, and job API

**Files:**
- Create: `app/persistence/research_jobs.py`
- Create: `app/graphs/deep_research.py`
- Create: `app/workers/__init__.py`
- Create: `app/workers/research.py`
- Create: `app/api/routes/research.py`
- Modify: `app/api/main.py`
- Test: `tests/mail_rag/test_deep_research.py`
- Test: `tests/mail_rag/test_research_api.py`

**Interfaces:**
- Produces: `ResearchJobStore.create/get/request_cancel/claim/complete/fail`
- Produces: `DeepResearchWorkflow.invoke(job, policy) -> DeepResearchResult`
- Produces: `ResearchWorker.run_once() -> bool`

- [ ] **Step 1: Write failing parallel research, ownership, cancellation, and lease tests**

```python
# tests/mail_rag/test_deep_research.py
import pytest
from app.domain.policy import PolicyContext
from app.graphs.deep_research import DeepResearchWorkflow

class DeepLLM:
    async def complete_model(self,system,user,schema):
        if schema.__name__=="ResearchPlan": return schema.model_validate({"sub_questions":["A팀 원인","B팀 조치"]})
        return schema.model_validate({"complete":True,"follow_up_questions":[]})
    async def complete_text(self,system,user): return "종합 결과 [S1]"
class DeepRetrieval:
    async def search(self,task,policy):
        from app.retrieval.service import RetrievalResult
        return RetrievalResult(evidence=[],mode="hybrid")

@pytest.mark.asyncio
async def test_deep_graph_finishes_bounded_parallel_plan():
    result=await DeepResearchWorkflow(DeepRetrieval(),DeepLLM()).invoke("두 팀 비교",PolicyContext.from_user_id("kim"))
    assert result.completed_sub_questions == 2
    assert result.rounds <= 2
```

```python
# tests/mail_rag/test_research_api.py
import pytest
from app.domain.errors import AppError
from app.domain.policy import PolicyContext
from app.persistence.research_jobs import InMemoryResearchJobStore

@pytest.mark.asyncio
async def test_job_status_and_cancel_are_owner_scoped_and_idempotent():
    store=InMemoryResearchJobStore(); job=await store.create("kim","trace","question","plan")
    await store.request_cancel(job.job_id,PolicyContext.from_user_id("kim")); await store.request_cancel(job.job_id,PolicyContext.from_user_id("kim"))
    with pytest.raises(AppError): await store.get(job.job_id,PolicyContext.from_user_id("lee"))
```

- [ ] **Step 2: Run tests and verify missing Deep modules**

Run: `python -m pytest tests/mail_rag/test_deep_research.py tests/mail_rag/test_research_api.py -q`

Expected: FAIL during imports.

- [ ] **Step 3: Implement job state transitions and lease-based claiming**

```python
# app/persistence/research_jobs.py
import uuid
from datetime import UTC,datetime,timedelta
from app.domain.errors import AppError,ErrorCode
from app.domain.research import ResearchJob,ResearchStatus
class InMemoryResearchJobStore:
    def __init__(self): self.jobs={}
    async def create(self,user_id,trace_id,question,plan_summary):
        now=datetime.now(UTC); job=ResearchJob(job_id=f"research_{uuid.uuid4().hex}",user_id=user_id,trace_id=trace_id,question=question,status=ResearchStatus.QUEUED,created_at=now,updated_at=now,plan_summary=plan_summary)
        self.jobs[job.job_id]=job; return job
    async def get(self,job_id,policy):
        job=self.jobs.get(job_id)
        if not job or job.user_id!=policy.user_id: raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE,"조사 작업에 접근할 수 없습니다.")
        return job
    async def request_cancel(self,job_id,policy):
        job=await self.get(job_id,policy)
        if job.status in {ResearchStatus.COMPLETED,ResearchStatus.FAILED,ResearchStatus.CANCELLED}: return job
        job=job.model_copy(update={"status":ResearchStatus.CANCELLING,"updated_at":datetime.now(UTC)}); self.jobs[job_id]=job; return job
    async def claim(self,lease_seconds=60):
        now=datetime.now(UTC)
        for job_id,job in self.jobs.items():
            if job.status==ResearchStatus.QUEUED or (job.status==ResearchStatus.RUNNING and job.lease_until and job.lease_until<=now):
                claimed=job.model_copy(update={"status":ResearchStatus.RUNNING,"attempts":job.attempts+1,"lease_until":now+timedelta(seconds=lease_seconds),"updated_at":now}); self.jobs[job_id]=claimed; return claimed
        return None
    async def complete(self,job_id,result):
        job=self.jobs[job_id].model_copy(update={"status":ResearchStatus.COMPLETED,"progress":100,"result_markdown":result,"lease_until":None,"updated_at":datetime.now(UTC)}); self.jobs[job_id]=job; return job
    async def mark_cancelled(self,job_id):
        job=self.jobs[job_id].model_copy(update={"status":ResearchStatus.CANCELLED,"lease_until":None,"updated_at":datetime.now(UTC)}); self.jobs[job_id]=job; return job
    async def fail(self,job_id,error_code):
        job=self.jobs[job_id].model_copy(update={"status":ResearchStatus.FAILED,"error_code":error_code,"lease_until":None,"updated_at":datetime.now(UTC)}); self.jobs[job_id]=job; return job
    async def retry(self,job_id,policy):
        job=await self.get(job_id,policy)
        if job.status not in {ResearchStatus.FAILED,ResearchStatus.CANCELLED}: return job
        job=job.model_copy(update={"status":ResearchStatus.QUEUED,"error_code":None,"progress":0,"lease_until":None,"updated_at":datetime.now(UTC)}); self.jobs[job_id]=job; return job

class MongoResearchJobStore:
    def __init__(self,collection): self.collection=collection
    async def create(self,user_id,trace_id,question,plan_summary):
        job=await InMemoryResearchJobStore().create(user_id,trace_id,question,plan_summary)
        await self.collection.insert_one(job.model_dump(mode="python")); return job
    async def get(self,job_id,policy):
        document=await self.collection.find_one({"job_id":job_id})
        if not document or document.get("user_id")!=policy.user_id: raise AppError(ErrorCode.UNAUTHORIZED_RESOURCE,"조사 작업에 접근할 수 없습니다.")
        return ResearchJob.model_validate(document)
    async def request_cancel(self,job_id,policy):
        job=await self.get(job_id,policy)
        if job.status in {ResearchStatus.COMPLETED,ResearchStatus.FAILED,ResearchStatus.CANCELLED}: return job
        await self.collection.update_one({"job_id":job_id,"user_id":policy.user_id},{"$set":{"status":ResearchStatus.CANCELLING,"updated_at":datetime.now(UTC)}})
        return await self.get(job_id,policy)
    async def claim(self,lease_seconds=60):
        from pymongo import ReturnDocument
        now=datetime.now(UTC); query={"$or":[{"status":ResearchStatus.QUEUED},{"status":ResearchStatus.RUNNING,"lease_until":{"$lte":now}}]}
        update={"$set":{"status":ResearchStatus.RUNNING,"lease_until":now+timedelta(seconds=lease_seconds),"updated_at":now},"$inc":{"attempts":1}}
        document=await self.collection.find_one_and_update(query,update,sort=[("created_at",1)],return_document=ReturnDocument.AFTER)
        return ResearchJob.model_validate(document) if document else None
    async def _terminal(self,job_id,status,**values):
        values.update({"status":status,"lease_until":None,"updated_at":datetime.now(UTC)})
        await self.collection.update_one({"job_id":job_id},{"$set":values})
    async def complete(self,job_id,result): await self._terminal(job_id,ResearchStatus.COMPLETED,result_markdown=result,progress=100)
    async def mark_cancelled(self,job_id): await self._terminal(job_id,ResearchStatus.CANCELLED)
    async def fail(self,job_id,error_code): await self._terminal(job_id,ResearchStatus.FAILED,error_code=error_code)
    async def retry(self,job_id,policy):
        job=await self.get(job_id,policy)
        if job.status in {ResearchStatus.FAILED,ResearchStatus.CANCELLED}:
            await self.collection.update_one({"job_id":job_id,"user_id":policy.user_id},{"$set":{"status":ResearchStatus.QUEUED,"error_code":None,"progress":0,"lease_until":None,"updated_at":datetime.now(UTC)}})
        return await self.get(job_id,policy)
```

- [ ] **Step 4: Implement Deep graph fan-out with an accumulating reducer**

```python
# app/graphs/deep_research.py
import operator
from typing import Annotated,TypedDict
from pydantic import BaseModel,Field
from langgraph.graph import END,START,StateGraph
from langgraph.types import Send
from app.domain.evidence import Evidence,SearchTask
from app.domain.chat import BM25_FALLBACK_DISCLOSURE
from app.security.citations import CitationValidator

class ResearchPlan(BaseModel): sub_questions:list[str]=Field(min_length=1,max_length=8)
class GapDecision(BaseModel): complete:bool; follow_up_questions:list[str]=Field(default_factory=list,max_length=4)
class BranchResult(BaseModel): question:str; evidence:list=Field(default_factory=list); failed:bool=False; embedding_fallback:bool=False
class DeepResearchResult(BaseModel): report:str; evidence:list[Evidence]=Field(default_factory=list); completed_sub_questions:int; rounds:int
class DeepState(TypedDict,total=False):
    question:str; sub_question:str; policy:object; sub_questions:list[str]; gap_questions:list[str]; complete:bool
    branch_results:Annotated[list[BranchResult],operator.add]; evidence:list[Evidence]; report:str; rounds:int
class DeepResearchWorkflow:
    def __init__(self,retrieval,llm): self.retrieval=retrieval; self.llm=llm; self.validator=CitationValidator(); self.graph=self._build()
    def _build(self):
        graph=StateGraph(DeepState); graph.add_node("plan",self._plan); graph.add_node("research",self._research); graph.add_node("gap",self._gap); graph.add_node("synthesize",self._synthesize)
        graph.add_edge(START,"plan"); graph.add_conditional_edges("plan",self._fanout,["research"]); graph.add_edge("research","gap"); graph.add_conditional_edges("gap",self._after_gap,["research","synthesize"]); graph.add_edge("synthesize",END); return graph.compile()
    async def _plan(self,state):
        plan=await self.llm.complete_model("Create objective bounded mail research sub-questions.",state["question"],ResearchPlan)
        return {"sub_questions":plan.sub_questions[:8],"rounds":0,"branch_results":[]}
    def _fanout(self,state): return [Send("research",{"question":state["question"],"policy":state["policy"],"sub_question":item}) for item in state["sub_questions"]]
    async def _research(self,state):
        try:
            result=await self.retrieval.search(SearchTask(query=state["sub_question"],top_k=8),state["policy"])
            return {"branch_results":[BranchResult(question=state["sub_question"],evidence=result.evidence,embedding_fallback=result.embedding_error=="EMBEDDING_UNAVAILABLE")]}
        except Exception: return {"branch_results":[BranchResult(question=state["sub_question"],failed=True)]}
    async def _gap(self,state):
        summary="\n".join(f"{item.question}: {len(item.evidence)} evidence" for item in state["branch_results"])
        decision=await self.llm.complete_model("Identify only material research gaps. Return complete=true when evidence is sufficient.",summary,GapDecision)
        return {"complete":decision.complete,"gap_questions":decision.follow_up_questions[:4],"rounds":state["rounds"]+1}
    def _after_gap(self,state):
        if state["complete"] or state["rounds"]>=2 or not state["gap_questions"]: return "synthesize"
        return [Send("research",{"question":state["question"],"policy":state["policy"],"sub_question":item}) for item in state["gap_questions"]]
    async def _synthesize(self,state):
        by_document={item.document_id:item for branch in state["branch_results"] if not branch.failed for item in branch.evidence}
        evidence=[item.model_copy(update={"evidence_id":f"S{index}"}) for index,item in enumerate(list(by_document.values())[:32],1)]
        context="\n".join(f"[{item.evidence_id}] {item.excerpt}" for item in evidence)
        report=await self.llm.complete_text("Synthesize only supported claims and retain [S#] citations.",f"Question: {state['question']}\nResearch:\n{context}")
        validation=self.validator.validate(report,evidence,state["policy"])
        if evidence and not validation.valid: report="조사 근거의 인용을 검증하지 못해 보고서를 제공할 수 없습니다."
        if any(item.embedding_fallback for item in state["branch_results"]): report=f"{report}\n\n{BM25_FALLBACK_DISCLOSURE}"
        return {"report":report,"evidence":evidence}
    async def invoke(self,question,policy):
        state=await self.graph.ainvoke({"question":question,"policy":policy,"branch_results":[]})
        completed=sum(not item.failed for item in state["branch_results"])
        return DeepResearchResult(report=state["report"],evidence=state.get("evidence",[]),completed_sub_questions=completed,rounds=state["rounds"])
```

- [ ] **Step 5: Implement worker and owner-scoped research routes**

```python
# app/workers/research.py
from app.domain.policy import PolicyContext
class ResearchWorker:
    def __init__(self,jobs,workflow): self.jobs=jobs; self.workflow=workflow
    async def run_once(self):
        job=await self.jobs.claim()
        if not job:return False
        current=await self.jobs.get(job.job_id,PolicyContext.from_user_id(job.user_id))
        if current.status.value=="cancelling": await self.jobs.mark_cancelled(job.job_id); return True
        try:
            result=await self.workflow.invoke(job.question,PolicyContext.from_user_id(job.user_id))
            await self.jobs.complete(job.job_id,result.report); return True
        except Exception:
            await self.jobs.fail(job.job_id,"RESEARCH_FAILED"); return True
```

```python
# app/api/routes/research.py
import asyncio,json
from pydantic import BaseModel
from fastapi import APIRouter,Request
from fastapi.responses import StreamingResponse
from app.domain.policy import PolicyContext
router=APIRouter(prefix="/v1/research")
class OwnerBody(BaseModel): user_id:str
@router.post("/{job_id}/status")
async def status(job_id:str,payload:OwnerBody,request:Request): return await request.app.state.container.jobs.get(job_id,PolicyContext.from_user_id(payload.user_id))
@router.post("/{job_id}/cancel")
async def cancel(job_id:str,payload:OwnerBody,request:Request): return await request.app.state.container.jobs.request_cancel(job_id,PolicyContext.from_user_id(payload.user_id))
@router.post("/{job_id}/retry")
async def retry(job_id:str,payload:OwnerBody,request:Request): return await request.app.state.container.jobs.retry(job_id,PolicyContext.from_user_id(payload.user_id))
@router.post("/{job_id}/events")
async def events(job_id:str,payload:OwnerBody,request:Request):
    policy=PolicyContext.from_user_id(payload.user_id)
    async def stream():
        while True:
            job=await request.app.state.container.jobs.get(job_id,policy)
            yield "data: "+json.dumps({"status":job.status,"progress":job.progress},ensure_ascii=False)+"\n\n"
            if job.status.value in {"completed","failed","cancelled"}: return
            await asyncio.sleep(1)
    return StreamingResponse(stream(),media_type="text/event-stream")
```

```python
# append to app/graphs/deep_research.py
class DeepCoordinator:
    def __init__(self,jobs): self.jobs=jobs
    async def enqueue(self,request,policy,trace_id):
        scope=[]
        if request.filters.teams: scope.append(f"{len(request.filters.teams)}개 팀")
        if request.filters.weeks: scope.append(f"{len(request.filters.weeks)}주")
        summary=", ".join(scope) or "질문 범위 조사"
        return await self.jobs.create(policy.user_id,trace_id,request.message,summary)
```

```python
# Task 6 modification to app/api/main.py
from app.api.routes.research import router as research_router

# inside create_app, after chat_router
app.include_router(research_router)
```

The production dependency builder constructs `MongoResearchJobStore` and `DeepCoordinator`; tests inject the in-memory store.

- [ ] **Step 6: Run Deep workflow and API tests**

Run: `python -m pytest tests/mail_rag/test_deep_research.py tests/mail_rag/test_research_api.py -q`

Expected: PASS with two accumulated branches and owner-scoped idempotent cancellation.

- [ ] **Step 7: Commit Deep Agent**

```bash
git add app/graphs/deep_research.py app/persistence/research_jobs.py app/workers app/api tests/mail_rag/test_deep_research.py tests/mail_rag/test_research_api.py
git commit -m "feat(rag): add persistent deep research"
```

---

### Task 7: Owner propagation, v2 index templates, and migration tools

**Files:**
- Modify: `fetch_mail.py`
- Modify: `embed_vectordb.py`
- Modify: `wiki_builder.py`
- Create: `index_templates/weekly_mail_child_v2.json`
- Create: `index_templates/weekly_mail_parent_v2.json`
- Create: `index_templates/wiki_summaries_v2.json`
- Create: `scripts/backfill_user_id.py`
- Create: `scripts/backfill_parent_child.py`
- Create: `scripts/shadow_retrieval.py`
- Test: `tests/mail_rag/test_ingestion_owner.py`
- Test: `tests/mail_rag/test_index_templates.py`

**Interfaces:**
- `fetch_mail` writes required `meta["user_id"]` from `MAIL_USER_ID`.
- `index_original_mail(client: OpenSearch, embedding_client: OpenAI, mail_dir: Path, index_name: str = INDEX_NAME) -> Optional[Dict]` rejects metadata without ownership and writes it into every chunk.
- `backfill_user_id --index INDEX --user-id OWNER [--dry-run]` updates only documents missing ownership.
- Parent/child IDs use SHA-256 over stable identifiers and version strings.

- [ ] **Step 1: Write failing ingestion and strict-template tests**

```python
# tests/mail_rag/test_ingestion_owner.py
import json
from pathlib import Path
import pytest
from scripts.backfill_user_id import build_update_query

def test_backfill_targets_only_missing_owner_and_uses_explicit_value():
    body=build_update_query("kim")
    assert body["query"] == {"bool":{"must_not":{"exists":{"field":"user_id"}}}}
    assert body["script"]["params"] == {"user_id":"kim"}

def test_backfill_rejects_blank_owner():
    with pytest.raises(ValueError): build_update_query(" ")
```

```python
# tests/mail_rag/test_index_templates.py
import json
from pathlib import Path
import pytest
@pytest.mark.parametrize("name",["weekly_mail_child_v2","weekly_mail_parent_v2","wiki_summaries_v2"])
def test_v2_template_is_strict_and_owner_filterable(name):
    body=json.loads(Path(f"index_templates/{name}.json").read_text())
    assert body["mappings"]["dynamic"] == "strict"
    assert body["mappings"]["properties"]["user_id"] == {"type":"keyword"}
```

- [ ] **Step 2: Run tests and verify missing migration/template failures**

Run: `python -m pytest tests/mail_rag/test_ingestion_owner.py tests/mail_rag/test_index_templates.py -q`

Expected: FAIL because scripts and templates do not exist.

- [ ] **Step 3: Add fail-closed owner propagation and backfill query**

```python
# scripts/backfill_user_id.py
import argparse
def build_update_query(user_id:str)->dict:
    owner=user_id.strip()
    if not owner: raise ValueError("user_id must not be blank")
    return {"query":{"bool":{"must_not":{"exists":{"field":"user_id"}}}},"script":{"lang":"painless","source":"ctx._source.user_id = params.user_id","params":{"user_id":owner}}}
def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--index",required=True); parser.add_argument("--user-id",required=True); parser.add_argument("--dry-run",action="store_true")
    args=parser.parse_args(); body=build_update_query(args.user_id)
    if args.dry_run: print({"index":args.index,"body":body}); return
    from app.api.dependencies import build_opensearch_client
    response=build_opensearch_client().update_by_query(index=args.index,body=body,conflicts="proceed",refresh=True)
    print({"updated":response.get("updated",0),"version_conflicts":response.get("version_conflicts",0)})
if __name__=="__main__": main()
```

```python
# fetch_mail.py additions
def require_mail_user_id() -> str:
    user_id=os.getenv("MAIL_USER_ID","").strip()
    if not user_id: raise RuntimeError("MAIL_USER_ID is required before collecting mail")
    return user_id

# change signature and meta construction
def save_mail(mail_data,week,team,mail_id:str,user_id:str):
    # retain the existing body/attachment behavior and include this exact field
    meta={"user_id":user_id,"subject":mail_data["subject"],"sender":mail_data["sender"],"week":week,"team":team,"mail_type":classify_mail_type(mail_data.get("subject",""))}

# main resolves once and passes it to every save
mail_user_id=require_mail_user_id()
save_mail(mail_data,week,team,mail_id,mail_user_id)
```

```python
# embed_vectordb.py additions
def require_document_user_id(meta:dict)->str:
    user_id=str(meta.get("user_id") or "").strip()
    if not user_id: raise ValueError("meta.json must contain user_id before indexing")
    return user_id

# create_index mapping
"user_id":{"type":"keyword"}

# index_original_mail before chunking and in every _source
user_id=require_document_user_id(meta)
source["user_id"]=user_id
```

```python
# wiki_builder.py owner contract
def owner_filter(user_id:str)->dict:
    owner=user_id.strip()
    if not owner: raise ValueError("user_id is required for Wiki generation")
    return {"term":{"user_id":owner}}

def save_wiki_doc(os_client,embed_client,text,title,summary_type,user_id,team=None,week=None,topic=None,source_doc_ids=None,doc_id=None,entities=None):
    if not text.strip(): return
    doc={"user_id":user_id,"embedding":get_embedding(embed_client,text),"text":text,"title":title,"summary_type":summary_type,"team":team,"week":week,"topic":topic,"source_doc_ids":source_doc_ids or [],"entities":[],"topic_keys":[],"created_at":datetime.now(tz=__import__("datetime").timezone.utc).isoformat(),"updated_at":datetime.now(tz=__import__("datetime").timezone.utc).isoformat()}
    os_client.index(index=WIKI_INDEX,id=doc_id,body=doc) if doc_id else os_client.index(index=WIKI_INDEX,body=doc)
```

Every Wiki source query receives `owner_filter(user_id)`, every Wiki document ID is prefixed with a stable owner hash, and all nine `save_wiki_doc` call sites pass the current owner explicitly.

- [ ] **Step 4: Add exact v2 mappings and stable-ID backfill**

Create the three JSON files with `"dynamic":"strict"`. The child mapping has keyword `child_id`, `parent_id`, `mail_id`, `user_id`, `team`, `week`, `mail_type`, version/hash fields; Korean-analyzed `text`; 4096-dimension FAISS cosine `embedding`; integer part fields; and date `indexed_at`. The parent mapping has keyword `parent_id`, `mail_id`, `user_id`, facet/version/hash fields; Korean `text`; integer section ordinal; and date `indexed_at`. The Wiki mapping has keyword `wiki_id`, `user_id`, `summary_type`, `team`, `week`, `source_doc_ids`, `content_hash`; Korean `title` and `text`; 4096-dimension embedding; and date `created_at`, `updated_at`.

```python
# scripts/backfill_parent_child.py core functions
from hashlib import sha256
def stable_id(*parts:str)->str: return sha256("\x1f".join(parts).encode()).hexdigest()
def build_parent_child(source:dict,parser_version="mail-v2",chunker_version="child-v2"):
    user_id=str(source.get("user_id") or "").strip(); mail_id=str(source.get("mail_id") or "").strip(); text=str(source.get("text") or "")
    if not user_id or not mail_id or not text: return [],[]
    parent_id=stable_id(user_id,mail_id,parser_version,str(source.get("part_index",0)))
    parent={"parent_id":parent_id,"mail_id":mail_id,"user_id":user_id,"text":text,"team":source.get("team"),"week":source.get("week"),"mail_type":source.get("mail_type","other"),"section_ordinal":int(source.get("part_index",0)),"content_hash":sha256(text.encode()).hexdigest(),"parser_version":parser_version,"chunker_version":chunker_version,"embedding_model":source.get("embedding_model","legacy")}
    child_id=stable_id(parent_id,chunker_version,"0")
    child={**parent,"child_id":child_id,"embedding":source.get("embedding"),"part_index":0,"total_parts":1}
    return [parent],[child]
```

The CLI scrolls the source index with an owner-present filter, resumes from a JSON checkpoint containing `last_sort`, bulk-writes parent and child actions, and writes the checkpoint after each successful batch. `shadow_retrieval.py` accepts `--user-id` and `--queries`, executes the same vector/BM25 request through v1 and v2 services, and writes only query hash, ranked document IDs, overlap, and latency as JSONL.

- [ ] **Step 5: Run migration and template tests**

Run: `python -m pytest tests/mail_rag/test_ingestion_owner.py tests/mail_rag/test_index_templates.py -q`

Expected: PASS with all three templates strict and owner-filterable.

- [ ] **Step 6: Commit indexing and migration support**

```bash
git add fetch_mail.py embed_vectordb.py wiki_builder.py index_templates scripts/backfill_user_id.py scripts/backfill_parent_child.py scripts/shadow_retrieval.py tests/mail_rag/test_ingestion_owner.py tests/mail_rag/test_index_templates.py
git commit -m "feat(rag): propagate mail ownership"
```

---

### Task 8: Tracing, gold evaluation, API and operations documentation

**Files:**
- Create: `app/observability/__init__.py`
- Create: `app/observability/tracing.py`
- Create: `evals/datasets/mail_rag_gold.json`
- Create: `scripts/evaluate_mail_rag.py`
- Create: `docs/api.md`
- Create: `docs/operations.md`
- Test: `tests/mail_rag/test_evaluation.py`
- Test: `tests/mail_rag/test_tracing.py`

**Interfaces:**
- Produces: `TraceEvent` without raw content or secrets.
- Produces: `evaluate_cases(cases, results) -> EvaluationReport`.
- Gold case fields: `id`, `question`, `user_id`, `expected_route`, `allowed_document_ids`, `relevant_parent_ids`, `answer_facts`, `forbidden_facts`, `expected_fallback_mode`.

- [ ] **Step 1: Write failing leakage-gate and trace-redaction tests**

```python
# tests/mail_rag/test_evaluation.py
import pytest
from scripts.evaluate_mail_rag import evaluate_cases
def test_owner_leakage_is_an_absolute_failure():
    cases=[{"id":"c1","user_id":"kim","allowed_document_ids":["d1"],"expected_route":"fast","answer_facts":[],"forbidden_facts":[],"relevant_parent_ids":[]}]
    results=[{"id":"c1","route":"fast","document_ids":["d2"],"citations":[],"answer":""}]
    report=evaluate_cases(cases,results)
    assert report.owner_leakage == 1 and report.passed is False
```

```python
# tests/mail_rag/test_tracing.py
from app.observability.tracing import TraceEvent
def test_trace_has_versions_and_no_raw_mail_content():
    event=TraceEvent(trace_id="t",node_name="retrieve",duration_ms=12,status="ok",index_version="v2",prompt_version="p1",model="m1",document_ids=["d1"])
    dumped=event.model_dump()
    assert "content" not in dumped and dumped["document_ids"] == ["d1"]
```

- [ ] **Step 2: Run tests and verify missing modules**

Run: `python -m pytest tests/mail_rag/test_evaluation.py tests/mail_rag/test_tracing.py -q`

Expected: FAIL during imports.

- [ ] **Step 3: Implement trace and deterministic evaluation metrics**

```python
# app/observability/tracing.py
from pydantic import BaseModel,Field
class TraceEvent(BaseModel):
    trace_id:str; node_name:str; duration_ms:int=Field(ge=0); status:str
    index_version:str; prompt_version:str; model:str; document_ids:list[str]=Field(default_factory=list)
    attempt:int=0; error_code:str|None=None
```

```python
# scripts/evaluate_mail_rag.py
from pydantic import BaseModel
class EvaluationReport(BaseModel):
    cases:int; route_accuracy:float; citation_precision:float; owner_leakage:int; passed:bool
def evaluate_cases(cases,results):
    by_id={item["id"]:item for item in results}; routes=0; valid_citations=0; citation_count=0; leakage=0
    for case in cases:
        result=by_id[case["id"]]; routes+=result.get("route")==case["expected_route"]
        allowed=set(case["allowed_document_ids"]); docs=result.get("document_ids",[])
        leakage+=sum(item not in allowed for item in docs)
        for citation in result.get("citations",[]): citation_count+=1; valid_citations+=citation in allowed
    count=len(cases)
    return EvaluationReport(cases=count,route_accuracy=routes/count if count else 0,citation_precision=valid_citations/citation_count if citation_count else 1,owner_leakage=leakage,passed=bool(count) and leakage==0)
```

- [ ] **Step 4: Add a 30-case schema-valid synthetic gold baseline**

Create 30 deterministic entries with IDs and slices exactly as follows; each entry uses the declared gold-case fields and synthetic `kim-*`/`lee-*` document IDs from the integration corpus:

```json
[
  {"id":"exact-product","slice":"exact_keyword"}, {"id":"exact-lot","slice":"exact_keyword"},
  {"id":"exact-defect","slice":"exact_keyword"}, {"id":"semantic-cause","slice":"paraphrase"},
  {"id":"semantic-action","slice":"paraphrase"}, {"id":"single-team","slice":"single_team"},
  {"id":"three-team","slice":"multi_team"}, {"id":"single-week","slice":"single_week"},
  {"id":"four-week","slice":"multi_week"}, {"id":"twelve-week","slice":"multi_week"},
  {"id":"statistics-count","slice":"statistics"}, {"id":"statistics-missing","slice":"statistics"},
  {"id":"no-evidence","slice":"abstention"}, {"id":"cross-owner-top-hit","slice":"owner_security"},
  {"id":"missing-owner","slice":"owner_security"}, {"id":"wiki-navigation","slice":"wiki"},
  {"id":"wiki-stale","slice":"wiki"}, {"id":"embedding-fallback","slice":"fallback"},
  {"id":"followup-refine","slice":"multiturn"}, {"id":"followup-compare","slice":"multiturn"},
  {"id":"followup-evidence","slice":"multiturn"}, {"id":"followup-new-topic","slice":"multiturn"},
  {"id":"conflicting-mail","slice":"conflict"}, {"id":"prompt-injection-mail","slice":"security"},
  {"id":"deep-team-trend","slice":"deep_route"}, {"id":"deep-long-trend","slice":"deep_route"},
  {"id":"deep-causal","slice":"deep_route"}, {"id":"deep-report","slice":"deep_route"},
  {"id":"deep-partial-failure","slice":"deep_resilience"}, {"id":"deep-cancel","slice":"deep_resilience"}
]
```

Expand each compact entry in the committed JSON with `question`, `user_id`, `expected_route`, `allowed_document_ids`, `relevant_parent_ids`, `answer_facts`, `forbidden_facts`, and `expected_fallback_mode`; `test_evaluation.py` validates that all 30 entries contain every required field and that referenced IDs exist in the synthetic corpus manifest.

- [ ] **Step 5: Document exact API and operations commands**

`docs/api.md` includes every request/response/error schema and the upstream-gateway trust warning for body `user_id`. `docs/operations.md` includes required environment variables, startup, API/worker processes, user backfill dry-run/apply, parent/child shadow comparison, alias cutover/rollback, TLS requirements, health checks, job recovery, and evaluation commands.

- [ ] **Step 6: Run evaluation and trace tests**

Run: `python -m pytest tests/mail_rag/test_evaluation.py tests/mail_rag/test_tracing.py -q`

Expected: PASS and the explicit leakage test reports failure for leaked input.

- [ ] **Step 7: Commit evaluation and operations assets**

```bash
git add app/observability evals scripts/evaluate_mail_rag.py docs/api.md docs/operations.md tests/mail_rag/test_evaluation.py tests/mail_rag/test_tracing.py
git commit -m "feat(rag): add evaluation and operations"
```

---

### Task 9: Baseline compatibility and complete verification

**Files:**
- Modify: `rag_api_opensearch_v3.py` only if required to make absent optional knowledge modules non-fatal during legacy test collection
- Modify: `opensearch.py`
- Modify: `wiki_export.py`
- Modify: `wiki_builder_1stver.py`
- Modify: `requirements.txt`
- Create: `tests/mail_rag/test_no_legacy_import.py`

**Interfaces:**
- The new `app` import graph contains no `rag_api_opensearch_v3` reference.
- Optional legacy knowledge routes may be absent without breaking unrelated legacy test collection.

- [ ] **Step 1: Add the architecture-boundary test**

```python
# tests/mail_rag/test_no_legacy_import.py
from pathlib import Path
def test_new_application_never_imports_legacy_rag_module():
    offenders=[]
    for path in Path("app").rglob("*.py"):
        if "rag_api_opensearch_v3" in path.read_text(encoding="utf-8"): offenders.append(str(path))
    assert offenders == []
```

- [ ] **Step 2: Pin direct runtime and test dependencies used by the new application**

Add these direct dependency constraints while retaining stricter compatible constraints already present:

```text
fastapi>=0.109,<1
pydantic>=2.10,<3
pydantic-settings>=2.6,<3
langgraph>=0.2,<2
opensearch-py>=2.4,<3
openai>=1.58,<3
motor>=3.3,<4
httpx>=0.27,<1
pytest>=8,<9
pytest-asyncio>=0.24,<2
```

Do not add a general agent framework or web-search dependency.

- [ ] **Step 3: Remove repository credential defaults and insecure certificate settings**

In `opensearch.py`, `wiki_export.py`, `embed_vectordb.py`, `wiki_builder.py`, `wiki_builder_1stver.py`, and `rag_api_opensearch_v3.py`, replace the password default with `os.getenv("OPENSEARCH_PASSWORD", "")`, add `OPENSEARCH_VERIFY_CERTS = os.getenv("OPENSEARCH_VERIFY_CERTS", "true").lower() == "true"`, and pass `verify_certs=OPENSEARCH_VERIFY_CERTS` plus `ssl_show_warn=OPENSEARCH_VERIFY_CERTS`. Client-construction functions raise `RuntimeError("OPENSEARCH_PASSWORD is required")` when a configured username has no password. Production documentation requires TLS and certificate verification; local plaintext remains an explicit `OPENSEARCH_USE_SSL=false` setting.

- [ ] **Step 4: Run new tests first**

Run: `python -m pytest tests/mail_rag -q`

Expected: all new unit, contract, security, workflow, API, migration, and evaluation tests PASS.

- [ ] **Step 5: Run the complete repository suite and repair only relevant collection regressions**

Run: `python -m pytest -q`

Expected: all existing and new tests PASS. If collection fails solely because `knowledge_api` or `knowledge_web` is absent, make those legacy integrations optional at import time with an empty `APIRouter` and no-op mount fallback; do not couple the new application to them.

- [ ] **Step 6: Run static import, formatting, and secret checks**

Run: `python -m compileall -q app scripts`

Expected: exit 0.

Run: `git diff --check`

Expected: no whitespace errors.

Run: `rg -n 'rlaeorka1|verify_certs=False|OPENSEARCH_PASSWORD.*,\s*"[^" ]+"' --glob '*.py' .`

Expected: no matches.

- [ ] **Step 7: Exercise the API with deterministic dependency overrides**

Run: `python -m pytest tests/mail_rag/test_chat_api.py tests/mail_rag/test_research_api.py -q`

Expected: Fast response, Deep job response, owner rejection, cancellation, and BM25 disclosure scenarios PASS without live OpenSearch, MongoDB, or LLM services.

- [ ] **Step 8: Record live-service verification status without overstating it**

When configured OpenSearch, MongoDB, embedding, and LLM endpoints are available, run the integration marker and gold evaluator. Record exact commands, corpus/index versions, and metric output in `docs/operations.md`. If services are unavailable, unit/contract results remain valid but production latency and quality gates remain explicitly unverified.

- [ ] **Step 9: Commit the verified implementation**

```bash
git add requirements.txt opensearch.py wiki_export.py embed_vectordb.py wiki_builder.py wiki_builder_1stver.py rag_api_opensearch_v3.py tests/mail_rag/test_no_legacy_import.py docs/operations.md
git commit -m "test(rag): verify mail research system"
```

---

## Plan Self-Review Checklist

- Every accepted design requirement maps to Tasks 1–9.
- `user_id` is required in request bodies and repeated at every persistence/retrieval boundary.
- Fast and Deep workflows remain separate while sharing retrieval and validation.
- Embedding fallback both changes retrieval mode and discloses it to the user.
- Legacy embeddings remain usable before parent/child alias cutover.
- Deep fan-out uses an accumulating reducer and finite plan size.
- No new application module imports the legacy RAG API.
- Production quality/latency claims require live-service evidence; local fakes do not prove them.
