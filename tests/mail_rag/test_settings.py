from pydantic import SecretStr

from app.config.settings import Settings


def test_resolves_openrouter_llm_endpoint_defaults():
    settings = Settings(openrouter_api_key=SecretStr("openrouter-secret"))

    endpoint = settings.resolve_llm_endpoint()

    assert endpoint.provider == "openrouter"
    assert endpoint.base_url == "https://openrouter.ai/api/v1"
    assert endpoint.model == "google/gemma-4-26b-a4b-it:free"
    assert endpoint.timeout_seconds == 150
    assert endpoint.api_key.get_secret_value() == "openrouter-secret"
    assert "openrouter-secret" not in repr(endpoint)


def test_resolves_openrouter_embedding_endpoint_defaults():
    settings = Settings(openrouter_api_key=SecretStr("openrouter-secret"))

    endpoint = settings.resolve_embedding_endpoint()

    assert endpoint.provider == "openrouter"
    assert endpoint.base_url == "https://openrouter.ai/api/v1"
    assert endpoint.model == "qwen/qwen3-embedding-8b"
    assert endpoint.timeout_seconds == 150
    assert endpoint.api_key.get_secret_value() == "openrouter-secret"
    assert "openrouter-secret" not in repr(endpoint)


def test_endpoint_overrides_remain_independent():
    settings = Settings(
        openrouter_api_key=SecretStr("o-key"),
        openrouter_base_url="https://openrouter.example/v1",
        openrouter_llm_model="custom-chat",
        openrouter_embedding_model="custom-embed",
        openrouter_request_timeout_seconds=75,
    )

    llm = settings.resolve_llm_endpoint()
    embedding = settings.resolve_embedding_endpoint()

    assert (llm.base_url, llm.model) == (
        "https://openrouter.example/v1",
        "custom-chat",
    )
    assert (embedding.base_url, embedding.model) == (
        "https://openrouter.example/v1",
        "custom-embed",
    )
    assert llm.timeout_seconds == embedding.timeout_seconds == 75


def test_multi_source_alias_and_demo_defaults():
    settings = Settings()
    assert settings.multi_source_demo is False
    assert settings.domain_knowledge_index == "syld_gpt"
    assert settings.mail_index_alias == "ews-mail-active"
    assert settings.calendar_index_alias == "ews-calendar-active"
    assert settings.default_user_timezone == "Asia/Seoul"
