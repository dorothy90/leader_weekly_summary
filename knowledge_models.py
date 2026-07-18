"""Pydantic models for the mail knowledge fixture and API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

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
    lotcds: list[Lotcd] = Field(default_factory=list)


class Domain(StrictModel):
    id: str
    name: Literal["DRAM", "NAND"]
    techs: list[Tech] = Field(default_factory=list)


class GroupAlias(StrictModel):
    alias: str
    target_lotcds: list[str] = Field(min_length=1)


class TaxonomyDocument(StrictModel):
    version: int
    is_dummy: bool
    notice: str
    domains: list[Domain]
    group_aliases: list[GroupAlias] = Field(default_factory=list)


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


class Agenda(StrictModel):
    id: str
    mail_id: str
    source_quote: str
    summary: str
    scope: Literal[
        "domain", "tech", "lotcd", "multi_lotcd", "cross_domain", "unknown"
    ]
    target_paths: list[CategoryPath]
    candidate_paths: list[CategoryPath] = Field(default_factory=list)
    topic: str
    state: str
    confidence: float = Field(ge=0, le=1)
    review_required: bool


class ExcludedSegment(StrictModel):
    mail_id: str
    reason: str
    text: str


class AgendaDocument(StrictModel):
    version: int
    is_dummy: bool
    notice: str
    agendas: list[Agenda]
    excluded_segments: list[ExcludedSegment] = Field(default_factory=list)


class AgendaView(Agenda):
    subject: str
    sender_team: str
    received_at: datetime
    review_status: Literal["pending", "confirmed", "on_hold"]


class AgendaListResponse(StrictModel):
    items: list[AgendaView]
    total: int


class AgendaDetailResponse(StrictModel):
    agenda: AgendaView
    mail: Mail


class CategoryCount(StrictModel):
    path: CategoryPath
    direct: int
    descendants: int


class CategoryCountResponse(StrictModel):
    items: list[CategoryCount]


class ClassificationUpdate(StrictModel):
    target_paths: list[CategoryPath] = Field(default_factory=list)
    review_status: Literal["pending", "confirmed", "on_hold"]


class ClassificationRevision(StrictModel):
    id: int
    agenda_id: str
    before: dict[str, Any]
    after: dict[str, Any]
    changed_at: datetime
    changed_by: str


class RevisionListResponse(StrictModel):
    items: list[ClassificationRevision]


class AliasRecord(StrictModel):
    id: int
    value: str
    target_paths: list[CategoryPath]


class AliasListResponse(StrictModel):
    items: list[AliasRecord]


class AliasCreate(StrictModel):
    value: str = Field(min_length=1, max_length=200)
    target_paths: list[CategoryPath] = Field(min_length=1)


class AliasUpdate(AliasCreate):
    pass


class MappingRevision(StrictModel):
    id: int
    alias_id: int
    before: dict[str, Any] | None
    after: dict[str, Any]
    changed_at: datetime
    changed_by: str


class MappingRevisionListResponse(StrictModel):
    items: list[MappingRevision]


class KnowledgeFacets(StrictModel):
    topics: list[str]
    states: list[str]
    sender_teams: list[str]
    date_min: date | None
    date_max: date | None


class SearchSyncResponse(StrictModel):
    synced_mails: int
    indexed_agendas: int
    failed_mails: list[str]


class KnowledgeSession(StrictModel):
    user_id: str
    roles: list[str]
    can_edit: bool


class CategoryWikiPage(StrictModel):
    category_id: str
    page_kind: Literal["latest", "snapshot"]
    level: Literal["domain", "tech", "lotcd"]
    domain: Literal["DRAM", "NAND"]
    tech: str | None = None
    lotcd: str | None = None
    title: str
    product: str | None = None
    fab_id: str | None = None
    as_of_week: str
    body_markdown: str
    agenda_count: int
    open_issue_ids: list[str] = Field(default_factory=list)
    resolved_issue_ids: list[str] = Field(default_factory=list)
    review_agenda_ids: list[str] = Field(default_factory=list)
    source_agenda_ids: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)
    source_hash: str
    taxonomy_version: int
    generated_at: datetime


class TechCreate(StrictModel):
    domain: Literal["DRAM", "NAND"]
    id: str = Field(pattern=r"^[a-z0-9_-]+$", min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)


class TechUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=100)
    aliases: list[str] = Field(default_factory=list)


class LotcdCreate(StrictModel):
    domain: Literal["DRAM", "NAND"]
    tech: str = Field(min_length=1, max_length=100)
    code: str = Field(pattern=r"^[A-Z0-9]+$", min_length=2, max_length=20)
    fab_id: str = Field(min_length=1, max_length=20)
    product_code: str = Field(min_length=1, max_length=40)
    product: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)


class LotcdUpdate(StrictModel):
    fab_id: str = Field(min_length=1, max_length=20)
    product_code: str = Field(min_length=1, max_length=40)
    product: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list)
