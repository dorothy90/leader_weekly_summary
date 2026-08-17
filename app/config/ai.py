from dataclasses import dataclass
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


LLMProvider = Literal["manus", "openai_compatible"]
ManusProfile = Literal[
    "manus-1.6",
    "manus-1.6-lite",
    "manus-1.6-max",
]


@dataclass(frozen=True)
class LLMEndpointConfig:
    provider: LLMProvider
    api_key: SecretStr
    base_url: str
    model: str
    request_timeout_seconds: float
    completion_timeout_seconds: float
    poll_interval_seconds: float


@dataclass(frozen=True)
class EmbeddingEndpointConfig:
    provider: Literal["openrouter"]
    api_key: SecretStr
    base_url: str
    model: str
    timeout_seconds: float


class AISettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: LLMProvider = "manus"
    manus_api_key: SecretStr = SecretStr("")
    manus_base_url: str = "https://api.manus.ai"
    manus_agent_profile: ManusProfile = "manus-1.6-lite"
    manus_request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    manus_task_timeout_seconds: int = Field(default=150, ge=1, le=600)
    manus_poll_interval_seconds: float = Field(default=2, ge=0.1, le=30)

    openai_compatible_llm_api_key: SecretStr = SecretStr("")
    openai_compatible_llm_base_url: str = ""
    openai_compatible_llm_model: str = ""
    openai_compatible_llm_timeout_seconds: int = Field(
        default=150,
        ge=1,
        le=600,
    )

    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_embedding_model: str = "qwen/qwen3-embedding-8b"
    openrouter_request_timeout_seconds: int = Field(
        default=150,
        ge=1,
        le=600,
    )

    def resolve_llm_endpoint(self) -> LLMEndpointConfig:
        if self.llm_provider == "manus":
            return LLMEndpointConfig(
                provider="manus",
                api_key=self.manus_api_key,
                base_url=self.manus_base_url,
                model=self.manus_agent_profile,
                request_timeout_seconds=float(
                    self.manus_request_timeout_seconds
                ),
                completion_timeout_seconds=float(
                    self.manus_task_timeout_seconds
                ),
                poll_interval_seconds=float(
                    self.manus_poll_interval_seconds
                ),
            )
        return LLMEndpointConfig(
            provider="openai_compatible",
            api_key=self.openai_compatible_llm_api_key,
            base_url=self.openai_compatible_llm_base_url,
            model=self.openai_compatible_llm_model,
            request_timeout_seconds=float(
                self.openai_compatible_llm_timeout_seconds
            ),
            completion_timeout_seconds=float(
                self.openai_compatible_llm_timeout_seconds
            ),
            poll_interval_seconds=0.0,
        )

    def resolve_embedding_endpoint(self) -> EmbeddingEndpointConfig:
        return EmbeddingEndpointConfig(
            provider="openrouter",
            api_key=self.openrouter_api_key,
            base_url=self.openrouter_base_url,
            model=self.openrouter_embedding_model,
            timeout_seconds=float(self.openrouter_request_timeout_seconds),
        )
