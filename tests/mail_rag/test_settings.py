from pydantic import SecretStr

from app.config.settings import Settings


def test_complete_cloudflare_credentials_take_precedence():
    settings = Settings(
        cloudflare_account_id="account-123",
        cloudflare_api_token=SecretStr("cf-secret-token"),
        openrouter_api_key=SecretStr("openrouter-secret-token"),
        openrouter_base_url="https://openrouter.example/v1",
        llm_model="openrouter-llm",
        embedding_model="openrouter-embedding",
    )

    provider = settings.resolve_ai_provider()

    assert provider.provider == "cloudflare"
    assert provider.base_url == (
        "https://api.cloudflare.com/client/v4/accounts/account-123/ai/v1"
    )
    assert provider.api_key.get_secret_value() == "cf-secret-token"
    assert provider.llm_model == "@cf/openai/gpt-oss-120b"
    assert provider.embedding_model == "@cf/qwen/qwen3-embedding-0.6b"
    assert "cf-secret-token" not in repr(settings)
    assert "cf-secret-token" not in repr(provider)


def test_incomplete_cloudflare_pair_uses_existing_openrouter_configuration():
    settings = Settings(
        cloudflare_account_id="account-123",
        cloudflare_api_token=SecretStr(""),
        openrouter_api_key=SecretStr("openrouter-secret-token"),
        openrouter_base_url="https://openrouter.example/v1",
        llm_model="openrouter-llm",
        embedding_model="openrouter-embedding",
    )

    provider = settings.resolve_ai_provider()

    assert provider.provider == "openrouter"
    assert provider.base_url == "https://openrouter.example/v1"
    assert provider.api_key.get_secret_value() == "openrouter-secret-token"
    assert provider.llm_model == "openrouter-llm"
    assert provider.embedding_model == "openrouter-embedding"
    assert "openrouter-secret-token" not in repr(provider)
