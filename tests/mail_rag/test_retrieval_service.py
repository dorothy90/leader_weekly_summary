import asyncio
from copy import deepcopy

import pytest

from app.domain.chat import BM25_FALLBACK_DISCLOSURE
from app.domain.evidence import RetrievalFilters, SearchTask
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.retrieval.service import RetrievalService


def _hit(
    document_id: str,
    *,
    user_id: str | None = "kim",
    score: float | None = 9,
    **source,
):
    payload = {"text": "근거", "mail_id": "m1", "part_index": 0, **source}
    if user_id is not None:
        payload["user_id"] = user_id
    return {"_id": document_id, "_score": score, "_source": payload}


def _owner_filter(body: dict) -> dict:
    return body["query"]["bool"]["filter"][0]


class FakeSearch:
    def __init__(self, initial_hits=None, expansion_hits=None, aggregations=None):
        self.calls = []
        self.initial_hits = initial_hits or [_hit("d1")]
        self.expansion_hits = expansion_hits
        self.aggregations = aggregations or {}

    async def search(self, index, body):
        self.calls.append((index, deepcopy(body)))
        if body.get("size") == 0:
            return {"aggregations": self.aggregations}
        filters = body["query"]["bool"]["filter"]
        if any("terms" in item and "mail_id" in item["terms"] for item in filters):
            hits = (
                self.initial_hits
                if self.expansion_hits is None
                else self.expansion_hits
            )
        else:
            hits = self.initial_hits
        return {"hits": {"hits": deepcopy(hits)}}


class ParentSearch(FakeSearch):
    def __init__(self, *, parent_hits, **kwargs):
        super().__init__(**kwargs)
        self.parent_hits = parent_hits

    async def search(self, index, body):
        if index == "parent-read":
            self.calls.append((index, deepcopy(body)))
            return {"hits": {"hits": deepcopy(self.parent_hits)}}
        return await super().search(index, body)


class FakeEmbedding:
    async def embed(self, text):
        return [0.1, 0.2]


class BrokenEmbedding:
    async def embed(self, text):
        raise TimeoutError("embedding timeout with secret-token")


class BrokenSearch:
    async def search(self, index, body):
        raise ConnectionError("index down")


class VectorMismatchSearch(FakeSearch):
    async def search(self, index, body):
        if "knn" in str(body):
            self.calls.append((index, deepcopy(body)))
            raise ValueError("vector dimension mismatch with secret-vector-detail")
        return await super().search(index, body)


class CancelledVectorSearch(FakeSearch):
    async def search(self, index, body):
        if "knn" in str(body):
            self.calls.append((index, deepcopy(body)))
            raise asyncio.CancelledError()
        return await super().search(index, body)


class Bm25FailsWhileVectorBlocks:
    def __init__(self):
        self.vector_started = asyncio.Event()
        self.vector_cancelled = False

    async def search(self, index, body):
        if "knn" in str(body):
            self.vector_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.vector_cancelled = True
                raise
        await self.vector_started.wait()
        raise ConnectionError("bm25 unavailable")


def test_opensearch_failure_is_retryable_index_unavailable():
    service = RetrievalService(BrokenSearch(), FakeEmbedding(), child_index="mail")
    with pytest.raises(AppError) as error:
        asyncio.run(
            service.search(SearchTask(query="q"), PolicyContext.from_user_id("kim"))
        )
    assert error.value.code == ErrorCode.INDEX_UNAVAILABLE
    assert error.value.retryable is True


def test_cancelled_vector_search_propagates_cancellation():
    backend = CancelledVectorSearch()
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            service.search(
                SearchTask(query="수율"),
                PolicyContext.from_user_id("kim"),
            )
        )


def test_bm25_failure_cancels_blocked_vector_without_waiting_for_it():
    backend = Bm25FailsWhileVectorBlocks()
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    async def exercise():
        with pytest.raises(AppError) as error:
            await asyncio.wait_for(
                service.search(
                    SearchTask(query="수율"),
                    PolicyContext.from_user_id("kim"),
                ),
                timeout=0.2,
            )
        return error.value

    error = asyncio.run(exercise())

    assert error.code == ErrorCode.INDEX_UNAVAILABLE
    assert error.retryable is True
    assert backend.vector_cancelled is True


def test_vector_dimension_failure_uses_owner_scoped_bm25_and_disclosure():
    backend = VectorMismatchSearch()
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(SearchTask(query="수율"), PolicyContext.from_user_id("kim"))
    )

    assert result.mode == "bm25"
    assert result.embedding_error == "EMBEDDING_UNAVAILABLE"
    assert result.embedding_error_class == "ValueError"
    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]
    assert "secret-vector-detail" not in result.model_dump_json()
    assert all(
        _owner_filter(body) == {"term": {"user_id": "kim"}}
        for _, body in backend.calls
    )
    assert any("knn" in str(body) for _, body in backend.calls)
    assert any(
        '"match"' in str(body).replace("'", '"') for _, body in backend.calls
    )


def test_hybrid_vector_bm25_and_expansion_queries_are_owner_scoped():
    backend = FakeSearch()
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(
                query="수율",
                filters=RetrievalFilters(teams=["YIELD팀"]),
            ),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert result.mode == "hybrid"
    assert len(backend.calls) == 3
    assert all(
        _owner_filter(body) == {"term": {"user_id": "kim"}} for _, body in backend.calls
    )
    assert all(
        {"terms": {"team": ["YIELD팀"]}} in body["query"]["bool"]["filter"]
        for _, body in backend.calls
    )
    assert [item.user_id for item in result.evidence] == ["kim"]


def test_embedding_failure_runs_bm25_only_and_propagates_exact_disclosure():
    backend = FakeSearch()
    service = RetrievalService(backend, BrokenEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert result.mode == "bm25"
    assert result.embedding_error == "EMBEDDING_UNAVAILABLE"
    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]
    assert "secret-token" not in result.model_dump_json()
    assert len(backend.calls) == 2
    assert all(
        _owner_filter(body) == {"term": {"user_id": "kim"}} for _, body in backend.calls
    )
    assert not any("knn" in str(body) for _, body in backend.calls)


def test_final_legacy_materialization_rechecks_owner_and_hides_raw_paths():
    backend = FakeSearch(
        initial_hits=[_hit("seed")],
        expansion_hits=[
            _hit("owned", document_locator="/srv/mail/kim/message.json"),
            _hit("cross-owner", user_id="lee", text="타인 근거"),
            _hit("missing-owner", user_id=None, text="소유자 없는 근거"),
        ],
    )
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert [item.document_id for item in result.evidence] == ["owned"]
    assert result.evidence[0].source_locator is None
    expansion_body = backend.calls[-1][1]
    assert _owner_filter(expansion_body) == {"term": {"user_id": "kim"}}
    assert {"terms": {"mail_id": ["m1"]}} in expansion_body["query"]["bool"]["filter"]


def test_sorted_legacy_expansion_accepts_null_opensearch_score():
    backend = FakeSearch(
        initial_hits=[_hit("seed")],
        expansion_hits=[_hit("owned", score=None)],
    )
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert [item.document_id for item in result.evidence] == ["owned"]
    assert result.evidence[0].score > 0


def test_legacy_expansion_materializes_only_adjacent_parts_around_late_match():
    backend = FakeSearch(
        initial_hits=[_hit("seed-8", part_index=8, text="일치 부분")],
        expansion_hits=[
            _hit(f"part-{part}", part_index=part, text=f"문맥 {part}")
            for part in range(10)
        ],
    )
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert [item.document_id for item in result.evidence] == ["part-8"]
    assert result.evidence[0].excerpt == "문맥 7\n\n문맥 8\n\n문맥 9"
    expansion_body = backend.calls[-1][1]
    assert expansion_body["size"] == 3
    neighborhood_filters = expansion_body["query"]["bool"]["must"][0]["bool"]["should"][
        0
    ]["bool"]["filter"]
    assert {"terms": {"part_index": [7, 8, 9]}} in neighborhood_filters


def test_source_locator_rejects_url_credentials_and_secret_query_values():
    backend = FakeSearch(
        initial_hits=[
            _hit(
                "seed",
                document_locator="https://kim:password@example.test/mail?token=secret",
            )
        ],
        expansion_hits=[],
    )
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert result.evidence[0].source_locator is None
    assert "password" not in result.model_dump_json()
    assert "secret" not in result.model_dump_json()


@pytest.mark.parametrize(
    "locator",
    [
        "https://example.test/mail?sig=secret",
        "https://example.test/mail#access_token=secret",
        "mail:/srv/private/message.json",
        "wiki:/etc/passwd",
    ],
)
def test_source_locator_rejects_noncanonical_secret_or_path_forms(locator):
    backend = FakeSearch(
        initial_hits=[_hit("seed", document_locator=locator)],
        expansion_hits=[],
    )
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert result.evidence[0].source_locator is None


def test_legacy_expansion_preserves_maximum_fused_score_for_mail():
    backend = FakeSearch(
        initial_hits=[
            _hit("best-seed", part_index=1),
            _hit("weaker-seed", part_index=2),
        ],
        expansion_hits=[
            _hit("part-1", part_index=1),
            _hit("part-2", part_index=2),
        ],
    )
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert result.evidence[0].score == pytest.approx(2 / 61)


def test_v2_parent_lookup_is_owner_scoped_and_materializes_only_owned_parent():
    backend = ParentSearch(
        initial_hits=[_hit("child-1", parent_id="p1", team="YIELD팀")],
        parent_hits=[
            _hit("parent-1", parent_id="p1", text="부모 근거", team="YIELD팀"),
            _hit(
                "cross-parent",
                user_id="lee",
                parent_id="p1",
                text="타인 부모",
            ),
            _hit(
                "missing-owner-parent",
                user_id=None,
                parent_id="p1",
                text="소유자 없는 부모",
            ),
        ],
    )
    service = RetrievalService(
        backend,
        FakeEmbedding(),
        child_index="weekly_mail",
        parent_index="parent-read",
    )

    result = asyncio.run(
        service.search(
            SearchTask(
                query="수율",
                filters=RetrievalFilters(teams=["YIELD팀"]),
            ),
            PolicyContext.from_user_id("kim"),
        )
    )

    parent_calls = [call for call in backend.calls if call[0] == "parent-read"]
    assert len(parent_calls) == 1
    parent_body = parent_calls[0][1]
    assert _owner_filter(parent_body) == {"term": {"user_id": "kim"}}
    assert {"terms": {"team": ["YIELD팀"]}} in parent_body["query"]["bool"]["filter"]
    assert {"terms": {"parent_id": ["p1"]}} in parent_body["query"]["bool"]["filter"]
    assert [item.document_id for item in result.evidence] == ["parent-1"]
    assert result.evidence[0].excerpt == "부모 근거"
    assert result.evidence[0].user_id == "kim"
    assert all(index != "weekly_mail" for index, _ in backend.calls[2:])


def test_unusable_parent_hit_falls_back_to_owner_scoped_legacy_expansion():
    backend = ParentSearch(
        initial_hits=[_hit("child-1", parent_id="p1", part_index=4)],
        parent_hits=[
            _hit("cross-parent", user_id="lee", parent_id="p1", text="타인 부모"),
            _hit("missing-parent", user_id=None, parent_id="p1", text="무소유 부모"),
        ],
        expansion_hits=[_hit("legacy-part", part_index=4, text="소유자 문맥")],
    )
    service = RetrievalService(
        backend,
        FakeEmbedding(),
        child_index="weekly_mail",
        parent_index="parent-read",
    )

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert [item.document_id for item in result.evidence] == ["legacy-part"]
    assert all(item.user_id == "kim" for item in result.evidence)
    assert "타인 부모" not in result.model_dump_json()
    assert "무소유 부모" not in result.model_dump_json()
    parent_body = next(body for index, body in backend.calls if index == "parent-read")
    assert _owner_filter(parent_body) == {"term": {"user_id": "kim"}}
    assert _owner_filter(backend.calls[-1][1]) == {"term": {"user_id": "kim"}}


def test_documents_without_user_id_remain_invisible():
    backend = FakeSearch(
        initial_hits=[_hit("missing", user_id=None)],
        expansion_hits=[_hit("still-missing", user_id=None)],
    )
    service = RetrievalService(backend, FakeEmbedding(), child_index="weekly_mail")

    result = asyncio.run(
        service.search(
            SearchTask(query="수율"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert result.evidence == []


@pytest.mark.parametrize(
    ("task", "expected_index"),
    [
        (SearchTask(query="공정", source="wiki"), "wiki-v2"),
        (SearchTask(query="집계", source="statistics"), "weekly_mail"),
    ],
)
def test_non_mail_lookups_are_exact_owner_scoped(task, expected_index):
    backend = FakeSearch(
        initial_hits=[_hit("wiki", title="위키")],
        aggregations={"by_team": {"buckets": []}},
    )
    service = RetrievalService(
        backend,
        FakeEmbedding(),
        child_index="weekly_mail",
        wiki_index="wiki-v2",
    )

    result = asyncio.run(service.search(task, PolicyContext.from_user_id("kim")))

    assert backend.calls
    assert all(index == expected_index for index, _ in backend.calls)
    assert all(
        _owner_filter(body) == {"term": {"user_id": "kim"}} for _, body in backend.calls
    )
    assert all(item.user_id == "kim" for item in result.evidence)
