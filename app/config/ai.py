from dataclasses import dataclass
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


LLMProvider = Literal["openrouter", "manus", "openai_compatible"]
LLMStage = Literal["routing", "planner", "judge", "answer"]
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

    llm_provider: LLMProvider = "openrouter"
    manus_api_key: SecretStr = SecretStr("")
    manus_base_url: str = "https://api.manus.ai"
    manus_agent_profile: ManusProfile = "manus-1.6-lite"
    manus_request_timeout_seconds: int = Field(default=30, ge=1, le=120)
    manus_task_timeout_seconds: int = Field(default=150, ge=1, le=600)
    manus_poll_interval_seconds: float = Field(default=2, ge=0.1, le=30)

    openai_compatible_llm_api_key: SecretStr = SecretStr("")
    openai_compatible_llm_base_url: str = ""
    openai_compatible_llm_model: str = ""
    openai_compatible_routing_model: str = ""
    openai_compatible_planner_model: str = ""
    openai_compatible_judge_model: str = ""
    openai_compatible_answer_model: str = ""
    openai_compatible_llm_timeout_seconds: int = Field(
        default=150,
        ge=1,
        le=600,
    )

    openrouter_api_key: SecretStr = SecretStr("")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_llm_model: str = "openrouter/free"
    openrouter_routing_model: str = ""
    openrouter_planner_model: str = ""
    openrouter_judge_model: str = ""
    openrouter_answer_model: str = ""
    openrouter_embedding_model: str = "qwen/qwen3-embedding-8b"
    openrouter_request_timeout_seconds: int = Field(
        default=150,
        ge=1,
        le=600,
    )

    @staticmethod
    def _stage_model(
        stage: LLMStage | None,
        shared_model: str,
        overrides: dict[LLMStage, str],
    ) -> str:
        if stage is None:
            return shared_model
        return overrides[stage].strip() or shared_model

    def resolve_llm_endpoint(
        self,
        stage: LLMStage | None = None,
    ) -> LLMEndpointConfig:
        if self.llm_provider == "openrouter":
            timeout = float(self.openrouter_request_timeout_seconds)
            return LLMEndpointConfig(
                provider="openrouter",
                api_key=self.openrouter_api_key,
                base_url=self.openrouter_base_url,
                model=self._stage_model(
                    stage,
                    self.openrouter_llm_model,
                    {
                        "routing": self.openrouter_routing_model,
                        "planner": self.openrouter_planner_model,
                        "judge": self.openrouter_judge_model,
                        "answer": self.openrouter_answer_model,
                    },
                ),
                request_timeout_seconds=timeout,
                completion_timeout_seconds=timeout,
                poll_interval_seconds=0.0,
            )
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
            model=self._stage_model(
                stage,
                self.openai_compatible_llm_model,
                {
                    "routing": self.openai_compatible_routing_model,
                    "planner": self.openai_compatible_planner_model,
                    "judge": self.openai_compatible_judge_model,
                    "answer": self.openai_compatible_answer_model,
                },
            ),
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
