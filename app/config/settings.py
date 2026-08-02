from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class AIProviderConfig:
    provider: Literal["cloudflare", "openrouter"]
    api_key: SecretStr
    base_url: str
    llm_model: str
    embedding_model: str


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
    embedding_model: str = "qwen/qwen3-embedding-8b"
    llm_model: str = "gpt-oss-120b"
    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = ""
    cloudflare_account_id: str = ""
    cloudflare_api_token: SecretStr = SecretStr("")
    cloudflare_llm_model: str = "@cf/openai/gpt-oss-120b"
    cloudflare_embedding_model: str = "@cf/qwen/qwen3-embedding-0.6b"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "weekly_mail_agent"
    fast_deadline_seconds: int = Field(default=20, ge=1, le=120)
    mail_content_root: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()

    def resolve_ai_provider(self) -> AIProviderConfig:
        account_id = self.cloudflare_account_id.strip()
        token = self.cloudflare_api_token.get_secret_value().strip()
        if account_id and token:
            return AIProviderConfig(
                provider="cloudflare",
                api_key=SecretStr(token),
                base_url=(
                    "https://api.cloudflare.com/client/v4/accounts/"
                    f"{account_id}/ai/v1"
                ),
                llm_model=self.cloudflare_llm_model,
                embedding_model=self.cloudflare_embedding_model,
            )
        return AIProviderConfig(
            provider="openrouter",
            api_key=self.openrouter_api_key,
            base_url=self.openrouter_base_url,
            llm_model=self.llm_model,
            embedding_model=self.embedding_model,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
