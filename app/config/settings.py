from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "weekly_mail_agent"
    fast_deadline_seconds: int = Field(default=20, ge=1, le=120)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()


@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
