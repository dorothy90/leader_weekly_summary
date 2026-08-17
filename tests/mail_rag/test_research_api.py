import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps

import httpx
import pytest

from app.api.dependencies import ServiceContainer
from app.api.main import create_app
from app.domain.chat import BM25_FALLBACK_DISCLOSURE
from app.domain.errors import AppError
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.domain.research import ResearchStatus
from app.domain.research import RESEARCH_ABSTENTION
from app.graphs.deep_research import DeepCoordinator, DeepResearchResult
from app.persistence.research_jobs import (
    InMemoryResearchJobStore,
    MongoResearchJobStore,
)
from app.workers.research import ResearchWorker


def async_test(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))

    return wrapped


class ASGIClient:
    def __init__(self, app):
        self.app = app

    def post(self, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as session:
                return await session.post(path, **kwargs)

        return asyncio.run(send())


def container(jobs):
    return ServiceContainer(
        agentic=None,
        conversations=None,
        jobs=jobs,
    )


def evidence(owner="kim", evidence_id="S1", document_id="doc-1"):
    return Evidence(
        evidence_id=evidence_id,
        source_type="mail",
        document_id=document_id,
        title="주간 보고",
        excerpt="확인된 근거",
        score=1,
        user_id=owner,
        acl_decision_id="acl",
        content_hash="hash",
    )


@async_test
async def test_job_status_and_cancel_are_owner_scoped_and_idempotent():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")

    first = await store.request_cancel(job.job_id, owner)
    second = await store.request_cancel(job.job_id, owner)

    assert first.status == second.status == ResearchStatus.CANCELLED
    with pytest.raises(AppError):
        await store.get(job.job_id, PolicyContext.from_user_id("lee"))


@async_test
async def test_claim_reclaims_expired_lease_and_stale_worker_cannot_complete():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")
    first = await store.claim(lease_seconds=1)
    store.jobs[job.job_id] = first.model_copy(
        update={"lease_until": datetime.now(UTC) - timedelta(seconds=1)}
    )
    second = await store.claim(lease_seconds=60)

    with pytest.raises(AppError):
        await store.complete(
            job.job_id,
            owner,
            first.lease_token,
            DeepResearchResult(report="old", completed_sub_questions=1, rounds=1),
        )
    completed = await store.complete(
        job.job_id,
        owner,
        second.lease_token,
        DeepResearchResult(report="new", completed_sub_questions=1, rounds=1),
    )
    repeated = await store.complete(
        job.job_id,
        owner,
        second.lease_token,
        DeepResearchResult(report="ignored", completed_sub_questions=1, rounds=1),
    )
    assert completed.result_markdown == repeated.result_markdown == RESEARCH_ABSTENTION


@async_test
async def test_reclaimed_job_keeps_safe_research_checkpoint_for_resume():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")
    claimed = await store.claim(lease_seconds=60)
    await store.checkpoint(
        job.job_id,
        owner,
        claimed.lease_token,
        {
            "stage": "research_complete",
            "progress": 42,
            "completed_sub_questions": ["A 원인"],
            "rounds_completed": 1,
            "compressed_evidence": [],
            "checkpoint": {
                "pending_queries": [],
                "branch_results": [],
                "searches": 1,
                "rounds": 1,
            },
        },
    )
    store.jobs[job.job_id] = store.jobs[job.job_id].model_copy(
        update={"lease_until": datetime.now(UTC) - timedelta(seconds=1)}
    )
    reclaimed = await store.claim()
    assert reclaimed.stage == "research_complete"
    assert reclaimed.progress == 42
    assert reclaimed.completed_sub_questions == ["A 원인"]
    assert reclaimed.checkpoint["searches"] == 1


@async_test
async def test_expired_unreclaimed_lease_cannot_complete_or_fail():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")
    claimed = await store.claim()
    store.jobs[job.job_id] = claimed.model_copy(
        update={"lease_until": datetime.now(UTC) - timedelta(microseconds=1)}
    )

    with pytest.raises(AppError):
        await store.complete(
            job.job_id,
            owner,
            claimed.lease_token,
            DeepResearchResult(report="stale", completed_sub_questions=1, rounds=1),
        )
    with pytest.raises(AppError):
        await store.fail(job.job_id, owner, claimed.lease_token, "STALE")


@async_test
async def test_expired_cancelling_job_is_reclaimed_for_deterministic_finalization():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")
    claimed = await store.claim()
    cancelling = await store.request_cancel(job.job_id, owner)
    store.jobs[job.job_id] = cancelling.model_copy(
        update={"lease_until": datetime.now(UTC) - timedelta(microseconds=1)}
    )

    reclaimed = await store.claim()

    assert reclaimed.status == ResearchStatus.CANCELLING
    assert reclaimed.lease_token != claimed.lease_token


@async_test
async def test_mongo_terminal_cas_requires_an_unexpired_lease():
    owner = PolicyContext.from_user_id("kim")
    memory = InMemoryResearchJobStore()
    job = await memory.create(owner, "trace", "question", "plan")
    claimed = await memory.claim()

    class Collection:
        query = None

        async def find_one_and_update(self, query, update, **_kwargs):
            self.query = query
            document = claimed.model_dump(mode="python")
            document.update(update["$set"])
            return document

    collection = Collection()
    await MongoResearchJobStore(collection).complete(
        job.job_id,
        owner,
        claimed.lease_token,
        DeepResearchResult(report="ignored", completed_sub_questions=1, rounds=1),
    )

    assert "$gt" in collection.query["lease_until"]


@async_test
async def test_mongo_cancel_catches_claim_race_instead_of_returning_running():
    owner = PolicyContext.from_user_id("kim")
    memory = InMemoryResearchJobStore()
    queued = await memory.create(owner, "trace", "question", "plan")

    class RacingCollection:
        def __init__(self):
            self.current = queued
            self.calls = 0

        async def find_one(self, query):
            if query.get("_id") == queued.job_id and query.get("user_id") == "kim":
                return self.current.model_dump(mode="python")
            return None

        async def find_one_and_update(self, query, update, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                self.current = self.current.model_copy(
                    update={
                        "status": ResearchStatus.RUNNING,
                        "lease_token": "worker",
                        "lease_until": datetime.now(UTC) + timedelta(seconds=60),
                    }
                )
                return None
            if self.current.status in query.get("status", {}).get("$in", []):
                self.current = self.current.model_copy(update=update["$set"])
                return self.current.model_dump(mode="python")
            return None

    result = await MongoResearchJobStore(RacingCollection()).request_cancel(
        queued.job_id, owner
    )

    assert result.status == ResearchStatus.CANCELLING


@async_test
async def test_store_persists_only_actually_cited_validated_safe_evidence():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")
    claimed = await store.claim()
    completed = await store.complete(
        job.job_id,
        owner,
        claimed.lease_token,
        DeepResearchResult(
            report="확인된 결과 [S1]",
            evidence=[
                evidence("kim", "S1", "/srv/private/one.json"),
                evidence("kim", "S2", "uncited"),
            ],
            completed_sub_questions=1,
            rounds=1,
            citation_valid=True,
        ),
    )

    assert [item.evidence_id for item in completed.result_evidence] == ["S1"]
    assert "/srv" not in completed.model_dump_json()


@async_test
async def test_store_fails_closed_when_result_contains_foreign_evidence():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")
    claimed = await store.claim()
    completed = await store.complete(
        job.job_id,
        owner,
        claimed.lease_token,
        DeepResearchResult(
            report="외부 결과 [S1]",
            evidence=[evidence("lee")],
            completed_sub_questions=1,
            rounds=1,
            citation_valid=True,
        ),
    )

    assert completed.result_evidence == []
    assert "외부 결과" not in completed.result_markdown


@async_test
async def test_store_replaces_empty_or_unvalidated_report_with_fixed_abstention():
    for result in (
        DeepResearchResult(
            report="arbitrary unsupported report",
            completed_sub_questions=1,
            rounds=1,
            citation_valid=True,
        ),
        DeepResearchResult(
            report="looks cited [S1]",
            evidence=[evidence()],
            completed_sub_questions=1,
            rounds=1,
            citation_valid=False,
        ),
    ):
        store = InMemoryResearchJobStore()
        owner = PolicyContext.from_user_id("kim")
        job = await store.create(owner, "trace", "question", "plan")
        claimed = await store.claim()
        completed = await store.complete(job.job_id, owner, claimed.lease_token, result)
        assert completed.result_markdown == RESEARCH_ABSTENTION
        assert completed.result_evidence == []


@async_test
async def test_job_persists_sanitized_filters_for_worker_retrieval():
    from app.domain.evidence import RetrievalFilters

    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    filters = RetrievalFilters(
        teams=["YIELD팀", "/srv/private/team"],
        weeks=["2026-08"],
        mail_type="weekly_report",
    )

    job = await store.create(owner, "trace", "question", "plan", filters)

    assert job.filters.weeks == ["2026-08"]
    assert job.filters.mail_type == "weekly_report"
    assert "/srv" not in job.model_dump_json()


@async_test
async def test_persistence_normalizes_exact_bm25_disclosure_once():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")
    claimed = await store.claim()
    repeated = (
        f"{RESEARCH_ABSTENTION}\n{BM25_FALLBACK_DISCLOSURE}\n{BM25_FALLBACK_DISCLOSURE}"
    )
    completed = await store.complete(
        job.job_id,
        owner,
        claimed.lease_token,
        DeepResearchResult(
            report=repeated,
            completed_sub_questions=1,
            rounds=1,
            disclosures=[BM25_FALLBACK_DISCLOSURE, BM25_FALLBACK_DISCLOSURE],
            citation_valid=True,
        ),
    )

    assert completed.result_markdown.count(BM25_FALLBACK_DISCLOSURE) == 1
    assert completed.disclosures == [BM25_FALLBACK_DISCLOSURE]


def test_public_job_fetch_hides_foreign_existence_and_sanitizes_output():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = asyncio.run(
        store.create(
            owner,
            "trace-secret",
            "/srv/private/question password=hunter2",
            "<analysis>private</analysis> 안전한 계획",
        )
    )
    api = ASGIClient(create_app(container(store)))

    foreign = api.post(f"/v1/research/{job.job_id}/status", json={"user_id": "lee"})
    missing = api.post("/v1/research/missing/status", json={"user_id": "lee"})
    owned = api.post(f"/v1/research/{job.job_id}/status", json={"user_id": "kim"})

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["error"] == missing.json()["error"]
    assert owned.status_code == 200
    serialized = str(owned.json()).casefold()
    for secret in (
        "user_id",
        "question",
        "trace-secret",
        "/srv",
        "hunter2",
        "analysis",
    ):
        assert secret not in serialized


def test_public_status_returns_only_cited_safe_reference_fields():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")

    async def finish():
        job = await store.create(owner, "trace", "question", "plan")
        claimed = await store.claim()
        await store.complete(
            job.job_id,
            owner,
            claimed.lease_token,
            DeepResearchResult(
                report="결과 [S1]",
                evidence=[evidence("kim", "S1", "/srv/private/one.json")],
                completed_sub_questions=1,
                rounds=1,
                citation_valid=True,
            ),
        )
        return job

    job = asyncio.run(finish())
    response = ASGIClient(create_app(container(store))).post(
        f"/v1/research/{job.job_id}/status", json={"user_id": "kim"}
    )

    assert response.status_code == 200
    assert response.json()["result_markdown"] == "결과 [S1]"
    assert [item["evidence_id"] for item in response.json()["references"]] == ["S1"]
    assert "/srv" not in str(response.json())


def test_public_status_replaces_corrupt_empty_evidence_report_with_abstention():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = asyncio.run(store.create(owner, "trace", "question", "plan"))
    store.jobs[job.job_id] = store.jobs[job.job_id].model_copy(
        update={
            "status": ResearchStatus.COMPLETED,
            "result_markdown": "unsupported arbitrary report",
            "result_evidence": [],
        }
    )

    response = ASGIClient(create_app(container(store))).post(
        f"/v1/research/{job.job_id}/status", json={"user_id": "kim"}
    )

    assert response.status_code == 200
    assert response.json()["result_markdown"] == RESEARCH_ABSTENTION
    assert response.json()["references"] == []


def test_cancel_and_retry_routes_are_owner_scoped_and_idempotent():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = asyncio.run(store.create(owner, "trace", "question", "plan"))
    api = ASGIClient(create_app(container(store)))

    cancelled = api.post(f"/v1/research/{job.job_id}/cancel", json={"user_id": "kim"})
    cancelled_again = api.post(
        f"/v1/research/{job.job_id}/cancel", json={"user_id": "kim"}
    )
    retried = api.post(f"/v1/research/{job.job_id}/retry", json={"user_id": "kim"})

    assert cancelled.json()["status"] == "cancelled"
    assert cancelled_again.json()["status"] == "cancelled"
    assert retried.json()["status"] == "queued"


def test_terminal_event_stream_is_owner_scoped_and_contains_no_internal_fields():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = asyncio.run(store.create(owner, "trace-secret", "question", "plan"))
    asyncio.run(store.request_cancel(job.job_id, owner))
    api = ASGIClient(create_app(container(store)))

    response = api.post(f"/v1/research/{job.job_id}/events", json={"user_id": "kim"})

    assert response.status_code == 200
    assert '"status": "cancelled"' in response.text
    for internal in ("user_id", "trace-secret", "question", "lease_token"):
        assert internal not in response.text


def test_research_routes_reject_invalid_body_owner_before_store_access():
    response = ASGIClient(create_app(container(InMemoryResearchJobStore()))).post(
        "/v1/research/missing/status", json={"user_id": "kim/dh"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_USER_ID"


@async_test
async def test_running_cancel_is_deterministic_and_worker_does_not_complete_it():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    job = await store.create(owner, "trace", "question", "plan")
    claimed = await store.claim()
    cancelling = await store.request_cancel(job.job_id, owner)

    assert cancelling.status == ResearchStatus.CANCELLING
    with pytest.raises(AppError):
        await store.complete(
            job.job_id,
            owner,
            claimed.lease_token,
            DeepResearchResult(
                report="must not persist", completed_sub_questions=1, rounds=1
            ),
        )
    cancelled = await store.mark_cancelled(job.job_id, owner, claimed.lease_token)
    assert cancelled.status == ResearchStatus.CANCELLED


class Workflow:
    def __init__(self, result=None, error=None):
        self.result = result or DeepResearchResult(
            report="완료", completed_sub_questions=1, rounds=1
        )
        self.error = error
        self.calls = []

    async def invoke(self, question, policy, filters, **kwargs):
        self.calls.append((question, policy, filters))
        if self.error:
            raise self.error
        return self.result


@async_test
async def test_worker_claims_once_and_persists_only_safe_result():
    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    await store.create(owner, "trace", "question", "plan")
    workflow = Workflow(
        DeepResearchResult(
            report="<analysis>secret</analysis> 완료 password=hunter2",
            completed_sub_questions=1,
            rounds=1,
        )
    )
    worker = ResearchWorker(store, workflow)

    assert await worker.run_once() is True
    assert await worker.run_once() is False
    stored = next(iter(store.jobs.values()))
    assert stored.status == ResearchStatus.COMPLETED
    assert "secret" not in stored.result_markdown
    assert "hunter2" not in stored.result_markdown


@async_test
async def test_worker_passes_exact_persisted_filters_to_deep_workflow():
    from app.domain.evidence import RetrievalFilters

    store = InMemoryResearchJobStore()
    owner = PolicyContext.from_user_id("kim")
    filters = RetrievalFilters(teams=["YIELD팀"], weeks=["2026-08"])
    await store.create(owner, "trace", "question", "plan", filters)
    workflow = Workflow()

    assert await ResearchWorker(store, workflow).run_once() is True
    assert workflow.calls[0][2] == filters


@dataclass
class Request:
    user_id: str
    message: str
    filters: object


@dataclass
class Filters:
    teams: list[str]
    weeks: list[str]


@async_test
async def test_coordinator_requires_exact_request_policy_owner():
    coordinator = DeepCoordinator(InMemoryResearchJobStore())
    request = Request("lee", "질문", Filters([], []))

    with pytest.raises(AppError):
        await coordinator.enqueue(request, PolicyContext.from_user_id("kim"), "trace")
