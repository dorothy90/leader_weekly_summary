from pydantic import SecretStr

from app.config.settings import Settings


def test_manus_llm_and_openrouter_embedding_defaults_are_independent():
    settings = Settings(
        manus_api_key=SecretStr("manus-secret"),
        openrouter_api_key=SecretStr("openrouter-secret"),
    )

    llm = settings.resolve_llm_endpoint()
    embedding = settings.resolve_embedding_endpoint()

    assert llm.provider == "manus"
    assert llm.base_url == "https://api.manus.ai"
    assert llm.model == "manus-1.6-lite"
    assert llm.api_key.get_secret_value() == "manus-secret"
    assert llm.request_timeout_seconds == 30
    assert llm.completion_timeout_seconds == 150
    assert llm.poll_interval_seconds == 2
    assert embedding.provider == "openrouter"
    assert embedding.base_url == "https://openrouter.ai/api/v1"
    assert embedding.model == "qwen/qwen3-embedding-8b"
    assert embedding.api_key.get_secret_value() == "openrouter-secret"
    assert "manus-secret" not in repr(llm)
    assert "openrouter-secret" not in repr(embedding)


def test_openai_compatible_llm_can_be_selected_without_changing_embedding():
    settings = Settings(
        llm_provider="openai_compatible",
        openai_compatible_llm_api_key=SecretStr("llm-secret"),
        openai_compatible_llm_base_url="https://llm.example/v1",
        openai_compatible_llm_model="future-model",
        openrouter_api_key=SecretStr("embed-secret"),
    )

    llm = settings.resolve_llm_endpoint()
    embedding = settings.resolve_embedding_endpoint()

    assert (llm.provider, llm.base_url, llm.model) == (
        "openai_compatible",
        "https://llm.example/v1",
        "future-model",
    )
    assert llm.request_timeout_seconds == 150
    assert llm.completion_timeout_seconds == 150
    assert llm.poll_interval_seconds == 0
    assert embedding.provider == "openrouter"
    assert embedding.model == "qwen/qwen3-embedding-8b"


def test_endpoint_overrides_remain_independent():
    settings = Settings(
        manus_api_key=SecretStr("m-key"),
        manus_base_url="https://manus.example",
        manus_agent_profile="manus-1.6-max",
        manus_request_timeout_seconds=45,
        manus_task_timeout_seconds=75,
        manus_poll_interval_seconds=1.5,
        openrouter_api_key=SecretStr("o-key"),
        openrouter_base_url="https://openrouter.example/v1",
        openrouter_embedding_model="custom-embed",
        openrouter_request_timeout_seconds=90,
    )

    llm = settings.resolve_llm_endpoint()
    embedding = settings.resolve_embedding_endpoint()

    assert (llm.base_url, llm.model) == (
        "https://manus.example",
        "manus-1.6-max",
    )
    assert llm.request_timeout_seconds == 45
    assert llm.completion_timeout_seconds == 75
    assert llm.poll_interval_seconds == 1.5
    assert (embedding.base_url, embedding.model) == (
        "https://openrouter.example/v1",
        "custom-embed",
    )
    assert embedding.timeout_seconds == 90


def test_multi_source_alias_and_demo_defaults():
    settings = Settings()
    assert settings.multi_source_demo is False
    assert settings.domain_knowledge_index == "syld_gpt"
    assert settings.mail_index_alias == "ews-mail-active"
    assert settings.calendar_index_alias == "ews-calendar-active"
    assert settings.default_user_timezone == "Asia/Seoul"
