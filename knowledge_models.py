from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Lotcd(StrictModel):
    code: str
    fab_id: str
    product_code: str
    product: str
    aliases: list[str] = Field(default_factory=list)


class Tech(StrictModel):
    id: str
    name: str
    aliases: list[str] = Field(default_factory=list)
    lotcds: list[Lotcd]


class Domain(StrictModel):
    id: str
    name: Literal["DRAM", "NAND"]
    techs: list[Tech]


class GroupAlias(StrictModel):
    alias: str
    target_lotcds: list[str] = Field(min_length=1)


class LearnedAliasRule(StrictModel):
    value: str
    lotcd: str
    origin_agenda_id: str | None = None
    context_domain: Literal["DRAM", "NAND"] | None = None
    context_tech: str | None = None


class TaxonomyDocument(StrictModel):
    version: int
    is_dummy: bool
    notice: str
    domains: list[Domain]
    group_aliases: list[GroupAlias] = Field(default_factory=list)
    learned_aliases: list[LearnedAliasRule] = Field(default_factory=list)
    policies: dict[str, str | bool] = Field(default_factory=dict)


class Mail(StrictModel):
    id: str
    subject: str
    sender_team: str
    sender: str
    received_at: datetime
    body: str
    reply_to: str | None = None


class MailDocument(StrictModel):
    version: int
    is_dummy: bool
    mails: list[Mail]


class CategoryPath(StrictModel):
    domain: Literal["DRAM", "NAND"]
    tech: str | None
    lotcd: str | None


ItemKind = Literal["lotcd_specific", "aggregate", "unknown"]
DecisionStatus = Literal[
    "confirmed", "aggregate", "unclassified", "conflict",
    "review_required", "manually_corrected", "excluded",
]
DiagnosticCode = Literal[
    "NO_LOTCD_MATCH", "MULTIPLE_LOTCD_CONFLICT", "UNKNOWN_LOTCD_CODE",
    "AGGREGATE_METRIC", "CONTEXT_MISSING", "ALIAS_COLLISION",
]


class CandidateMatch(StrictModel):
    phrase: str
    lotcd: str
    match_type: Literal["canonical", "alias"]
    rule_id: str
    score: float = Field(ge=0, le=1)


class ClassificationDecision(StrictModel):
    status: DecisionStatus
    target_path: CategoryPath | None = None
    matches: list[CandidateMatch] = Field(default_factory=list)
    diagnostics: list[DiagnosticCode] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)


class ClassificationRun(StrictModel):
    id: str
    week: str
    status: Literal["processing", "completed", "failed"]
    prompt_version: str
    classifier_version: str
    taxonomy_version: int
    alias_version: int
    prior_run_id: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class WeekClassificationSummary(StrictModel):
    week: str
    workflow_state: Literal[
        "not_started", "processing", "review_in_progress", "ready_for_approval",
        "approved", "revalidation_required", "failed",
    ]
    active_run_id: str | None = None
    counts: dict[DecisionStatus, int] = Field(default_factory=dict)


class RunItemChange(StrictModel):
    agenda_id: str
    before_status: DecisionStatus | None
    after_status: DecisionStatus | None
    before_lotcd: str | None
    after_lotcd: str | None


class RunComparison(StrictModel):
    old_run_id: str
    new_run_id: str
    changed: list[RunItemChange]
    unchanged_count: int


class ClassificationItem(StrictModel):
    agenda_id: str
    mail_id: str
    summary: str
    source_quote: str
    classification_context: str
    item_kind: ItemKind
    decision: ClassificationDecision
    revision_count: int


class ClassificationItemListResponse(StrictModel):
    items: list[ClassificationItem]
    total: int


class ClassificationRunRequest(StrictModel):
    rerun: bool = False


class AliasRecord(StrictModel):
    id: int
    value: str
    target_paths: list[CategoryPath]
    origin_agenda_id: str | None = None
    context_domain: Literal["DRAM", "NAND"] | None = None
    context_tech: str | None = None


class WorkbenchCorrection(StrictModel):
    lotcd: str
    reason: str = Field(min_length=1, max_length=500)


class ItemDispositionUpdate(StrictModel):
    status: Literal["aggregate", "excluded"]
    reason: str = Field(min_length=1, max_length=500)


class LearnedAliasCreate(StrictModel):
    value: str = Field(min_length=1, max_length=200)
    lotcd: str
    origin_agenda_id: str
    context_domain: Literal["DRAM", "NAND"] | None = None
    context_tech: str | None = None


class ItemSplitPart(StrictModel):
    source_quote: str = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=240)
    lotcd: str


class ItemSplitRequest(StrictModel):
    parts: list[ItemSplitPart] = Field(min_length=2)
    reason: str = Field(min_length=1, max_length=500)


class KnowledgeSession(StrictModel):
    user_id: str
    roles: list[str]
    can_edit: bool
