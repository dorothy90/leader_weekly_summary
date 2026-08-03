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
    corpus_info: Any = None


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

    llm_endpoint = settings.resolve_llm_endpoint()
    embedding_endpoint = settings.resolve_embedding_endpoint()
    llm_client = AsyncOpenAI(
        api_key=llm_endpoint.api_key.get_secret_value(),
        base_url=llm_endpoint.base_url,
        timeout=llm_endpoint.timeout_seconds,
    )
    embedding_client = AsyncOpenAI(
        api_key=embedding_endpoint.api_key.get_secret_value(),
        base_url=embedding_endpoint.base_url,
        timeout=embedding_endpoint.timeout_seconds,
    )
    return (
        OpenAILLMGateway(llm_client, llm_endpoint.model),
        OpenAIEmbeddingGateway(embedding_client, embedding_endpoint.model),
    )


def build_container(settings=None, trace_sink=None) -> ServiceContainer:
    """Build synchronous Fast and distinct persistent Deep services."""
    from motor.motor_asyncio import AsyncIOMotorClient

    from app.config.settings import get_settings
    from app.content.mail import MailContentStore
    from app.graphs.conversation import contextualize_request
    from app.graphs.deep_research import DeepResearchWorkflow
    from app.graphs.fast_rag import FastRAGWorkflow
    from app.graphs.router import route_request
    from app.observability.tracing import NoOpTraceSink
    from app.observability.node_runs import record_node
    from app.persistence.conversations import MongoConversationStore
    from app.persistence.research_jobs import MongoResearchJobStore
    from app.retrieval.opensearch import AsyncOpenSearchGateway
    from app.retrieval.corpus_info import CorpusInfoService
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
        async def route(self, request, conversation=None):
            return await route_request(
                request,
                llm,
                conversation,
                timeout_seconds=current.openrouter_request_timeout_seconds,
            )

        async def contextualize_request(self, request, conversation=None):
            history = len(getattr(conversation, "messages", None) or [])
            async with record_node(
                "router.contextualize",
                input_metrics={"history_messages": min(20, history)},
            ):
                return await contextualize_request(llm, request, conversation)

    return ServiceContainer(
        router=RouterService(),
        fast=FastRAGWorkflow(
            retrieval,
            llm,
            trace_sink=traces,
            model_step_timeout_seconds=current.openrouter_request_timeout_seconds,
            general_timeout_seconds=current.openrouter_request_timeout_seconds,
        ),
        deep=DeepResearchWorkflow(
            retrieval,
            llm,
            trace_sink=traces,
        ),
        conversations=MongoConversationStore(database.conversations),
        jobs=jobs,
        mail_content=MailContentStore(current.mail_content_root),
        traces=traces,
        readiness=DependencyReadiness(
            opensearch_client,
            mongo_client,
            [current.mail_child_index, current.mail_parent_index, current.wiki_index],
        ),
        corpus_info=CorpusInfoService(search, current.mail_child_index),
    )
