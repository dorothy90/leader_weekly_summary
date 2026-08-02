from pydantic import SecretStr

from app.api.dependencies import build_ai_gateways
from app.config.settings import Settings


def test_ai_gateways_use_resolved_cloudflare_client_and_models(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(
        cloudflare_account_id="account-123",
        cloudflare_api_token=SecretStr("cf-secret-token"),
        openrouter_api_key=SecretStr("unused-openrouter-token"),
    )

    llm, embeddings = build_ai_gateways(settings)

    assert captured == {
        "api_key": "cf-secret-token",
        "base_url": (
            "https://api.cloudflare.com/client/v4/accounts/account-123/ai/v1"
        ),
    }
    assert llm.client is embeddings.client
    assert llm.model == "@cf/openai/gpt-oss-120b"
    assert embeddings.model == "@cf/qwen/qwen3-embedding-0.6b"
    assert "cf-secret-token" not in repr(settings.resolve_ai_provider())


def test_ai_gateways_preserve_openrouter_client_and_models(monkeypatch):
    captured = {}

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("openai.AsyncOpenAI", FakeAsyncOpenAI)
    settings = Settings(
        cloudflare_account_id="",
        cloudflare_api_token=SecretStr(""),
        openrouter_api_key=SecretStr("openrouter-secret-token"),
        openrouter_base_url="https://openrouter.example/v1",
        llm_model="openrouter-llm",
        embedding_model="openrouter-embedding",
    )

    llm, embeddings = build_ai_gateways(settings)

    assert captured == {
        "api_key": "openrouter-secret-token",
        "base_url": "https://openrouter.example/v1",
    }
    assert llm.client is embeddings.client
    assert llm.model == "openrouter-llm"
    assert embeddings.model == "openrouter-embedding"
    assert "openrouter-secret-token" not in repr(settings.resolve_ai_provider())
