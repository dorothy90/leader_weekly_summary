from pydantic import SecretStr

from app.api.dependencies import build_ai_gateways
from app.config.settings import Settings


def test_ai_gateways_use_separate_openrouter_clients_with_150_second_timeout(
    monkeypatch,
):
    clients = []

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            clients.append(self)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(
        openrouter_api_key=SecretStr("openrouter-secret"),
    )

    llm, embeddings = build_ai_gateways(settings)

    assert [client.kwargs for client in clients] == [
        {
            "api_key": "openrouter-secret",
            "base_url": "https://openrouter.ai/api/v1",
            "timeout": 150.0,
        },
        {
            "api_key": "openrouter-secret",
            "base_url": "https://openrouter.ai/api/v1",
            "timeout": 150.0,
        },
    ]
    assert llm.client is clients[0]
    assert embeddings.client is clients[1]
    assert llm.client is not embeddings.client
    assert llm.model == "google/gemma-4-26b-a4b-it:free"
    assert embeddings.model == "qwen/qwen3-embedding-8b"
