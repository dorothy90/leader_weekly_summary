from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class AIEndpointConfig:
    provider: Literal["openrouter"]
    api_key: SecretStr
    base_url: str
    model: str
    timeout_seconds: float


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    opensearch_host: str = "localhost"
    opensearch_port: int = 9200
    opensearch_user: str = ""
    opensearch_password: SecretStr = SecretStr("")
    opensearch_use_ssl: bool = False
    opensearch_verify_certs: bool = True
    mail_child_index: str = "weekly_mail"
    mail_parent_index: str = "weekly_mail_parent_read"
    wiki_index: str = "wiki_summaries_v2"
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_llm_model: str = "google/gemma-4-26b-a4b-it:free"
    openrouter_embedding_model: str = "qwen/qwen3-embedding-8b"
    openrouter_request_timeout_seconds: int = Field(default=150, ge=1, le=600)
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "weekly_mail_agent"
    mail_content_root: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()

    def resolve_llm_endpoint(self) -> AIEndpointConfig:
        return AIEndpointConfig(
            provider="openrouter",
            api_key=self.openrouter_api_key,
            base_url=self.openrouter_base_url,
            model=self.openrouter_llm_model,
            timeout_seconds=float(self.openrouter_request_timeout_seconds),
        )

    def resolve_embedding_endpoint(self) -> AIEndpointConfig:
        return AIEndpointConfig(
            provider="openrouter",
            api_key=self.openrouter_api_key,
            base_url=self.openrouter_base_url,
            model=self.openrouter_embedding_model,
            timeout_seconds=float(self.openrouter_request_timeout_seconds),
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
