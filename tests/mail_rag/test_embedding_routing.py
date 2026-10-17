from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.retrieval.embedding import OpenAIEmbeddingGateway


@pytest.mark.asyncio
async def test_embedding_routes_only_to_configured_provider_without_changing_model():
    create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])])
    )
    client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    gateway = OpenAIEmbeddingGateway(
        client, "qwen/qwen3-embedding-8b", providers=["nebius"]
    )
    assert await gateway.embed("test") == [0.1]
    assert create.call_args.kwargs["model"] == "qwen/qwen3-embedding-8b"
    assert create.call_args.kwargs["extra_body"] == {
        "provider": {"only": ["nebius"], "allow_fallbacks": False}
    }


@pytest.mark.asyncio
async def test_unconfigured_embedding_keeps_default_routing():
    create = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(embedding=[0.1])])
    )
    gateway = OpenAIEmbeddingGateway(
        SimpleNamespace(embeddings=SimpleNamespace(create=create)), "model"
    )
    await gateway.embed("test")
    assert "extra_body" not in create.call_args.kwargs
