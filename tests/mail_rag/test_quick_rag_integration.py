import asyncio
from types import SimpleNamespace

from app.api.main import create_app
from app.config.settings import Settings


def test_only_chat_runtime_routes_exposed():
    app = create_app(SimpleNamespace(traces=None))
    paths = {route.path for route in app.routes}
    assert "/v1/chat" in paths
    assert not any(
        path.startswith("/v1/research") or path.startswith("/stats") for path in paths
    )


def test_speed_settings_use_real_four_sources_and_bounded_timeouts(monkeypatch):
    monkeypatch.delenv("QUICK_SOURCE_TIMEOUT_SECONDS", raising=False)
    settings = Settings(_env_file=None)
    assert settings.wiki_index == "wiki_summaries"
    assert settings.quick_deadline_seconds == 20
    assert settings.quick_source_timeout_seconds == 4
    assert settings.quick_shared_sources is True


def test_quick_gateways_build_only_interpret_and_answer_without_retries(monkeypatch):
    import app.api.dependencies as dep

    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", Client)
    settings = Settings(
        _env_file=None,
        openrouter_api_key="test",
        openrouter_routing_model="interpret",
        openrouter_answer_model="answer",
    )
    interpret, answer = dep.build_quick_llm_gateways(settings)
    assert interpret.model == "interpret"
    assert answer.model == "answer"
    assert all(call["max_retries"] == 0 for call in calls)
    assert interpret.output_token_limit == 1200
    assert answer.output_token_limit == 700


async def _roundtrip_quick_state():
    from app.domain.quick_rag import QuickMemory, QuickPlan, QuickResultRef
    from app.domain.policy import PolicyContext
    from app.persistence.conversations import (
        ConversationMemory,
        InMemoryConversationStore,
    )
    from app.domain.errors import AppError
    import pytest

    store = InMemoryConversationStore()
    policy = PolicyContext.from_user_id("alice")
    state = QuickMemory(
        user_id="alice",
        plan=QuickPlan(intent="list"),
        result_ids=[QuickResultRef(source="ews_mail", id="raw-id")],
    )
    memory = ConversationMemory(quick_memory=state)
    await store.save("quick", policy, memory)
    loaded = await store.load("quick", policy)
    assert loaded.quick_memory == state
    with pytest.raises(AppError):
        await store.save("wrong", PolicyContext.from_user_id("bob"), memory)


def test_quick_memory_roundtrip_and_owner_enforcement():
    asyncio.run(_roundtrip_quick_state())


def test_seed_retains_sender_display_name():
    from scripts.seed_multi_source_opensearch import (
        build_index_definition,
        build_index_document,
    )

    document = build_index_document(
        {"source_type": "mail", "metadata": {"sender_name": "김테스트"}}, [1.0]
    )
    assert document["sender_name"] == "김테스트"
    assert (
        build_index_definition(1)["mappings"]["properties"]["sender_name"]["fields"][
            "keyword"
        ]["type"]
        == "keyword"
    )


def test_configured_app_uses_quick_container_even_with_legacy_demo_flag(monkeypatch):
    import app.api.main as main
    import app.api.dependencies as dep

    container = SimpleNamespace(traces=None)
    monkeypatch.setattr(
        main.Settings,
        "from_env",
        lambda: Settings(_env_file=None, multi_source_demo=True),
    )
    monkeypatch.setattr(dep, "build_container", lambda settings: container)
    app = main.build_configured_app()
    assert app.state.container is container


def test_list_ten_references_survive_public_api_and_memory():
    import httpx
    from app.domain.chat import FastRAGResult, QualityStatus
    from app.domain.evidence import Evidence
    from app.domain.quick_rag import QuickMemory, QuickPlan, QuickResultRef
    from app.api.dependencies import ServiceContainer
    from app.persistence.conversations import InMemoryConversationStore
    from app.domain.policy import PolicyContext

    evidence = [
        Evidence(
            evidence_id=f"S{i}",
            source_type="mail",
            document_id=f"d{i}",
            title="메일",
            excerpt="본문",
            score=1,
            user_id="alice",
            acl_decision_id="acl",
            content_hash="hash",
        )
        for i in range(1, 11)
    ]
    state = QuickMemory(
        user_id="alice",
        plan=QuickPlan(intent="list"),
        result_ids=[QuickResultRef(source="ews_mail", id=str(i)) for i in range(10)],
    )

    class Workflow:
        async def invoke(self, request, policy, memory):
            return FastRAGResult(
                answer="\n".join(f"{i}. 메일 [S{i}]" for i in range(1, 11)),
                evidence=evidence,
                quick_memory=state,
                quality=QualityStatus(citation_valid=True),
            )

    async def exercise():
        store = InMemoryConversationStore()
        app = create_app(
            ServiceContainer(agentic=Workflow(), conversations=store, jobs=None)
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/v1/chat", json={"user_id": "alice", "message": "최근 메일"}
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["references"]) == 10
        memory = await store.load(
            body["conversation_id"], PolicyContext.from_user_id("alice")
        )
        assert memory.quick_memory == state
        assert len(memory.cited_evidence) == 10

    asyncio.run(exercise())


def test_bm25_runtime_does_not_require_embedding_credentials(monkeypatch):
    import app.api.dependencies as dep

    def no_embedding(settings):
        raise AssertionError("BM25 runtime must not construct embedding client")

    monkeypatch.setattr(dep, "build_embedding_gateway", no_embedding)
    settings = Settings(
        _env_file=None, openrouter_api_key="test", quick_vector_sources=[]
    )
    container = dep.build_container(settings)
    assert container.agentic.retrieval.embeddings is None


def test_conversation_read_outage_is_bounded_and_disclosed():
    from app.api.routes.chat import _load_memory, CONTEXT_UNAVAILABLE_DISCLOSURE
    from app.domain.policy import PolicyContext
    import time

    class SlowStore:
        async def load(self, *args):
            await asyncio.sleep(10)

    async def exercise():
        start = time.monotonic()
        memory, disclosures = await _load_memory(
            SimpleNamespace(conversations=SlowStore()),
            "slow",
            PolicyContext.from_user_id("alice"),
            False,
        )
        assert memory is None
        assert disclosures == [CONTEXT_UNAVAILABLE_DISCLOSURE]
        assert time.monotonic() - start < 2

    asyncio.run(exercise())
