from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServiceContainer:
    router: Any
    fast: Any
    deep: Any
    conversations: Any
    jobs: Any


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


def build_container(settings=None) -> ServiceContainer:
    """Build Task-5 services; Task 6 supplies the separate Deep coordinator."""
    from motor.motor_asyncio import AsyncIOMotorClient
    from openai import AsyncOpenAI

    from app.config.settings import get_settings
    from app.graphs.fast_rag import FastRAGWorkflow
    from app.graphs.router import route_request
    from app.llm.gateway import OpenAILLMGateway
    from app.persistence.conversations import MongoConversationStore
    from app.retrieval.embedding import OpenAIEmbeddingGateway
    from app.retrieval.opensearch import AsyncOpenSearchGateway
    from app.retrieval.service import RetrievalService

    current = settings or get_settings()
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
    )
    database = AsyncIOMotorClient(current.mongo_uri)[current.mongo_db]

    class RouterService:
        async def route(self, request):
            return await route_request(request, llm)

    return ServiceContainer(
        router=RouterService(),
        fast=FastRAGWorkflow(retrieval, llm),
        deep=None,
        conversations=MongoConversationStore(database.conversations),
        jobs=None,
    )
