from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import SettingsConfigDict

from app.config.ai import AISettings


class Settings(AISettings):
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
    multi_source_demo: bool = False
    domain_knowledge_index: str = "syld_gpt"
    mail_index_alias: str = "ews-mail-active"
    calendar_index_alias: str = "ews-calendar-active"
    default_user_timezone: str = "Asia/Seoul"
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "weekly_mail_agent"
    mail_content_root: Path = Path("data")

    @classmethod
    def from_env(cls) -> "Settings":
        return cls()

@lru_cache
def get_settings() -> Settings:
    return Settings.from_env()
