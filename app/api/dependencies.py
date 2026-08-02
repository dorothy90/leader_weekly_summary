from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServiceContainer:
    router: Any
    fast: Any
    deep: Any
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


def build_ai_gateways(settings):
    from openai import AsyncOpenAI

    from app.llm.gateway import OpenAILLMGateway
    from app.retrieval.embedding import OpenAIEmbeddingGateway

    provider = settings.resolve_ai_provider()
    ai = AsyncOpenAI(
        api_key=provider.api_key.get_secret_value(),
        base_url=provider.base_url or None,
    )
    return (
        OpenAILLMGateway(ai, provider.llm_model),
        OpenAIEmbeddingGateway(ai, provider.embedding_model),
    )


def build_container(settings=None, trace_sink=None) -> ServiceContainer:
    """Build synchronous Fast and distinct persistent Deep services."""
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.config.settings import get_settings
    from app.graphs.fast_rag import FastRAGWorkflow
    from app.content.mail import MailContentStore
    from app.graphs.deep_research import DeepCoordinator
    from app.graphs.router import route_request
    from app.persistence.conversations import MongoConversationStore
    from app.persistence.research_jobs import MongoResearchJobStore
    from app.observability.tracing import NoOpTraceSink
    from app.retrieval.opensearch import AsyncOpenSearchGateway
    from app.retrieval.service import RetrievalService

    current = settings or get_settings()
    traces = trace_sink if trace_sink is not None else NoOpTraceSink()
    llm, embeddings = build_ai_gateways(current)
    opensearch_client = build_opensearch_client(current)
    search = AsyncOpenSearchGateway(opensearch_client)
    retrieval = RetrievalService(
        search,
        embeddings,
        current.mail_child_index,
        current.mail_parent_index,
        current.wiki_index,
        trace_sink=traces,
    )
    mongo_client = AsyncIOMotorClient(current.mongo_uri)
    database = mongo_client[current.mongo_db]
    jobs = MongoResearchJobStore(database.research_jobs)

    class RouterService:
        async def route(self, request):
            return await route_request(request, llm)

    return ServiceContainer(
        router=RouterService(),
        fast=FastRAGWorkflow(
            retrieval,
            llm,
            trace_sink=traces,
            deadline_seconds=current.fast_deadline_seconds,
        ),
        deep=DeepCoordinator(jobs),
        conversations=MongoConversationStore(database.conversations),
        jobs=jobs,
        mail_content=MailContentStore(current.mail_content_root),
        traces=traces,
        readiness=DependencyReadiness(
            opensearch_client,
            mongo_client,
            [current.mail_child_index, current.mail_parent_index, current.wiki_index],
        ),
    )
