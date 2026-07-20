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
    source_path: str | None = None


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
    team: str = "unknown"
    subject: str = ""
    received_at: datetime | None = None
    source_path: str | None = None
    topic_hint: str = ""
    state_hint: str = ""


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


DomainName = Literal["DRAM", "NAND"]
TopicKind = Literal[
    "issue", "observation", "change", "experiment", "action", "decision",
    "plan", "knowledge",
]
KnowledgeArea = Literal[
    "yield_defect", "process_equipment", "quality_analysis",
    "experiment_validation", "product_production", "schedule_delivery",
    "decision_action", "other",
]
TopicState = Literal[
    "new", "investigating", "action_in_progress", "monitoring", "resolved",
    "reopened", "closed", "review_required",
]
RelationKind = Literal[
    "possible_cause", "affects", "measurement_effect", "comparison",
    "follow_up", "supports", "contradicts", "shares_condition",
]


class SupportedClaim(StrictModel):
    text: str = Field(min_length=1)
    agenda_ids: list[str] = Field(min_length=1)


class TopicSection(StrictModel):
    key: Literal[
        "current_state", "observations", "cause_and_impact",
        "actions_and_decisions", "lotcd_differences", "team_contributions",
        "related_topics", "open_questions", "timeline",
    ]
    title: str
    body: str


class WikiTopic(StrictModel):
    topic_id: str
    title: str
    topic_kind: TopicKind
    primary_area: KnowledgeArea
    secondary_areas: list[KnowledgeArea] = Field(default_factory=list)
    state: TopicState
    importance: Literal["low", "medium", "high", "critical"]
    first_seen_week: str
    last_updated_week: str
    target_paths: list[CategoryPath]
    teams: list[str]
    source_agenda_ids: list[str]
    related_topic_ids: list[str] = Field(default_factory=list)
    current_revision_id: str


class ClaimChange(StrictModel):
    before: SupportedClaim
    after: SupportedClaim


class TopicRelationChange(StrictModel):
    relation_id: str
    action: Literal["proposed", "accepted", "rejected"]


class TopicRevision(StrictModel):
    revision_id: str
    topic_id: str
    week: str
    body_markdown: str
    sections: list[TopicSection]
    claims: list[SupportedClaim]
    source_agenda_ids: list[str]
    evidence_refs: list[str] = Field(default_factory=list)
    previous_state: TopicState | None = None
    new_state: TopicState | None = None
    added_agenda_ids: list[str] = Field(default_factory=list)
    added_claims: list[SupportedClaim] = Field(default_factory=list)
    removed_claims: list[SupportedClaim] = Field(default_factory=list)
    changed_claims: list[ClaimChange] = Field(default_factory=list)
    relation_changes: list[TopicRelationChange] = Field(default_factory=list)
    build_run_id: str = ""
    prompt_version: str = ""
    builder_version: str = ""
    summary: str = ""
    validation_results: list[str] = Field(default_factory=list)
    created_at: datetime
    model: str


class TopicAssignment(StrictModel):
    agenda_id: str
    topic_id: str
    decision: Literal["attach", "create"]
    confidence: float = Field(ge=0, le=1)
    rationale: str
    decision_source: Literal["auto", "manual"]
    decided_by: str
    decided_at: datetime


class TopicCandidate(StrictModel):
    topic_id: str
    score: float
    rank_reasons: list[str] = Field(default_factory=list)


class TopicRelation(StrictModel):
    relation_id: str
    source_topic_id: str
    target_topic_id: str
    kind: RelationKind
    agenda_ids: list[str]
    confidence: float = Field(ge=0, le=1)
    review_state: Literal["pending", "accepted", "rejected"]
    creation_source: Literal["llm", "manual", "migration"] = "migration"
    created_by: str = "migration"
    created_at: datetime | None = None
    created_build_run_id: str = ""
    creation_week: str = ""
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


class WikiReview(StrictModel):
    review_id: str
    kind: Literal["assignment", "relation"]
    agenda_id: str | None = None
    candidates: list[TopicCandidate] = Field(default_factory=list)
    relation_id: str | None = None
    relation_kind: RelationKind | None = None
    relation_agenda_ids: list[str] = Field(default_factory=list)
    rationale: str = ""
    status: Literal["pending", "resolved", "held"] = "pending"
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    resolution_action: Literal[
        "attach", "create", "hold", "accept", "reject"
    ] | None = None


class ArchivedApprovedEvidence(StrictModel):
    evidence_ref: str
    week: str
    classification_run_id: str
    item: ClassificationItem
    archived_at: datetime


class ArchivedApprovedWeek(StrictModel):
    week: str
    workflow_state: Literal["approved"] = "approved"
    classification_run_id: str
    taxonomy_version: int
    approved_at: datetime | None = None
    approved_by: str | None = None
    runs: dict[str, ClassificationRun]
    items: dict[str, ClassificationItem]
    revisions: dict[str, list[dict[str, object]]] = Field(default_factory=dict)
    evidence_refs: list[str]
    archived_at: datetime


class WikiIndex(StrictModel):
    values: list[str]


class TopicListItem(StrictModel):
    topic_id: str
    title: str
    state: TopicState
    importance: Literal["low", "medium", "high", "critical"]
    primary_area: KnowledgeArea
    target_paths: list[CategoryPath]
    teams: list[str]
    last_updated_week: str
    evidence_count: int
    rank_reasons: list[str] = Field(default_factory=list)


class WikiEvidence(StrictModel):
    agenda_id: str
    mail_id: str
    team: str
    week: str
    subject: str
    source_quote: str
    source_path: str | None = None


class WikiTopicDetail(StrictModel):
    topic: WikiTopic
    body_markdown: str
    sections: list[TopicSection]
    claims: list[SupportedClaim]
    evidence: list[WikiEvidence]
    relations: list[TopicRelation]


class WikiGraphView(StrictModel):
    topics: list[TopicListItem]
    relations: list[TopicRelation]


class LotcdWikiView(StrictModel):
    domain: Literal["DRAM", "NAND"]
    tech: str | None = None
    lotcd: str | None = None
    scope_level: Literal["domain", "tech", "lotcd"] = "lotcd"
    breadcrumb: list[str] = Field(default_factory=list)
    summary: str
    recent_changes: list[TopicListItem]
    active_topics: list[TopicListItem]
    knowledge_areas: dict[KnowledgeArea, list[TopicListItem]]
    actions_and_decisions: list[TopicListItem]
    related_lotcds: list[str]
    closed_topics: dict[str, list[TopicListItem]]
    activity: list[WikiEvidence]
    direct_activity: list[WikiEvidence] = Field(default_factory=list)
    rolled_up_activity: list[WikiEvidence] = Field(default_factory=list)
    topic_ids: list[str]


class TeamWikiView(StrictModel):
    team: str
    topics: list[TopicListItem]
    topic_ids: list[str]
    recent_activity: list[WikiEvidence]
    partner_teams: list[str]
    target_paths: list[CategoryPath]
    actions_and_decisions: list[TopicListItem]


class WeekRelationReviewEvent(StrictModel):
    relation_id: str
    relation_kind: RelationKind | None = None
    origin_week: str = ""
    action: Literal["accepted", "rejected"]
    actor: str
    reviewed_at: datetime


class WeekWikiView(StrictModel):
    week: str
    revision_id: str
    published_at: datetime
    build_run_id: str
    new_topic_ids: list[str]
    changed_topic_ids: list[str]
    resolved_topic_ids: list[str]
    reopened_topic_ids: list[str]
    actions_and_decisions: list[TopicListItem]
    new_relation_ids: list[str]
    relation_review_events: list[WeekRelationReviewEvent] = Field(default_factory=list)
    pending_assignment_count: int
    contradictions: list[str]
    teams: list[str]


class WikiBuildRun(StrictModel):
    run_id: str
    week: str
    classification_run_id: str
    taxonomy_version: int = Field(ge=1)
    status: Literal[
        "linking", "review_required", "generating", "validating", "published",
        "partially_failed", "failed",
    ]
    input_hash: str
    model: str
    affected_topic_ids: list[str] = Field(default_factory=list)
    failed_topic_ids: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class WikiReviewResolution(StrictModel):
    action: Literal["attach", "create", "hold", "accept", "reject"]
    topic_id: str | None = None
    title: str | None = None
