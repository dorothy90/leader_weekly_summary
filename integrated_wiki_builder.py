from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from category_wiki_builder import page_index_definition


class WikiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SupportedClaim(WikiModel):
    text: str = Field(min_length=1)
    mail_ids: list[str]
    agenda_ids: list[str]

    @field_validator("mail_ids", "agenda_ids")
    @classmethod
    def require_evidence(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("supported claims require evidence")
        return value


class IssueDecision(WikiModel):
    issue_id: str
    status: Literal["ongoing", "resolved", "reopened"]
    summary: str
    mail_ids: list[str]
    agenda_ids: list[str]


class PageAnalysis(WikiModel):
    new_claims: list[SupportedClaim] = Field(default_factory=list)
    retained_claims: list[SupportedClaim] = Field(default_factory=list)
    stale_claims: list[str] = Field(default_factory=list)
    issue_decisions: list[IssueDecision] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    review_items: list[str] = Field(default_factory=list)
    outline: list[str]


class NarrativeDraft(WikiModel):
    overview: str
    current_status: str
    cause_and_impact: str
    actions_and_effects: str
    pending_and_decisions: str
    accumulated_knowledge: str
    weekly_update: str
    confidence: Literal["low", "medium", "high"]


class WeeklyHistoryEntry(WikiModel):
    week: str
    body_markdown: str
    source_mail_ids: list[str] = Field(default_factory=list)


class CitationMapEntry(WikiModel):
    mail_id: str
    agenda_ids: list[str]
    used_in_sections: list[str]
    category_paths: list[str]


class ChildDigest(WikiModel):
    canonical_id: str
    as_of_week: str
    summary: str
    claims: list[SupportedClaim]
    issues: list[IssueDecision]
    contradictions: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]


AnalysisFn = Callable[[dict[str, Any]], PageAnalysis]
DraftFn = Callable[[dict[str, Any], PageAnalysis], NarrativeDraft]


def integrated_page_index_definition() -> dict[str, Any]:
    definition = page_index_definition()
    definition["mappings"]["properties"].update(
        {
            "doc_type": {"type": "keyword"},
            "canonical_id": {"type": "keyword"},
            "aliases": {"type": "keyword"},
            "current_body_markdown": {
                "type": "text",
                "analyzer": "category_korean",
            },
            "weekly_history": {
                "type": "nested",
                "properties": {
                    "week": {"type": "keyword"},
                    "body_markdown": {"type": "text", "analyzer": "category_korean"},
                    "source_mail_ids": {"type": "keyword"},
                },
            },
            "citation_map": {
                "type": "nested",
                "properties": {
                    "mail_id": {"type": "keyword"},
                    "agenda_ids": {"type": "keyword"},
                    "used_in_sections": {"type": "keyword"},
                    "category_paths": {"type": "keyword"},
                },
            },
            "child_page_ids": {"type": "keyword"},
            "confidence": {"type": "keyword"},
            "open_issue_count": {"type": "integer"},
            "resolved_issue_count": {"type": "integer"},
            "contradictions": {"type": "text", "analyzer": "category_korean"},
            "generation_review_items": {
                "type": "text",
                "analyzer": "category_korean",
            },
            "updated_at": {"type": "date"},
        }
    )
    return definition
