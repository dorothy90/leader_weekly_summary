import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.retrieval.embedding import OpenAIEmbeddingGateway


@pytest.mark.asyncio
async def test_cache_singleflight_and_no_shared_mutation():
    async def create(**kwargs):
        await asyncio.sleep(0.02)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])])

    create = AsyncMock(side_effect=create)
    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)), "model"
    )
    results = await asyncio.gather(*[g.embed("same") for _ in range(4)])
    assert create.await_count == 1
    results[0][0] = 99
    assert await g.embed("same") == [0.1, 0.2]
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_failed_embedding_not_cached():
    create = AsyncMock(
        side_effect=[
            ValueError("bad"),
            SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])]),
        ]
    )
    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)), "model"
    )
    with pytest.raises(ValueError):
        await g.embed("same")
    assert await g.embed("same") == [0.1]


@pytest.mark.asyncio
async def test_cancelled_waiter_does_not_cancel_shared_embedding():
    async def create(**kwargs):
        await asyncio.sleep(0.05)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])])

    create = AsyncMock(side_effect=create)
    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)), "model"
    )
    a = asyncio.create_task(g.embed("same"))
    b = asyncio.create_task(g.embed("same"))
    await asyncio.sleep(0.01)
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    assert await b == [0.1]
    assert create.await_count == 1


@pytest.mark.asyncio
async def test_transient_failure_retried_once_and_cached():
    import httpx
    from openai import APIConnectionError

    create = AsyncMock(
        side_effect=[
            APIConnectionError(
                request=httpx.Request("POST", "https://example.invalid")
            ),
            SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])]),
        ]
    )
    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)), "model"
    )
    assert await g.embed("q") == [0.1]
    assert await g.embed("q") == [0.1]
    assert create.await_count == 2


@pytest.mark.asyncio
async def test_expired_cache_and_model_change_refetch():
    create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])])
    )
    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)),
        "model",
        cache_ttl_seconds=0.01,
    )
    await g.embed("q")
    await asyncio.sleep(0.02)
    await g.embed("q")
    g.model = "different"
    await g.embed("q")
    assert create.await_count == 3


@pytest.mark.asyncio
async def test_slow_valid_embedding_reaches_real_knn_path_after_three_seconds():
    from app.retrieval.quick_rag import QuickRetrieval
    from app.domain.quick_rag import QuickPlan
    from app.domain.policy import PolicyContext

    seen = []

    class Backend:
        async def search(self, index, body):
            seen.append(body)
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "1",
                            "_source": {"source_id": "parent", "text": "confirmed"},
                        }
                    ]
                }
            }

    class Slow:
        async def embed(self, text):
            await asyncio.sleep(3.1)
            return [0.1]

    r = QuickRetrieval(
        Backend(),
        Slow(),
        weekly_index="w",
        mail_index="m",
        calendar_index="c",
        wiki_index="i",
        compatible_sources=["weekly_mail", "ews_mail"],
        require_vector=True,
    )
    o = await r.search_expanded(
        QuickPlan(source_scope="mail", query="q", search_queries=["alternate"]),
        PolicyContext.from_user_id("kim"),
        embedding_timeout_seconds=7.5,
    )
    assert o.retrieval_mode == "hybrid" and not o.vector_required_failed
    assert o.vector_sources == ["weekly_mail", "ews_mail"]
    assert any("knn" in str(body) for body in seen)
    assert not any("키워드 검색만" in x for x in o.disclosures)


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", ["q", ""])
async def test_failed_vector_cannot_be_replaced_by_successful_keyword_lanes(primary):
    from app.retrieval.quick_rag import QuickRetrieval
    from app.domain.quick_rag import QuickPlan
    from app.domain.policy import PolicyContext
    from app.graphs.quick_rag import QuickRAGWorkflow
    from app.domain.chat import ChatRequest

    class Backend:
        async def search(self, index, body):
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "1",
                            "_source": {"source_id": "parent", "text": "keyword only"},
                        }
                    ]
                }
            }

    class Failed:
        async def embed(self, text):
            raise TimeoutError()

    class Interpreter:
        async def interpret(self, *args):
            return QuickPlan(
                query=primary,
                source_scope="mail",
                search_queries=["alternate", "other"],
            )

    r = QuickRetrieval(
        Backend(),
        Failed(),
        weekly_index="w",
        mail_index="m",
        calendar_index="c",
        wiki_index="i",
        compatible_sources=["weekly_mail", "ews_mail"],
        require_vector=True,
    )
    o = await r.search_expanded(
        await Interpreter().interpret(), PolicyContext.from_user_id("kim")
    )
    assert o.vector_required_failed and not o.documents
    result = await QuickRAGWorkflow(
        r, Interpreter(), None, evidence_agent=object()
    ).invoke(ChatRequest(user_id="kim", message="q"), PolicyContext.from_user_id("kim"))
    assert result.execution.error_code == "VECTOR_SEARCH_UNAVAILABLE"
    assert not result.evidence


@pytest.mark.asyncio
async def test_detailed_preserves_primary_vector_failure():
    from types import SimpleNamespace
    from app.graphs.detailed_rag import _Retrieval
    from app.retrieval.quick_rag import QuickSearchOutcome
    from app.domain.quick_rag import QuickPlan
    from app.domain.policy import PolicyContext

    class Base:
        async def search(self, plan, policy, **kwargs):
            return QuickSearchOutcome(vector_required_failed=True)

    retrieval = _Retrieval(
        Base(), SimpleNamespace(plan=SimpleNamespace(subqueries=[])), None, "q", 1
    )
    result = await retrieval.search(
        QuickPlan(query="q"), PolicyContext.from_user_id("kim")
    )
    assert result.vector_required_failed and not result.documents


@pytest.mark.asyncio
async def test_slow_provider_is_hedged_and_loser_cancelled():
    cancelled = asyncio.Event()
    calls = []

    async def create(**kwargs):
        p = kwargs["extra_body"]["provider"]["only"][0]
        calls.append(p)
        if p == "primary":
            try:
                await asyncio.sleep(1)
            finally:
                cancelled.set()
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.2])])

    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)),
        "model",
        providers=["primary", "backup"],
        hedge_delay_seconds=0.01,
    )
    assert await asyncio.wait_for(g.embed("q"), 0.2) == [0.2]
    assert calls == ["primary", "backup"]
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_fast_provider_does_not_duplicate_request():
    create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])])
    )
    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)),
        "model",
        providers=["primary", "backup"],
        hedge_delay_seconds=0.01,
    )
    assert await g.embed("q") == [0.1]
    assert create.await_count == 1
    assert create.call_args.kwargs["extra_body"]["provider"]["only"] == ["primary"]


@pytest.mark.asyncio
async def test_provider_connection_failure_uses_backup_immediately():
    import httpx
    from openai import APIConnectionError

    async def create(**kwargs):
        if kwargs["extra_body"]["provider"]["only"] == ["primary"]:
            raise APIConnectionError(
                request=httpx.Request("POST", "https://example.invalid")
            )
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.2])])

    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)),
        "model",
        providers=["primary", "backup"],
        hedge_delay_seconds=1,
    )
    assert await asyncio.wait_for(g.embed("q"), 0.2) == [0.2]


@pytest.mark.asyncio
async def test_mail_content_topic_is_query_not_unsupported_filter():
    from app.retrieval.quick_rag import QuickRetrieval
    from app.domain.quick_rag import QuickPlan, QuickConditions
    from app.domain.policy import PolicyContext

    seen = []

    class Backend:
        async def search(self, index, body):
            seen.append((index, body))
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "1",
                            "_source": {
                                "source_id": "parent",
                                "text": "장비 정기 점검",
                            },
                        }
                    ]
                }
            }

    class Embed:
        async def embed(self, text):
            assert "장비 정기 점검" in text
            return [0.1]

    r = QuickRetrieval(
        Backend(),
        Embed(),
        weekly_index="w",
        mail_index="m",
        calendar_index="c",
        wiki_index="i",
        compatible_sources=["ews_mail"],
        require_vector=True,
    )
    o = await r.search(
        QuickPlan(
            source_scope="mail",
            conditions=QuickConditions(
                sender_email="kim@example.com", topic="장비 정기 점검"
            ),
        ),
        PolicyContext.from_user_id("kim"),
    )
    assert o.vector_sources == ["ews_mail"] and o.documents
    assert not any("topic" in s for s in o.disclosures)
    assert o.resolved_plan.conditions.topic is None
    assert o.resolved_plan.conditions.sender_email == "kim@example.com"
    for _, body in seen:
        filters = body["query"]["bool"]["filter"]
        assert any("sender_email" in str(f) for f in filters)
        assert any("employee_id" in str(f) for f in filters)


@pytest.mark.asyncio
async def test_wiki_explicit_topic_filter_is_retained():
    from app.retrieval.quick_rag import QuickRetrieval
    from app.domain.quick_rag import QuickPlan, QuickConditions
    from app.domain.policy import PolicyContext

    seen = []

    class Backend:
        async def search(self, index, body):
            seen.append(body)
            return {"hits": {"hits": []}}

    r = QuickRetrieval(
        Backend(),
        None,
        weekly_index="w",
        mail_index="m",
        calendar_index="c",
        wiki_index="i",
    )
    await r.search(
        QuickPlan(
            source_scope="wiki",
            intent="list",
            conditions=QuickConditions(topic="yield"),
        ),
        PolicyContext.from_user_id("kim"),
    )
    assert {"term": {"topic": "yield"}} in seen[0]["query"]["bool"]["filter"]


@pytest.mark.asyncio
async def test_malformed_backup_does_not_cancel_valid_primary():
    async def create(**kwargs):
        if kwargs["extra_body"]["provider"]["only"] == ["backup"]:
            return SimpleNamespace(data=[])
        await asyncio.sleep(0.03)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.3])])

    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)),
        "model",
        providers=["primary", "backup"],
        hedge_delay_seconds=0.01,
    )
    assert await g.embed("q") == [0.3]


@pytest.mark.asyncio
async def test_hedge_deadline_cancels_both_and_does_not_cache():
    cancelled = []

    async def create(**kwargs):
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(kwargs["extra_body"]["provider"]["only"][0])

    g = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)),
        "model",
        providers=["primary", "backup"],
        hedge_delay_seconds=0.01,
        timeout_seconds=0.04,
    )
    with pytest.raises(TimeoutError):
        await g.embed("q")
    assert sorted(cancelled) == ["backup", "primary"]
    assert not g._cache and not g._inflight
