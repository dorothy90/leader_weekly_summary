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
                    self.opensearch_client.indices.exists_alias, name=alias
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
    )


def build_llm_gateway(settings):
    import httpx
    from openai import AsyncOpenAI

    from app.llm.gateway import OpenAILLMGateway
    from app.llm.manus import ManusLLMGateway

    endpoint = settings.resolve_llm_endpoint()
    api_key = endpoint.api_key.get_secret_value().strip()
    if not api_key:
        required = (
            "MANUS_API_KEY"
            if endpoint.provider == "manus"
            else "OPENAI_COMPATIBLE_LLM_API_KEY"
        )
        raise RuntimeError(f"{required} is required")
    if endpoint.provider == "manus":
        client = httpx.AsyncClient(
            base_url=endpoint.base_url.rstrip("/"),
            headers={"x-manus-api-key": api_key},
            timeout=endpoint.request_timeout_seconds,
        )
        return ManusLLMGateway(
            client,
            profile=endpoint.model,
            poll_interval_seconds=endpoint.poll_interval_seconds,
            task_timeout_seconds=endpoint.completion_timeout_seconds,
        )
    if not endpoint.base_url.strip() or not endpoint.model.strip():
        raise RuntimeError("OpenAI-compatible LLM endpoint is incomplete")
    return OpenAILLMGateway(
        AsyncOpenAI(
            api_key=api_key,
            base_url=endpoint.base_url,
            timeout=endpoint.request_timeout_seconds,
        ),
        endpoint.model,
    )


def build_embedding_gateway(settings):
    from openai import AsyncOpenAI

    from app.retrieval.embedding import OpenAIEmbeddingGateway

    endpoint = settings.resolve_embedding_endpoint()
    api_key = endpoint.api_key.get_secret_value().strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required")
    embedding_client = AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint.base_url,
        timeout=endpoint.timeout_seconds,
    )
    return OpenAIEmbeddingGateway(
        embedding_client,
        endpoint.model,
    )


def build_ai_gateways(settings):
    return build_llm_gateway(settings), build_embedding_gateway(settings)


def build_container(settings=None, trace_sink=None) -> ServiceContainer:
    """Build the direct multi-source chat service and shared persistence."""
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.config.settings import get_settings
    from app.content.mail import MailContentStore
    from app.graphs.multi_source import MultiSourceAgenticWorkflow
    from app.llm.agentic import StructuredAgentModel
    from app.observability.tracing import NoOpTraceSink
    from app.persistence.conversations import MongoConversationStore
    from app.persistence.research_jobs import MongoResearchJobStore
    from app.retrieval.opensearch import AsyncOpenSearchGateway
    from app.retrieval.multi_source_opensearch import OpenSearchMultiSourceSearch
    from app.retrieval.source_registry import SourceRegistry

    current = settings or get_settings()
    traces = trace_sink if trace_sink is not None else NoOpTraceSink()
    llm, embeddings = build_ai_gateways(current)
    llm_endpoint = current.resolve_llm_endpoint()
    opensearch_client = build_opensearch_client(current)
    search = AsyncOpenSearchGateway(opensearch_client)
    registry = SourceRegistry.from_settings(current)
    agentic = MultiSourceAgenticWorkflow(
        OpenSearchMultiSourceSearch(search, embeddings, registry),
        StructuredAgentModel(
            llm,
            timeout_seconds=llm_endpoint.completion_timeout_seconds,
            attempts=1 if llm_endpoint.provider == "manus" else 2,
        ),
        timezone_name=current.default_user_timezone,
    )
    mongo_client = AsyncIOMotorClient(current.mongo_uri)
    database = mongo_client[current.mongo_db]
    jobs = MongoResearchJobStore(database.research_jobs)

    return ServiceContainer(
        agentic=agentic,
        conversations=MongoConversationStore(database.conversations),
        jobs=jobs,
        mail_content=MailContentStore(current.mail_content_root),
        traces=traces,
        readiness=DependencyReadiness(
            opensearch_client,
            mongo_client,
            [
                current.mail_index_alias,
                current.calendar_index_alias,
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
        endpoint = current.resolve_llm_endpoint()
        api_key = endpoint.api_key.get_secret_value().strip()
        if api_key:
            llm = build_llm_gateway(current)
            model = StructuredAgentModel(
                llm,
                timeout_seconds=endpoint.completion_timeout_seconds,
                attempts=1 if endpoint.provider == "manus" else 2,
                now=datetime(2026, 8, 17, 0, tzinfo=UTC),
            )
        else:
            model = UnavailableAnalyzer()
    else:
        model = agent_model
    model_status = (
        "unavailable" if isinstance(model, UnavailableAnalyzer) else "ready"
    )
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
