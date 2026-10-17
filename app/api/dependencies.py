from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServiceContainer:
    agentic: Any
    conversations: Any
    jobs: Any
    mail_content: Any = None
    traces: Any = None
    readiness: Any = None
    detailed: Any = None


class DependencyReadiness:
    def __init__(self, opensearch_client, mongo_client, aliases):
        self.opensearch_client = opensearch_client
        self.mongo_client = mongo_client
        self.aliases = tuple(aliases)

    async def check(self):
        import asyncio

        status = {}
        try:
            await self.mongo_client.admin.command("ping")
            status["mongo"] = "ready"
        except Exception:
            status["mongo"] = "unavailable"
        try:
            await asyncio.to_thread(self.opensearch_client.cluster.health)
            status["opensearch"] = "ready"
        except Exception:
            status["opensearch"] = "unavailable"
        try:
            for alias in self.aliases:
                exists = await asyncio.to_thread(
                    self.opensearch_client.indices.exists, index=alias
                )
                if not exists:
                    raise RuntimeError("alias unavailable")
            status["aliases"] = "ready"
        except Exception:
            status["aliases"] = "unavailable"
        return status


class DemoReadiness:
    def __init__(self, agent_model="ready"):
        self.agent_model = agent_model

    async def check(self):
        return {
            "opensearch": "ready",
            "mongo": "ready",
            "aliases": "ready",
            "agent_model": self.agent_model,
        }


def build_opensearch_client(settings=None):
    from opensearchpy import OpenSearch

    from app.config.settings import get_settings

    current = settings or get_settings()
    password = current.opensearch_password.get_secret_value()
    if current.opensearch_user and not password:
        raise RuntimeError("OPENSEARCH_PASSWORD is required")
    return OpenSearch(
        hosts=[{"host": current.opensearch_host, "port": current.opensearch_port}],
        http_auth=(
            current.opensearch_user,
            password,
        ),
        use_ssl=current.opensearch_use_ssl,
        verify_certs=current.opensearch_verify_certs,
        ssl_show_warn=current.opensearch_verify_certs,
        timeout=current.quick_source_timeout_seconds,
    )


LLM_STAGES = ("routing", "planner", "judge", "answer")


def _validated_llm_api_key(endpoint):
    api_key = endpoint.api_key.get_secret_value().strip()
    if not api_key:
        required = {
            "openrouter": "OPENROUTER_API_KEY",
            "manus": "MANUS_API_KEY",
            "openai_compatible": "OPENAI_COMPATIBLE_LLM_API_KEY",
        }[endpoint.provider]
        raise RuntimeError(f"{required} is required")
    if endpoint.provider != "manus" and (
        not endpoint.base_url.strip() or not endpoint.model.strip()
    ):
        raise RuntimeError("OpenAI-compatible LLM endpoint is incomplete")
    return api_key


def _build_llm_client(endpoint, api_key):
    import httpx
    from openai import AsyncOpenAI

    if endpoint.provider == "manus":
        return httpx.AsyncClient(
            base_url=endpoint.base_url.rstrip("/"),
            headers={"x-manus-api-key": api_key},
            timeout=endpoint.request_timeout_seconds,
        )
    return AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint.base_url,
        timeout=endpoint.request_timeout_seconds,
    )


def _build_llm_gateway_for_endpoint(endpoint, client):
    from app.llm.gateway import OpenAILLMGateway
    from app.llm.manus import ManusLLMGateway

    if endpoint.provider == "manus":
        return ManusLLMGateway(
            client,
            profile=endpoint.model,
            poll_interval_seconds=endpoint.poll_interval_seconds,
            task_timeout_seconds=endpoint.completion_timeout_seconds,
        )
    return OpenAILLMGateway(
        client,
        endpoint.model,
        native_structured_output=endpoint.provider == "openrouter",
    )


def build_llm_gateway(settings, stage=None):
    endpoint = settings.resolve_llm_endpoint(stage)
    api_key = _validated_llm_api_key(endpoint)
    client = _build_llm_client(endpoint, api_key)
    return _build_llm_gateway_for_endpoint(endpoint, client)


def build_stage_llm_gateways(settings):
    endpoints = {stage: settings.resolve_llm_endpoint(stage) for stage in LLM_STAGES}
    first = endpoints["routing"]
    api_key = _validated_llm_api_key(first)
    for endpoint in endpoints.values():
        _validated_llm_api_key(endpoint)
    client = _build_llm_client(first, api_key)
    return {
        stage: _build_llm_gateway_for_endpoint(endpoint, client)
        for stage, endpoint in endpoints.items()
    }


def build_embedding_gateway(settings):
    from openai import AsyncOpenAI

    from app.retrieval.embedding import OpenAIEmbeddingGateway

    endpoint = settings.resolve_embedding_endpoint()
    api_key = endpoint.api_key.get_secret_value().strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required")
    import httpx

    embedding_client = AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint.base_url,
        timeout=httpx.Timeout(4.0, connect=2.0),
        max_retries=0,
        http_client=httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=20, max_keepalive_connections=10, keepalive_expiry=60
            )
        ),
    )
    return OpenAIEmbeddingGateway(
        embedding_client,
        endpoint.model,
        providers=settings.openrouter_embedding_providers,
        hedge_delay_seconds=settings.openrouter_embedding_hedge_delay_seconds,
    )


def build_ai_gateways(settings):
    return build_llm_gateway(settings), build_embedding_gateway(settings)


def build_quick_llm_gateways(settings):
    """Only interpretation and generation; no SDK retries in the fast path."""
    from openai import AsyncOpenAI
    from app.llm.gateway import OpenAILLMGateway

    gateways = []
    for stage, tokens in (("routing", 1200), ("answer", 700)):
        endpoint = settings.resolve_llm_endpoint(stage)
        key = _validated_llm_api_key(endpoint)
        if endpoint.provider == "manus":
            gateways.append(build_llm_gateway(settings, stage))
            continue
        model = endpoint.model
        if endpoint.provider == "openrouter" and model == "openrouter/free":
            model = settings.quick_openrouter_free_model
        disable_reasoning = (
            endpoint.provider == "openrouter"
            and model == settings.quick_openrouter_free_model
        )
        client = AsyncOpenAI(
            api_key=key,
            base_url=endpoint.base_url,
            timeout=settings.quick_deadline_seconds,
            max_retries=0,
        )
        gateways.append(
            OpenAILLMGateway(
                client,
                model,
                native_structured_output=endpoint.provider == "openrouter",
                output_token_limit=tokens,
                reasoning_enabled=False if disable_reasoning else None,
            )
        )
    return tuple(gateways)


def build_container(settings=None, trace_sink=None) -> ServiceContainer:
    """Build speed-first four-source chat with no research/statistics route."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from app.config.settings import get_settings
    from app.content.mail import MailContentStore
    from app.graphs.quick_rag import QuickRAGWorkflow
    from app.graphs.fast_agent import FastEvidenceAgent
    from app.graphs.detailed_rag import DetailedRAGWorkflow
    from app.observability.tracing import NoOpTraceSink
    from app.persistence.conversations import MongoConversationStore
    from app.persistence.research_jobs import MongoResearchJobStore
    from app.retrieval.opensearch import AsyncOpenSearchGateway
    from app.retrieval.quick_rag import QuickRetrieval

    current = settings or get_settings()
    interpreter, generator = build_quick_llm_gateways(current)
    agent_gateway = interpreter
    if current.quick_agent_model:
        from app.llm.gateway import OpenAILLMGateway

        agent_gateway = OpenAILLMGateway(
            interpreter.client,
            current.quick_agent_model,
            native_structured_output=True,
            output_token_limit=1500,
            reasoning_enabled=False,
        )
    embeddings = (
        build_embedding_gateway(current) if current.quick_vector_sources else None
    )
    # The outer workflow owns the deadline; SDK retry loops must not extend it.
    if embeddings is not None and hasattr(embeddings.client, "with_options"):
        embeddings.client = embeddings.client.with_options(max_retries=0)
    client = build_opensearch_client(current)
    client.transport.max_retries = 0
    retrieval = QuickRetrieval(
        AsyncOpenSearchGateway(client),
        embeddings,
        weekly_index=current.mail_child_index,
        mail_index=current.mail_index_alias,
        calendar_index=current.calendar_index_alias,
        wiki_index=current.wiki_index,
        shared_sources=current.quick_shared_sources,
        source_timeout_seconds=current.quick_source_timeout_seconds,
        compatible_sources=current.quick_vector_sources,
        require_vector=True,
    )
    database = AsyncIOMotorClient(
        current.mongo_uri,
        serverSelectionTimeoutMS=1000,
        connectTimeoutMS=1000,
        socketTimeoutMS=1000,
    )[current.mongo_db]
    return ServiceContainer(
        agentic=QuickRAGWorkflow(
            retrieval,
            interpreter,
            generator,
            timezone_name=current.default_user_timezone,
            deadline_seconds=current.quick_deadline_seconds,
            evidence_agent=FastEvidenceAgent(agent_gateway),
        ),
        detailed=DetailedRAGWorkflow(
            retrieval,
            interpreter,
            generator,
            generator,
            timezone_name=current.default_user_timezone,
            deadline_seconds=current.detailed_deadline_seconds,
            rerank_timeout_seconds=current.detailed_rerank_timeout_seconds,
        ),
        conversations=MongoConversationStore(database.conversations),
        jobs=MongoResearchJobStore(database.research_jobs),
        mail_content=MailContentStore(current.mail_content_root),
        traces=trace_sink if trace_sink is not None else NoOpTraceSink(),
        readiness=DependencyReadiness(
            client,
            database.client,
            [
                current.mail_child_index,
                current.mail_index_alias,
                current.calendar_index_alias,
                current.wiki_index,
            ],
        ),
    )


def build_demo_container(
    settings=None,
    *,
    agent_model=None,
) -> ServiceContainer:
    from datetime import UTC, datetime
    from pathlib import Path

    from app.config.settings import get_settings
    from app.graphs.multi_source import MultiSourceAgenticWorkflow
    from app.llm.agentic import StructuredAgentModel
    from app.llm.demo_scenarios import UnavailableAnalyzer
    from app.persistence.conversations import InMemoryConversationStore
    from app.persistence.research_jobs import InMemoryResearchJobStore
    from app.retrieval.multi_source import InMemoryMultiSourceSearch
    from app.retrieval.source_registry import SourceRegistry

    current = settings or get_settings()
    registry = SourceRegistry.from_settings(current)
    fixture = (
        Path(__file__).resolve().parents[2]
        / "fixtures"
        / "multi_source_demo"
        / "corpus.json"
    )
    search = InMemoryMultiSourceSearch.from_path(fixture, registry)
    if agent_model is None:
        endpoint = current.resolve_llm_endpoint("routing")
        api_key = endpoint.api_key.get_secret_value().strip()
        if api_key:
            stage_llms = build_stage_llm_gateways(current)
            model = StructuredAgentModel(
                stage_llms["routing"],
                planner_llm=stage_llms["planner"],
                judge_llm=stage_llms["judge"],
                answer_llm=stage_llms["answer"],
                timeout_seconds=endpoint.completion_timeout_seconds,
                attempts=1 if endpoint.provider == "manus" else 2,
                now=datetime(2026, 8, 17, 0, tzinfo=UTC),
            )
        else:
            model = UnavailableAnalyzer()
    else:
        model = agent_model
    model_status = "unavailable" if isinstance(model, UnavailableAnalyzer) else "ready"
    agentic = MultiSourceAgenticWorkflow(
        search,
        model,
        timezone_name=current.default_user_timezone,
    )

    return ServiceContainer(
        agentic=agentic,
        conversations=InMemoryConversationStore(),
        jobs=InMemoryResearchJobStore(),
        readiness=DemoReadiness(model_status),
    )
