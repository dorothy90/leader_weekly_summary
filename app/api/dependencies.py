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


def build_opensearch_client(settings=None):
    from opensearchpy import OpenSearch

    from app.config.settings import get_settings

    current = settings or get_settings()
    return OpenSearch(
        hosts=[{"host": current.opensearch_host, "port": current.opensearch_port}],
        http_auth=(
            current.opensearch_user,
            current.opensearch_password.get_secret_value(),
        ),
        use_ssl=current.opensearch_use_ssl,
        verify_certs=current.opensearch_verify_certs,
        ssl_show_warn=not current.opensearch_verify_certs,
    )


def build_container(settings=None, trace_sink=None) -> ServiceContainer:
    """Build synchronous Fast and distinct persistent Deep services."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from openai import AsyncOpenAI

    from app.config.settings import get_settings
    from app.graphs.fast_rag import FastRAGWorkflow
    from app.content.mail import MailContentStore
    from app.graphs.deep_research import DeepCoordinator
    from app.graphs.router import route_request
    from app.llm.gateway import OpenAILLMGateway
    from app.persistence.conversations import MongoConversationStore
    from app.persistence.research_jobs import MongoResearchJobStore
    from app.observability.tracing import NoOpTraceSink
    from app.retrieval.embedding import OpenAIEmbeddingGateway
    from app.retrieval.opensearch import AsyncOpenSearchGateway
    from app.retrieval.service import RetrievalService

    current = settings or get_settings()
    traces = trace_sink if trace_sink is not None else NoOpTraceSink()
    ai = AsyncOpenAI(
        api_key=current.openrouter_api_key.get_secret_value(),
        base_url=current.openrouter_base_url or None,
    )
    llm = OpenAILLMGateway(ai, current.llm_model)
    search = AsyncOpenSearchGateway(build_opensearch_client(current))
    embeddings = OpenAIEmbeddingGateway(ai, current.embedding_model)
    retrieval = RetrievalService(
        search,
        embeddings,
        current.mail_child_index,
        current.mail_parent_index,
        current.wiki_index,
        trace_sink=traces,
    )
    database = AsyncIOMotorClient(current.mongo_uri)[current.mongo_db]
    jobs = MongoResearchJobStore(database.research_jobs)

    class RouterService:
        async def route(self, request):
            return await route_request(request, llm)

    return ServiceContainer(
        router=RouterService(),
        fast=FastRAGWorkflow(retrieval, llm, trace_sink=traces),
        deep=DeepCoordinator(jobs),
        conversations=MongoConversationStore(database.conversations),
        jobs=jobs,
        mail_content=MailContentStore(current.mail_content_root),
        traces=traces,
    )
