from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetrievalFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    teams: list[str] = Field(default_factory=list, max_length=10)
    weeks: list[str] = Field(default_factory=list, max_length=52)
    mail_type: Literal["weekly_report", "daily_report", "other"] | None = None

    @field_validator("weeks")
    @classmethod
    def normalize_weeks(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            year, separator, week = value.strip().partition("-")
            if (
                separator != "-"
                or len(year) != 4
                or len(week) not in (1, 2)
                or not year.isdigit()
                or not week.isdigit()
                or not 1 <= int(week) <= 53
            ):
                raise ValueError("weeks must contain YYYY-WW values")
            item = f"{int(year):04d}-{int(week):02d}"
            if item not in normalized:
                normalized.append(item)
        return normalized


class SearchTask(BaseModel):
    query: str = Field(min_length=1, max_length=1000)
    source: Literal["mail", "wiki", "statistics"] = "mail"
    filters: RetrievalFilters = Field(default_factory=RetrievalFilters)
    top_k: int = Field(default=8, ge=1, le=20)


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    evidence_id: str = Field(min_length=1, max_length=32)
    source_type: Literal[
        "mail", "wiki", "statistic", "domain_knowledge", "calendar"
    ]
    document_id: str = Field(min_length=1, max_length=256)
    parent_id: str | None = Field(default=None, max_length=256)
    title: str = Field(default="", max_length=500)
    excerpt: str = Field(min_length=1, max_length=8000)
    team: str | None = Field(default=None, max_length=100)
    week: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}$")
    source_locator: str | None = Field(default=None, max_length=1000)
    score: float
    rerank_score: float | None = None
    user_id: str = Field(min_length=1, max_length=128)
    acl_decision_id: str = Field(min_length=1, max_length=64)
    content_hash: str = Field(min_length=1, max_length=128)
