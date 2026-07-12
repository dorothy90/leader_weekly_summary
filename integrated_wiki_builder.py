from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agenda_extract import llm_connection
from category_wiki_builder import (
    TERMINAL_STATES,
    CategoryNode,
    agenda_matches_node,
    build_page_documents,
    category_nodes,
    issue_timelines,
    page_index_definition,
)
from knowledge_models import TaxonomyDocument


MAIL_CITATION = re.compile(r"\[mail:([^\]]+)\]")


class NarrativeValidationError(ValueError):
    pass


@dataclass
class BuildResult:
    pages: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)


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


ANALYSIS_SYSTEM_PROMPT = """당신은 반도체 수율 Wiki 편집자입니다.
이전 문서와 허용된 근거를 비교해 새 사실, 유지 사실, 낡은 사실, 이슈 상태 전환,
모순, 검토 항목, 문서 목차를 구조화하십시오. mail_id와 agenda_id가 없는 주장은
SupportedClaim으로 만들지 마십시오. 하위 digest는 원본 mail_id가 추적되는 주장만 사용하십시오."""

DRAFT_SYSTEM_PROMPT = """당신은 통합 서술형 반도체 수율 Wiki 작성자입니다.
승인된 분석과 근거만 사용해 여섯 개 현재 본문 섹션과 이번 주 이력을 한국어로
작성하십시오. 사실 문단마다 [mail:<mail_id>]를 붙이십시오. 과거 이력은 작성하지
마십시오. 메일에 없는 원인, 수치, 담당자, 해결 여부를 만들지 마십시오."""


def invoke_structured(runnable, messages: list[dict[str, str]]):
    current = list(messages)
    for attempt in range(2):
        try:
            return runnable.invoke(current)
        except ValidationError as exc:
            if attempt == 1:
                raise
            current.append(
                {
                    "role": "user",
                    "content": f"스키마 오류를 수정해 다시 반환하십시오: {exc}",
                }
            )
    raise AssertionError("unreachable")


def build_llm_generators() -> tuple[AnalysisFn, DraftFn]:
    from langchain_openai import ChatOpenAI

    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
    )
    analysis_llm = llm.with_structured_output(PageAnalysis, method="function_calling")
    draft_llm = llm.with_structured_output(NarrativeDraft, method="function_calling")

    def analyze(context: dict[str, Any]) -> PageAnalysis:
        return invoke_structured(
            analysis_llm,
            [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
        )

    def draft(context: dict[str, Any], analysis: PageAnalysis) -> NarrativeDraft:
        payload = {"context": context, "analysis": analysis.model_dump()}
        return invoke_structured(
            draft_llm,
            [
                {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )

    return analyze, draft


def canonical_path(node: CategoryNode) -> str:
    return "/".join(
        value.lower() for value in (node.domain, node.tech, node.lotcd) if value
    )


def direct_agendas_for_node(
    node: CategoryNode, agendas: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    def matches(path: dict[str, Any]) -> bool:
        if path.get("domain") != node.domain:
            return False
        if node.level == "domain":
            return path.get("tech") is None and path.get("lotcd") is None
        if node.level == "tech":
            return path.get("tech") == node.tech and path.get("lotcd") is None
        return path.get("tech") == node.tech and path.get("lotcd") == node.lotcd

    return [
        agenda
        for agenda in agendas
        if agenda.get("review_status", "confirmed") == "confirmed"
        and any(matches(path) for path in agenda.get("target_paths", []))
    ]


def validate_issue_decisions(
    analysis: PageAnalysis, allowed_agendas: list[dict[str, Any]]
) -> None:
    agendas = {str(item["agenda_id"]): item for item in allowed_agendas}
    for decision in analysis.issue_decisions:
        if decision.status != "resolved":
            continue
        cited = [agendas.get(agenda_id) for agenda_id in decision.agenda_ids]
        if not any(
            item and str(item.get("state", "")).casefold() in TERMINAL_STATES
            for item in cited
        ):
            raise NarrativeValidationError(
                f"resolved issue {decision.issue_id} has no terminal agenda"
            )


def validate_draft(
    draft: NarrativeDraft, allowed_agendas: list[dict[str, Any]]
) -> list[CitationMapEntry]:
    allowed_mail_ids = {str(item["mail_id"]) for item in allowed_agendas}
    agendas_by_mail: dict[str, list[str]] = {}
    for item in allowed_agendas:
        agendas_by_mail.setdefault(str(item["mail_id"]), []).append(
            str(item["agenda_id"])
        )
    section_values = {
        "개요": draft.overview,
        "현재 상태와 주요 변화": draft.current_status,
        "원인과 영향 관계": draft.cause_and_impact,
        "조치와 효과": draft.actions_and_effects,
        "펜딩 이슈와 의사결정": draft.pending_and_decisions,
        "누적 지식": draft.accumulated_knowledge,
        "주차별 업데이트 이력": draft.weekly_update,
    }
    used: dict[str, set[str]] = {}
    for section, body in section_values.items():
        paragraphs = [
            part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()
        ]
        for paragraph in paragraphs:
            citations = MAIL_CITATION.findall(paragraph)
            if not citations:
                raise NarrativeValidationError(f"uncited paragraph in {section}")
            for mail_id in citations:
                if mail_id not in allowed_mail_ids:
                    raise NarrativeValidationError(f"unknown mail citation: {mail_id}")
                used.setdefault(mail_id, set()).add(section)
    return [
        CitationMapEntry(
            mail_id=mail_id,
            agenda_ids=sorted(agendas_by_mail[mail_id]),
            used_in_sections=sorted(sections),
            category_paths=[],
        )
        for mail_id, sections in sorted(used.items())
    ]


def build_child_digest(
    page: dict[str, Any],
    analysis: PageAnalysis,
    draft: NarrativeDraft,
) -> ChildDigest:
    return ChildDigest(
        canonical_id=str(page["canonical_id"]),
        as_of_week=str(page["as_of_week"]),
        summary=draft.overview,
        claims=[*analysis.new_claims, *analysis.retained_claims],
        issues=list(analysis.issue_decisions),
        contradictions=list(analysis.contradictions),
        confidence=page["confidence"],
    )


def _direct_children(
    node: CategoryNode, nodes: list[CategoryNode]
) -> list[CategoryNode]:
    if node.level == "domain":
        return [
            candidate
            for candidate in nodes
            if candidate.level == "tech" and candidate.domain == node.domain
        ]
    if node.level == "tech":
        return [
            candidate
            for candidate in nodes
            if candidate.level == "lotcd"
            and candidate.domain == node.domain
            and candidate.tech == node.tech
        ]
    return []


def _previous_cited_agenda_ids(previous: dict[str, Any]) -> set[str]:
    return {
        str(agenda_id)
        for citation in previous.get("citation_map", [])
        for agenda_id in (
            citation.agenda_ids
            if isinstance(citation, CitationMapEntry)
            else citation.get("agenda_ids", [])
        )
    }


def _recent_history(previous: dict[str, Any]) -> list[dict[str, Any]]:
    records = [
        item.model_dump() if isinstance(item, WeeklyHistoryEntry) else dict(item)
        for item in previous.get("weekly_history", [])
    ]
    return sorted(records, key=lambda item: str(item["week"]), reverse=True)[:2]


def _compact_issue_timelines(
    agendas: list[dict[str, Any]], as_of_week: str
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for issue in issue_timelines(agendas, as_of_week=as_of_week):
        compact.append(
            {
                key: issue[key]
                for key in (
                    "issue_id",
                    "title",
                    "current_state",
                    "event_type",
                    "first_seen_week",
                    "last_seen_week",
                    "resolved_week",
                    "is_open",
                    "updated_this_week",
                )
            }
            | {
                "events": [
                    {
                        key: event.get(key)
                        for key in (
                            "agenda_id",
                            "mail_id",
                            "week",
                            "summary",
                            "state",
                        )
                    }
                    for event in issue["events"]
                ]
            }
        )
    return compact


def _taxonomy_aliases(taxonomy: TaxonomyDocument, node: CategoryNode) -> list[str]:
    for domain in taxonomy.domains:
        if domain.name != node.domain:
            continue
        for tech in domain.techs:
            if tech.name != node.tech:
                continue
            if node.level == "tech":
                return list(tech.aliases)
            for lotcd in tech.lotcds:
                if lotcd.code == node.lotcd:
                    return list(lotcd.aliases)
    return []


def _child_agendas(
    child_digests: list[ChildDigest],
    agendas_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    agenda_ids = {
        agenda_id
        for digest in child_digests
        for evidence in [*digest.claims, *digest.issues]
        for agenda_id in evidence.agenda_ids
    }
    missing = sorted(
        agenda_id for agenda_id in agenda_ids if agenda_id not in agendas_by_id
    )
    if missing:
        raise NarrativeValidationError(f"unknown child agenda: {', '.join(missing)}")
    return [agendas_by_id[agenda_id] for agenda_id in sorted(agenda_ids)]


def build_integrated_pages(
    taxonomy: TaxonomyDocument,
    agendas: list[dict[str, Any]],
    previous_pages: dict[str, dict[str, Any]],
    *,
    as_of_week: str,
    analyze: AnalysisFn,
    draft: DraftFn,
) -> BuildResult:
    eligible_agendas = [
        agenda
        for agenda in agendas
        if not agenda.get("week") or str(agenda["week"]) <= as_of_week
    ]
    agendas_by_id = {str(agenda["agenda_id"]): agenda for agenda in eligible_agendas}
    compatibility_pages = {
        page["category_id"]: page
        for page in build_page_documents(
            taxonomy,
            eligible_agendas,
            as_of_week=as_of_week,
        )
    }
    order = {"lotcd": 0, "tech": 1, "domain": 2}
    nodes = sorted(
        category_nodes(taxonomy), key=lambda node: (order[node.level], node.id)
    )
    result = BuildResult()
    digests: dict[str, ChildDigest] = {}

    for node in nodes:
        children = _direct_children(node, nodes)
        if any(child.id in result.failures for child in children):
            result.failures[node.id] = "required child failed"
            continue

        previous = previous_pages.get(node.id, {})
        child_digests = [digests[child.id] for child in children]
        try:
            current_direct = [
                agenda
                for agenda in direct_agendas_for_node(node, eligible_agendas)
                if agenda.get("week") == as_of_week
            ]
            previous_agendas = [
                agendas_by_id[agenda_id]
                for agenda_id in sorted(_previous_cited_agenda_ids(previous))
                if agenda_id in agendas_by_id
            ]
            allowed_by_id = {
                str(agenda["agenda_id"]): agenda
                for agenda in [
                    *current_direct,
                    *previous_agendas,
                    *_child_agendas(child_digests, agendas_by_id),
                ]
            }
            allowed_agendas = [
                allowed_by_id[agenda_id] for agenda_id in sorted(allowed_by_id)
            ]
            descendant_agendas = [
                agenda
                for agenda in eligible_agendas
                if agenda_matches_node(agenda, node)
            ]
            context = {
                "node": asdict(node),
                "as_of_week": as_of_week,
                "allowed_agendas": allowed_agendas,
                "issue_timelines": _compact_issue_timelines(
                    descendant_agendas, as_of_week
                ),
                "previous_current_body_markdown": previous.get(
                    "current_body_markdown", ""
                ),
                "recent_history": _recent_history(previous),
                "child_digests": [item.model_dump() for item in child_digests],
            }
            analysis = analyze(context)
            validate_issue_decisions(analysis, allowed_agendas)
            narrative = draft(context, analysis)
            citation_map = validate_draft(narrative, allowed_agendas)
            path = canonical_path(node)
            citation_map = [
                item.model_copy(update={"category_paths": [path]})
                for item in citation_map
            ]
            current_body = render_current_body(narrative)
            previous_history = [
                (
                    item
                    if isinstance(item, WeeklyHistoryEntry)
                    else WeeklyHistoryEntry.model_validate(item)
                )
                for item in previous.get("weekly_history", [])
            ]
            current_source_mail_ids = sorted(
                item.mail_id
                for item in citation_map
                if "주차별 업데이트 이력" in item.used_in_sections
            )
            history = merge_weekly_history(
                previous_history,
                WeeklyHistoryEntry(
                    week=as_of_week,
                    body_markdown=narrative.weekly_update,
                    source_mail_ids=current_source_mail_ids,
                ),
            )
            base = compatibility_pages[node.id]
            page = {
                **base,
                "doc_type": "canonical",
                "canonical_id": path,
                "aliases": _taxonomy_aliases(taxonomy, node),
                "current_body_markdown": current_body,
                "weekly_history": [item.model_dump() for item in history],
                "body_markdown": assemble_body(current_body, history),
                "citation_map": [item.model_dump() for item in citation_map],
                "child_page_ids": [child.id for child in children],
                "confidence": narrative.confidence,
                "open_issue_count": len(base["open_issue_ids"]),
                "resolved_issue_count": len(base["resolved_issue_ids"]),
                "contradictions": list(analysis.contradictions),
                "generation_review_items": list(analysis.review_items),
                "updated_at": base["generated_at"],
            }
            result.pages.append(page)
            digests[node.id] = build_child_digest(page, analysis, narrative)
        except Exception as exc:
            result.failures[node.id] = str(exc)

    return result


def merge_weekly_history(
    previous: list[WeeklyHistoryEntry], current: WeeklyHistoryEntry
) -> list[WeeklyHistoryEntry]:
    by_week = {item.week: item for item in previous}
    by_week[current.week] = current
    return [by_week[week] for week in sorted(by_week, reverse=True)]


def render_current_body(draft: NarrativeDraft) -> str:
    sections = (
        ("개요", draft.overview),
        ("현재 상태와 주요 변화", draft.current_status),
        ("원인과 영향 관계", draft.cause_and_impact),
        ("조치와 효과", draft.actions_and_effects),
        ("펜딩 이슈와 의사결정", draft.pending_and_decisions),
        ("누적 지식", draft.accumulated_knowledge),
    )
    return "\n\n".join(f"## {title}\n\n{body.strip()}" for title, body in sections)


def assemble_body(current_body: str, history: list[WeeklyHistoryEntry]) -> str:
    entries = "\n\n".join(
        f"### {item.week}\n\n{item.body_markdown.strip()}" for item in history
    )
    return f"{current_body.strip()}\n\n## 주차별 업데이트 이력\n\n{entries}".strip()


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
