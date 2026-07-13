from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from langchain_core.exceptions import OutputParserException
from opensearchpy import OpenSearch, helpers
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agenda_extract import llm_connection
from category_wiki_builder import (
    AGENDA_INDEX,
    PAGE_INDEX,
    SOURCE_INDEX,
    TERMINAL_STATES,
    CategoryNode,
    agenda_matches_node,
    build_page_documents,
    category_nodes,
    ensure_index,
    fetch_agendas,
    issue_timelines,
    load_taxonomy,
    page_index_definition,
)
from knowledge_models import TaxonomyDocument
from wiki_issue_ledger import (
    ISSUE_INDEX,
    IssueLedgerResult,
    IssueSuggestionFn,
    WikiIssue,
    fetch_issue_ledger,
    issue_index_definition,
    resolve_issue_ledger,
    save_issue_ledger,
)


MAIL_CITATION = re.compile(r"\[mail:([^\]]+)\]")
WEEK = re.compile(r"^(\d{4})-W?(\d{2})$")


class NarrativeValidationError(ValueError):
    pass


def _normalize_week(value: object) -> str:
    week = str(value)
    match = WEEK.fullmatch(week)
    if match is None:
        return week
    return f"{match.group(1)}-W{match.group(2)}"


def select_weekly_delta(
    agendas: list[dict[str, Any]],
    week: str,
) -> list[dict[str, Any]]:
    normalized = _normalize_week(week)
    return sorted(
        (
            agenda
            for agenda in agendas
            if agenda.get("review_status", "confirmed") == "confirmed"
            and (
                _normalize_week(agenda.get("week")) == normalized
                or _normalize_week(agenda.get("updated_week")) == normalized
            )
        ),
        key=lambda agenda: str(agenda["agenda_id"]),
    )


@dataclass
class BuildResult:
    pages: list[dict[str, Any]] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    pending: dict[str, str] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)


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


class MergedWikiDocument(WikiModel):
    title: str
    current_body_markdown: str
    used_claim_ids: list[str] = Field(default_factory=list)
    used_issue_ids: list[str] = Field(default_factory=list)
    weekly_delta: str
    confidence: Literal["low", "medium", "high"]
    review_items: list[str] = Field(default_factory=list)


class MergeValidationContext(WikiModel):
    evidence_by_mail: dict[str, list[str]]
    source_docs_by_mail: dict[str, list[str]]
    required_claim_ids: set[str]
    required_issue_ids: set[str]
    resolved_issue_ids: set[str]
    reopened_issue_ids: set[str]
    stale_claim_texts: list[str]


# Legacy fixed-contract model used by build_integrated_pages and the current CLI.
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
    agenda_ids: list[str] = Field(default_factory=list)
    source_doc_ids: list[str] = Field(default_factory=list)


class CitationMapEntry(WikiModel):
    mail_id: str
    agenda_ids: list[str]
    source_doc_ids: list[str] = Field(default_factory=list)
    used_in_sections: list[str]
    category_paths: list[str]


class ChildDigest(WikiModel):
    category_id: str = ""
    canonical_id: str
    current_body_markdown: str = ""
    weekly_delta: str = ""
    used_claim_ids: list[str] = Field(default_factory=list)
    used_issue_ids: list[str] = Field(default_factory=list)
    citation_map: list[CitationMapEntry] = Field(default_factory=list)
    source_hash: str = ""
    # Legacy fields remain readable until Task 5 replaces the old pipeline.
    as_of_week: str = ""
    summary: str = ""
    claims: list[SupportedClaim] = Field(default_factory=list)
    issues: list[IssueDecision] = Field(default_factory=list)
    reopened_evidence_ids: dict[str, list[str]] = Field(default_factory=dict)
    contradictions: list[str] = Field(default_factory=list)
    confidence: Literal["low", "medium", "high"]


AnalysisFn = Callable[[dict[str, Any]], PageAnalysis]
DraftFn = Callable[[dict[str, Any], PageAnalysis], NarrativeDraft]
MergeFn = Callable[[dict[str, Any]], MergedWikiDocument]


ANALYSIS_SYSTEM_PROMPT = """당신은 반도체 수율 Wiki 편집자입니다.
이전 문서와 허용된 근거를 비교해 새 사실, 유지 사실, 낡은 사실, 이슈 상태 전환,
모순, 검토 항목, 문서 목차를 구조화하십시오. mail_id와 agenda_id가 없는 주장은
SupportedClaim으로 만들지 마십시오. 하위 digest는 원본 mail_id가 추적되는 주장만 사용하십시오.
issue_decisions는 deterministic pipeline이 채우므로 빈 배열로 반환하십시오.
validation_feedback이 있으면 기존의 유효한 근거를 버리지 말고 해당 오류를 수정하십시오."""

DRAFT_SYSTEM_PROMPT = """당신은 통합 서술형 반도체 수율 Wiki 작성자입니다.
승인된 분석과 근거만 사용해 여섯 개 현재 본문 섹션과 이번 주 이력을 한국어로
작성하십시오. 사실 문단마다 [mail:<mail_id>]를 붙이십시오. 과거 이력은 작성하지
마십시오. 메일에 없는 원인, 수치, 담당자, 해결 여부를 만들지 마십시오.
각 섹션은 핵심 사실만 최대 네 문장으로 작성하고 섹션 간 같은 사실을 반복하지 마십시오.
validation_feedback이 있으면 기존의 유효한 인용을 유지하며 해당 오류를 수정하십시오."""

MERGE_SYSTEM_PROMPT = """당신은 반도체 수율 Wiki 편집자입니다.
기존 현재 문서와 이번 주 근거를 병합해 하나의 완결된 최신 문서를 작성하십시오.
내용에 맞는 동적 H2 목차를 사용하고 고정 목차를 강제하지 마십시오.
제공된 Claim과 Issue를 빠짐없이 반영하되, 사실 문단마다 [mail:<mail_id>]를
붙이십시오. 더 최신의 명확한 근거가 있으면 현재 본문은 최신 상태로 바꾸고,
모호한 충돌은 양쪽 주장을 각각 인용해 유지하십시오. weekly_delta에는 이번 주
변경점만 쓰십시오. 과거 weekly_history는 입력일 뿐이며 다시 쓰지 마십시오.
메일에 없는 원인, 수치, 담당자, 해결 여부를 만들지 마십시오.
validation_feedback이 있으면 유효한 인용은 유지하고 지적된 오류만 수정하십시오."""


def invoke_structured(runnable, messages: list[dict[str, str]]):
    current = list(messages)
    for attempt in range(2):
        try:
            return runnable.invoke(current)
        except (ValidationError, OutputParserException) as exc:
            if attempt == 1:
                raise
            current.append(
                {
                    "role": "user",
                    "content": f"스키마 오류를 수정해 다시 반환하십시오: {exc}",
                }
            )
    raise AssertionError("unreachable")


GeneratedT = TypeVar("GeneratedT")
ValidatedT = TypeVar("ValidatedT")


def generate_with_semantic_retry(
    generator: Callable[[dict[str, Any]], GeneratedT],
    validator: Callable[[GeneratedT], ValidatedT],
    context: dict[str, Any],
    *,
    attempts: int = 3,
) -> tuple[GeneratedT, ValidatedT]:
    retry_context = context
    for attempt in range(attempts):
        generated = generator(retry_context)
        try:
            if generated is None:
                raise NarrativeValidationError("model returned no structured response")
            return generated, validator(generated)
        except NarrativeValidationError as exc:
            if attempt == attempts - 1:
                raise
            retry_context = {**context, "validation_feedback": str(exc)}
    raise AssertionError("unreachable")


def _llm_extra_body() -> dict[str, Any] | None:
    reasoning_effort = os.getenv("KNOWLEDGE_LLM_REASONING_EFFORT")
    if not reasoning_effort:
        return None
    return {"reasoning": {"effort": reasoning_effort}}


def _has_generation_evidence(context: dict[str, Any]) -> bool:
    return bool(
        context.get("allowed_agendas")
        or context.get("issue_timelines")
        or context.get("child_digests")
        or str(context.get("previous_current_body_markdown", "")).strip()
    )


def _empty_page_analysis() -> PageAnalysis:
    return PageAnalysis(outline=["개요"])


def _empty_narrative_draft() -> NarrativeDraft:
    return NarrativeDraft(
        overview="",
        current_status="",
        cause_and_impact="",
        actions_and_effects="",
        pending_and_decisions="",
        accumulated_knowledge="",
        weekly_update="",
        confidence="low",
    )


def build_llm_generators() -> tuple[AnalysisFn, DraftFn]:
    from langchain_openai import ChatOpenAI

    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
        extra_body=_llm_extra_body(),
    )
    analysis_llm = llm.with_structured_output(PageAnalysis, method="function_calling")
    draft_llm = llm.with_structured_output(NarrativeDraft, method="function_calling")

    def analyze(context: dict[str, Any]) -> PageAnalysis:
        if not _has_generation_evidence(context):
            return _empty_page_analysis()
        return invoke_structured(
            analysis_llm,
            [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
        )

    def draft(context: dict[str, Any], analysis: PageAnalysis) -> NarrativeDraft:
        if not _has_generation_evidence(context):
            return _empty_narrative_draft()
        payload = {"context": context, "analysis": analysis.model_dump()}
        return invoke_structured(
            draft_llm,
            [
                {"role": "system", "content": DRAFT_SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )

    return analyze, draft


def build_page_merger() -> Callable[[dict[str, Any]], MergedWikiDocument]:
    from langchain_openai import ChatOpenAI

    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
        extra_body=_llm_extra_body(),
    )
    runnable = llm.with_structured_output(
        MergedWikiDocument,
        method="function_calling",
    )

    def merge(context: dict[str, Any]) -> MergedWikiDocument:
        if (
            not context["evidence_by_mail"]
            and not context["previous_current_body_markdown"].strip()
        ):
            return MergedWikiDocument(
                title=context["node"]["title"],
                current_body_markdown="## 문서 범위",
                used_claim_ids=[],
                used_issue_ids=[],
                weekly_delta="",
                confidence="low",
                review_items=[],
            )
        return invoke_structured(
            runnable,
            [
                {"role": "system", "content": MERGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False),
                },
            ],
        )

    return merge


def canonical_path(node: CategoryNode) -> str:
    return "/".join(
        value.lower() for value in (node.domain, node.tech, node.lotcd) if value
    )


def affected_node_ids(
    taxonomy: TaxonomyDocument,
    delta: list[dict[str, Any]],
    *,
    rebuild_all: bool = False,
) -> set[str]:
    nodes = category_nodes(taxonomy)
    if rebuild_all:
        return {node.id for node in nodes}
    domain_ids = {
        node.domain: node.id for node in nodes if node.level == "domain"
    }
    tech_ids = {
        (node.domain, node.tech): node.id for node in nodes if node.level == "tech"
    }
    selected: set[str] = set()
    for agenda in delta:
        for node in nodes:
            if not agenda_matches_node(agenda, node):
                continue
            selected.add(node.id)
            if node.level in {"tech", "lotcd"}:
                selected.add(domain_ids[node.domain])
            if node.level == "lotcd":
                selected.add(tech_ids[(node.domain, node.tech)])
    return selected


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
    analysis: PageAnalysis,
    allowed_agendas: list[dict[str, Any]],
    expected_issues: dict[str, str] | None = None,
    reopened_evidence_ids: dict[str, set[str]] | None = None,
) -> None:
    agendas = {str(item["agenda_id"]): item for item in allowed_agendas}
    decisions: dict[str, IssueDecision] = {}
    for decision in analysis.issue_decisions:
        if decision.issue_id in decisions:
            raise NarrativeValidationError(
                f"duplicate issue decision: {decision.issue_id}"
            )
        decisions[decision.issue_id] = decision
        cited = []
        for agenda_id in decision.agenda_ids:
            agenda = agendas.get(agenda_id)
            if agenda is None:
                raise NarrativeValidationError(f"unknown agenda: {agenda_id}")
            if str(agenda.get("issue_id")) != decision.issue_id:
                raise NarrativeValidationError(
                    f"issue {decision.issue_id} disagrees with agenda {agenda_id}"
                )
            if str(agenda.get("mail_id")) not in decision.mail_ids:
                raise NarrativeValidationError(
                    f"mail and agenda evidence disagree: {agenda_id}"
                )
            cited.append(agenda)
        cited_mail_ids = {str(item.get("mail_id")) for item in cited}
        if any(mail_id not in cited_mail_ids for mail_id in decision.mail_ids):
            raise NarrativeValidationError(
                f"mail and agenda evidence disagree for issue {decision.issue_id}"
            )
        if decision.status == "resolved" and not any(
            str(item.get("state", "")).casefold() in TERMINAL_STATES
            for item in cited
        ):
            raise NarrativeValidationError(
                f"resolved issue {decision.issue_id} has no terminal agenda"
            )
        if decision.status in {"ongoing", "reopened"}:
            cited_nonterminal = [
                item
                for item in cited
                if str(item.get("state", "")).casefold()
                not in TERMINAL_STATES
            ]
            if not cited_nonterminal:
                raise NarrativeValidationError(
                    f"{decision.status} issue {decision.issue_id} "
                    "has no non-terminal agenda"
                )
            if decision.status == "reopened":
                terminal_events = [
                    item
                    for item in allowed_agendas
                    if str(item.get("issue_id")) == decision.issue_id
                    and str(item.get("state", "")).casefold()
                    in TERMINAL_STATES
                ]

                def event_key(item: dict[str, Any]) -> tuple[str, str]:
                    return (
                        _normalize_week(item.get("week")),
                        str(item["agenda_id"]),
                    )

                deterministic_evidence = (reopened_evidence_ids or {}).get(
                    decision.issue_id, set()
                )
                has_later_nonterminal = any(
                    str(item["agenda_id"]) in deterministic_evidence
                    for item in cited_nonterminal
                ) or any(
                    event_key(terminal) < event_key(nonterminal)
                    for terminal in terminal_events
                    for nonterminal in cited_nonterminal
                )
                if not has_later_nonterminal:
                    raise NarrativeValidationError(
                        f"reopened issue {decision.issue_id} has no non-terminal "
                        "event later than terminal evidence"
                    )

    for issue_id, expected_status in (expected_issues or {}).items():
        decision = decisions.get(issue_id)
        if decision is None:
            raise NarrativeValidationError(f"missing issue decision: {issue_id}")
        if expected_status == "resolved":
            valid_statuses = {"resolved"}
        else:
            valid_statuses = {"ongoing", "reopened"}
        if decision.status not in valid_statuses:
            raise NarrativeValidationError(
                f"issue {issue_id} expected {expected_status}, got {decision.status}"
            )


def validate_stage1_evidence(
    analysis: PageAnalysis, allowed_agendas: list[dict[str, Any]]
) -> dict[str, list[str]]:
    agendas = {str(item["agenda_id"]): item for item in allowed_agendas}
    evidence_by_mail: dict[str, set[str]] = {}
    for evidence in [
        *analysis.new_claims,
        *analysis.retained_claims,
        *analysis.issue_decisions,
    ]:
        if not evidence.agenda_ids:
            raise NarrativeValidationError("Stage-1 evidence requires an agenda")
        if not evidence.mail_ids:
            raise NarrativeValidationError("Stage-1 evidence requires a mail")
        referenced = []
        for agenda_id in evidence.agenda_ids:
            agenda = agendas.get(agenda_id)
            if agenda is None:
                raise NarrativeValidationError(f"unknown agenda: {agenda_id}")
            referenced.append(agenda)
            mail_id = str(agenda["mail_id"])
            if mail_id not in evidence.mail_ids:
                raise NarrativeValidationError(
                    f"mail and agenda evidence disagree: {mail_id}/{agenda_id}"
                )
            evidence_by_mail.setdefault(mail_id, set()).add(agenda_id)
        referenced_mail_ids = {str(item["mail_id"]) for item in referenced}
        for mail_id in evidence.mail_ids:
            if mail_id not in referenced_mail_ids:
                raise NarrativeValidationError(
                    f"mail and agenda evidence disagree: {mail_id}"
                )
    return {
        mail_id: sorted(agenda_ids)
        for mail_id, agenda_ids in sorted(evidence_by_mail.items())
    }


def _factual_units(markdown: str) -> list[str]:
    units: list[str] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            units.append("\n".join(paragraph))
            paragraph.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue
        if re.match(r"^#{1,6}\s+", stripped):
            flush_paragraph()
            continue
        if re.match(r"^(?:[-+*]|\d+\.)\s+", stripped):
            flush_paragraph()
            units.append(stripped)
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush_paragraph()
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            units.append(stripped)
            continue
        paragraph.append(stripped)
    flush_paragraph()
    return units


def _factual_units_by_heading(markdown: str) -> list[tuple[str, str]]:
    section = "본문"
    buffer: list[str] = []
    result: list[tuple[str, str]] = []

    def flush() -> None:
        if not buffer:
            return
        result.extend((section, unit) for unit in _factual_units("\n".join(buffer)))
        buffer.clear()

    for line in markdown.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            flush()
            section = heading.group(1)
        else:
            buffer.append(line)
    flush()
    return result


def validate_merged_document(
    document: MergedWikiDocument,
    context: MergeValidationContext,
) -> list[CitationMapEntry]:
    missing_claims = context.required_claim_ids - set(document.used_claim_ids)
    if missing_claims:
        raise NarrativeValidationError(
            "missing claim coverage: " + ", ".join(sorted(missing_claims))
        )
    missing_issues = context.required_issue_ids - set(document.used_issue_ids)
    if missing_issues:
        raise NarrativeValidationError(
            "missing issue coverage: " + ", ".join(sorted(missing_issues))
        )
    headings = re.findall(r"^##\s+(.+)$", document.current_body_markdown, re.M)
    heading_ids = [
        re.sub(r"[^\w가-힣-]", "", re.sub(r"\s+", "-", heading.casefold()))
        for heading in headings
    ]
    if not headings or len(heading_ids) != len(set(heading_ids)):
        raise NarrativeValidationError("current body requires unique dynamic headings")
    for stale in context.stale_claim_texts:
        if stale and stale in document.current_body_markdown:
            raise NarrativeValidationError("stale claim remains in current body")
    used: dict[str, set[str]] = {}
    sections = [
        *_factual_units_by_heading(document.current_body_markdown),
        *(
            ("주차별 업데이트 이력", unit)
            for unit in _factual_units(document.weekly_delta)
        ),
    ]
    for section, unit in sections:
        mail_ids = MAIL_CITATION.findall(unit)
        if not mail_ids:
            raise NarrativeValidationError("uncited factual unit")
        for mail_id in mail_ids:
            if mail_id not in context.evidence_by_mail:
                raise NarrativeValidationError("unknown mail citation: " + mail_id)
            if not context.source_docs_by_mail.get(mail_id):
                raise NarrativeValidationError("citation has no raw source: " + mail_id)
            used.setdefault(mail_id, set()).add(section)
    cited_claim_ids = {
        "claim:" + agenda_id
        for mail_id in used
        for agenda_id in context.evidence_by_mail[mail_id]
    }
    agenda_backed_claims = {
        claim_id
        for claim_id in context.required_claim_ids
        if claim_id.startswith("claim:")
    }
    uncited_claims = agenda_backed_claims - cited_claim_ids
    if uncited_claims:
        raise NarrativeValidationError(
            "claims declared used but not cited: "
            + ", ".join(sorted(uncited_claims))
        )
    return [
        CitationMapEntry(
            mail_id=mail_id,
            agenda_ids=context.evidence_by_mail[mail_id],
            source_doc_ids=context.source_docs_by_mail[mail_id],
            used_in_sections=sorted(used_in_sections),
            category_paths=[],
        )
        for mail_id, used_in_sections in sorted(used.items())
    ]


def validate_draft(
    draft: NarrativeDraft, evidence_by_mail: dict[str, list[str]]
) -> list[CitationMapEntry]:
    allowed_mail_ids = set(evidence_by_mail)
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
        for unit in _factual_units(body):
            citations = MAIL_CITATION.findall(unit)
            if not citations:
                raise NarrativeValidationError(f"uncited factual unit in {section}")
            for mail_id in citations:
                if mail_id not in allowed_mail_ids:
                    raise NarrativeValidationError(f"unknown mail citation: {mail_id}")
                used.setdefault(mail_id, set()).add(section)
    return [
        CitationMapEntry(
            mail_id=mail_id,
            agenda_ids=evidence_by_mail[mail_id],
            used_in_sections=sorted(sections),
            category_paths=[],
        )
        for mail_id, sections in sorted(used.items())
    ]


def build_child_digest(
    page: dict[str, Any],
    analysis: PageAnalysis,
    draft: NarrativeDraft,
    reopened_evidence_ids: dict[str, set[str]] | None = None,
) -> ChildDigest:
    return ChildDigest(
        canonical_id=str(page["canonical_id"]),
        as_of_week=str(page["as_of_week"]),
        summary=draft.overview,
        claims=[*analysis.new_claims, *analysis.retained_claims],
        issues=list(analysis.issue_decisions),
        reopened_evidence_ids={
            issue_id: sorted(agenda_ids)
            for issue_id, agenda_ids in sorted(
                (reopened_evidence_ids or {}).items()
            )
        },
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
        if any(
            section != "주차별 업데이트 이력"
            and WEEK.fullmatch(str(section)) is None
            for section in (
                citation.used_in_sections
                if isinstance(citation, CitationMapEntry)
                else citation.get("used_in_sections", [])
            )
        )
        for agenda_id in (
            citation.agenda_ids
            if isinstance(citation, CitationMapEntry)
            else citation.get("agenda_ids", [])
        )
    }


def _merge_citation_maps(
    current: list[CitationMapEntry],
    previous: list[CitationMapEntry],
    history: list[WeeklyHistoryEntry],
) -> list[CitationMapEntry]:
    history_sections: dict[str, set[str]] = {}
    for entry in history:
        for mail_id in entry.source_mail_ids:
            history_sections.setdefault(mail_id, set()).add(
                _normalize_week(entry.week)
            )
    merged: dict[str, dict[str, set[str]]] = {}

    def add(citation: CitationMapEntry, sections: list[str]) -> None:
        record = merged.setdefault(
            citation.mail_id,
            {
                "agenda_ids": set(),
                "source_doc_ids": set(),
                "used_in_sections": set(),
            },
        )
        record["agenda_ids"].update(citation.agenda_ids)
        record["source_doc_ids"].update(citation.source_doc_ids)
        record["used_in_sections"].update(sections)

    for citation in current:
        add(citation, citation.used_in_sections)
    for citation in previous:
        if citation.mail_id in history_sections:
            add(citation, sorted(history_sections[citation.mail_id]))

    missing = sorted(set(history_sections) - set(merged))
    if missing:
        raise NarrativeValidationError(
            "retained history has no citation mapping: " + ", ".join(missing)
        )
    incomplete = sorted(
        mail_id
        for mail_id, record in merged.items()
        if not record["agenda_ids"] or not record["source_doc_ids"]
    )
    if incomplete:
        raise NarrativeValidationError(
            "citation evidence is incomplete: " + ", ".join(incomplete)
        )
    return [
        CitationMapEntry(
            mail_id=mail_id,
            agenda_ids=sorted(record["agenda_ids"]),
            source_doc_ids=sorted(record["source_doc_ids"]),
            used_in_sections=sorted(record["used_in_sections"]),
            category_paths=[],
        )
        for mail_id, record in sorted(merged.items())
    ]


def _recent_history(previous: dict[str, Any]) -> list[dict[str, Any]]:
    records = [
        item.model_dump() if isinstance(item, WeeklyHistoryEntry) else dict(item)
        for item in previous.get("weekly_history", [])
    ]
    return sorted(records, key=lambda item: _normalize_week(item["week"]), reverse=True)[
        :2
    ]


def _is_legacy_page(previous: dict[str, Any]) -> bool:
    return (
        previous.get("page_kind") == "latest"
        and bool(previous.get("body_markdown"))
        and all(
            field not in previous
            for field in (
                "current_body_markdown",
                "weekly_history",
                "citation_map",
            )
        )
    )


def _bootstrap_legacy_history(
    agendas: list[dict[str, Any]], as_of_week: str
) -> tuple[list[WeeklyHistoryEntry], list[CitationMapEntry]]:
    by_week: dict[str, list[dict[str, Any]]] = {}
    for agenda in agendas:
        week = _normalize_week(agenda.get("week"))
        if week >= as_of_week:
            continue
        by_week.setdefault(week, []).append(agenda)

    history: list[WeeklyHistoryEntry] = []
    citations: list[CitationMapEntry] = []
    for week, week_agendas in sorted(by_week.items(), reverse=True):
        ordered = sorted(week_agendas, key=lambda item: str(item["agenda_id"]))
        history.append(
            WeeklyHistoryEntry(
                week=week,
                body_markdown="\n".join(
                    f"- {agenda.get('summary', '')} [mail:{agenda['mail_id']}]"
                    for agenda in ordered
                ),
                source_mail_ids=sorted(
                    {str(agenda["mail_id"]) for agenda in ordered}
                ),
                agenda_ids=sorted(str(agenda["agenda_id"]) for agenda in ordered),
                source_doc_ids=sorted(
                    {
                        str(source_id)
                        for agenda in ordered
                        for source_id in agenda.get("source_doc_ids", [])
                    }
                ),
            )
        )
        agendas_by_mail: dict[str, list[str]] = {}
        source_docs_by_mail: dict[str, set[str]] = {}
        for agenda in ordered:
            mail_id = str(agenda["mail_id"])
            agendas_by_mail.setdefault(mail_id, []).append(str(agenda["agenda_id"]))
            source_docs_by_mail.setdefault(mail_id, set()).update(
                str(source_id) for source_id in agenda.get("source_doc_ids", [])
            )
        citations.extend(
            CitationMapEntry(
                mail_id=mail_id,
                agenda_ids=sorted(agenda_ids),
                source_doc_ids=sorted(source_docs_by_mail[mail_id]),
                used_in_sections=[week],
                category_paths=[],
            )
            for mail_id, agenda_ids in sorted(agendas_by_mail.items())
        )
    return history, citations


def _compact_issue_timelines(
    agendas: list[dict[str, Any]], as_of_week: str
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for issue in issue_timelines(agendas, as_of_week=as_of_week):
        events = issue["events"]
        selected_ids = {str(events[0]["agenda_id"]), str(events[-1]["agenda_id"])}
        transition = None
        for index in range(len(events) - 2, 0, -1):
            state = str(events[index].get("state", "")).casefold()
            previous_state = str(
                events[index - 1].get("state", "")
            ).casefold()
            if (
                state in TERMINAL_STATES
                or (state in TERMINAL_STATES)
                != (previous_state in TERMINAL_STATES)
                or state != previous_state
            ):
                transition = events[index]
                break
        if transition is not None:
            selected_ids.add(str(transition["agenda_id"]))
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
                    for event in events
                    if str(event["agenda_id"]) in selected_ids
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


def _expected_issue_statuses(
    timelines: list[dict[str, Any]], child_digests: list[ChildDigest]
) -> dict[str, str]:
    expected = {
        str(issue["issue_id"]): (
            "reopened"
            if issue["event_type"] == "reopened"
            else "ongoing"
            if issue["is_open"]
            else "resolved"
        )
        for issue in timelines
    }
    for digest in child_digests:
        for decision in digest.issues:
            existing = expected.get(decision.issue_id)
            if existing is not None and (
                (existing == "resolved") != (decision.status == "resolved")
            ):
                raise NarrativeValidationError(
                    f"conflicting expected issue state: {decision.issue_id}"
                )
            expected[decision.issue_id] = decision.status
    return expected


def deterministic_issue_decisions(
    timelines: list[dict[str, Any]], child_digests: list[ChildDigest]
) -> list[IssueDecision]:
    decisions: dict[str, IssueDecision] = {}

    def merge(decision: IssueDecision) -> None:
        existing = decisions.get(decision.issue_id)
        if existing is None:
            decisions[decision.issue_id] = decision
            return
        if existing.status != decision.status:
            raise NarrativeValidationError(
                f"conflicting deterministic issue state: {decision.issue_id}"
            )
        decisions[decision.issue_id] = existing.model_copy(
            update={
                "mail_ids": sorted({*existing.mail_ids, *decision.mail_ids}),
                "agenda_ids": sorted(
                    {*existing.agenda_ids, *decision.agenda_ids}
                ),
            }
        )

    for timeline in timelines:
        latest = timeline["events"][-1]
        merge(
            IssueDecision(
                issue_id=str(timeline["issue_id"]),
                status=(
                    "reopened"
                    if timeline["event_type"] == "reopened"
                    else "ongoing"
                    if timeline["is_open"]
                    else "resolved"
                ),
                summary=str(timeline["title"]),
                mail_ids=[str(latest["mail_id"])],
                agenda_ids=[str(latest["agenda_id"])],
            )
        )
    for digest in child_digests:
        for decision in digest.issues:
            merge(decision)
    return [decisions[issue_id] for issue_id in sorted(decisions)]


def _reopened_evidence_ids(
    agendas: list[dict[str, Any]], child_digests: list[ChildDigest]
) -> dict[str, set[str]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for agenda in agendas:
        grouped.setdefault(str(agenda["issue_id"]), []).append(agenda)
    evidence: dict[str, set[str]] = {}
    for issue_id, events in grouped.items():
        terminal_seen = False
        for event in sorted(
            events,
            key=lambda item: (
                _normalize_week(item.get("week")),
                str(item["agenda_id"]),
            ),
        ):
            if str(event.get("state", "")).casefold() in TERMINAL_STATES:
                terminal_seen = True
            elif terminal_seen:
                evidence.setdefault(issue_id, set()).add(
                    str(event["agenda_id"])
                )
    for digest in child_digests:
        for issue_id, agenda_ids in digest.reopened_evidence_ids.items():
            evidence.setdefault(issue_id, set()).update(agenda_ids)
    return evidence


def build_merge_context(
    *,
    node: CategoryNode,
    week: str,
    agendas: list[dict[str, Any]],
    delta: list[dict[str, Any]],
    previous: dict[str, Any],
    issues: dict[str, WikiIssue],
    agenda_to_issue: dict[str, str],
    child_digests: list[ChildDigest],
    base_page: dict[str, Any],
) -> dict[str, Any]:
    direct = direct_agendas_for_node(node, agendas)
    direct_ids = {str(item["agenda_id"]) for item in direct}
    direct_delta = [
        item for item in delta if str(item["agenda_id"]) in direct_ids
    ]
    evidence_by_mail: dict[str, set[str]] = {}
    source_docs_by_mail: dict[str, set[str]] = {}
    for agenda in direct:
        mail_id = str(agenda["mail_id"])
        evidence_by_mail.setdefault(mail_id, set()).add(str(agenda["agenda_id"]))
        source_docs_by_mail.setdefault(mail_id, set()).update(
            str(item) for item in agenda.get("source_doc_ids", [])
        )
    previous_citations = [
        item
        if isinstance(item, CitationMapEntry)
        else CitationMapEntry.model_validate(item)
        for item in previous.get("citation_map", [])
    ]
    for citation in previous_citations:
        evidence_by_mail.setdefault(citation.mail_id, set()).update(
            citation.agenda_ids
        )
        source_docs_by_mail.setdefault(citation.mail_id, set()).update(
            citation.source_doc_ids
        )
    for child in child_digests:
        for citation in child.citation_map:
            evidence_by_mail.setdefault(citation.mail_id, set()).update(
                citation.agenda_ids
            )
            source_docs_by_mail.setdefault(citation.mail_id, set()).update(
                citation.source_doc_ids
            )

    required_claim_ids = {
        "claim:" + agenda_id
        for agenda_ids in evidence_by_mail.values()
        for agenda_id in agenda_ids
    }
    required_claim_ids.update(
        claim_id
        for child in child_digests
        for claim_id in child.used_claim_ids
    )
    required_issue_ids = {
        agenda_to_issue[str(agenda["agenda_id"])]
        for agenda in direct
        if str(agenda["agenda_id"]) in agenda_to_issue
    }
    required_issue_ids.update(
        issue_id
        for child in child_digests
        for issue_id in child.used_issue_ids
    )
    required_issue_ids.update(
        str(issue_id) for issue_id in previous.get("issue_ids", [])
    )
    relevant_issues = {
        issue_id: issues[issue_id].model_dump(mode="json")
        for issue_id in sorted(required_issue_ids)
    }
    return {
        "node": asdict(node),
        "week": week,
        "base_page": base_page,
        "previous_current_body_markdown": previous.get(
            "current_body_markdown", ""
        ),
        "previous_weekly_history": previous.get("weekly_history", []),
        "previous_citation_map": previous.get("citation_map", []),
        "direct_agendas": direct,
        "weekly_delta_agendas": direct_delta,
        "child_digests": [
            item.model_dump(mode="json") for item in child_digests
        ],
        "evidence_by_mail": {
            key: sorted(value)
            for key, value in sorted(evidence_by_mail.items())
        },
        "source_docs_by_mail": {
            key: sorted(value)
            for key, value in sorted(source_docs_by_mail.items())
        },
        "required_claim_ids": sorted(required_claim_ids),
        "required_issue_ids": sorted(required_issue_ids),
        "issues": relevant_issues,
        "has_weekly_change": bool(direct_delta)
        or any(child.weekly_delta.strip() for child in child_digests),
    }


def merge_source_hash(context: dict[str, Any]) -> str:
    payload = {
        "strategy": "incremental_merge:v2",
        "node_id": context["node"]["id"],
        "week": context["week"],
        "agendas": [
            {
                "agenda_id": item["agenda_id"],
                "content_hash": item.get("content_hash", ""),
            }
            for item in context["direct_agendas"]
        ],
        "issues": context["issues"],
        "children": [
            {
                "category_id": item["category_id"],
                "source_hash": item["source_hash"],
            }
            for item in context["child_digests"]
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def retry_context_to_validation_context(
    context: dict[str, Any],
) -> MergeValidationContext:
    validate_canonical_issue_states(context["issues"])
    resolved = {
        issue_id
        for issue_id, issue in context["issues"].items()
        if issue["current_status"] == "resolved"
    }
    reopened = {
        issue_id
        for issue_id, issue in context["issues"].items()
        if issue["current_status"] == "reopened"
    }
    return MergeValidationContext(
        evidence_by_mail=context["evidence_by_mail"],
        source_docs_by_mail=context["source_docs_by_mail"],
        required_claim_ids=set(context["required_claim_ids"]),
        required_issue_ids=set(context["required_issue_ids"]),
        resolved_issue_ids=resolved,
        reopened_issue_ids=reopened,
        stale_claim_texts=[],
    )


def validate_canonical_issue_states(
    issues: dict[str, dict[str, Any]],
) -> None:
    for issue_id, issue in issues.items():
        events = sorted(
            issue["state_history"],
            key=lambda item: (
                _normalize_week(item["week"]),
                item["agenda_id"],
            ),
        )
        terminal_positions = [
            index
            for index, event in enumerate(events)
            if str(event["state"]).casefold() in TERMINAL_STATES
        ]
        if issue["current_status"] == "resolved":
            if not events or len(events) - 1 not in terminal_positions:
                raise NarrativeValidationError(
                    "resolved issue has no latest terminal evidence: " + issue_id
                )
        if issue["current_status"] == "reopened":
            if not terminal_positions or terminal_positions[-1] >= len(events) - 1:
                raise NarrativeValidationError(
                    "reopened issue has no later non-terminal evidence: " + issue_id
                )


def build_page_from_merge(
    node: CategoryNode,
    document: MergedWikiDocument,
    citations: list[CitationMapEntry],
    context: dict[str, Any],
    previous: dict[str, Any],
    source_hash: str,
) -> dict[str, Any]:
    path = canonical_path(node)
    current_citations = [
        item.model_copy(update={"category_paths": [path]})
        for item in citations
    ]
    history = [
        item
        if isinstance(item, WeeklyHistoryEntry)
        else WeeklyHistoryEntry.model_validate(item)
        for item in previous.get("weekly_history", [])
    ]
    if context["has_weekly_change"]:
        weekly_mail_ids = sorted(
            item.mail_id
            for item in current_citations
            if "주차별 업데이트 이력" in item.used_in_sections
        )
        history = merge_weekly_history(
            history,
            WeeklyHistoryEntry(
                week=context["week"],
                body_markdown=document.weekly_delta,
                source_mail_ids=weekly_mail_ids,
                agenda_ids=sorted(
                    {
                        agenda_id
                        for item in current_citations
                        if item.mail_id in weekly_mail_ids
                        for agenda_id in item.agenda_ids
                    }
                ),
                source_doc_ids=sorted(
                    {
                        source_id
                        for item in current_citations
                        if item.mail_id in weekly_mail_ids
                        for source_id in item.source_doc_ids
                    }
                ),
            ),
        )
    previous_citations = [
        item
        if isinstance(item, CitationMapEntry)
        else CitationMapEntry.model_validate(item)
        for item in previous.get("citation_map", [])
    ]
    citation_map = [
        item.model_copy(update={"category_paths": [path]})
        for item in _merge_citation_maps(
            current_citations,
            previous_citations,
            history,
        )
    ]
    source_agenda_ids = sorted(
        {agenda_id for item in citation_map for agenda_id in item.agenda_ids}
    )
    source_doc_ids = sorted(
        {source_id for item in citation_map for source_id in item.source_doc_ids}
    )
    issue_ids = sorted(document.used_issue_ids)
    resolved_ids = sorted(
        issue_id
        for issue_id in issue_ids
        if context["issues"][issue_id]["current_status"] == "resolved"
    )
    open_ids = sorted(set(issue_ids) - set(resolved_ids))
    base = context["base_page"]
    now = datetime.now(UTC).isoformat()
    return {
        **base,
        "doc_type": "canonical",
        "canonical_id": path,
        "current_body_markdown": document.current_body_markdown,
        "weekly_history": [item.model_dump() for item in history],
        "body_markdown": assemble_body(document.current_body_markdown, history),
        "citation_map": [item.model_dump() for item in citation_map],
        "child_page_ids": [
            item["canonical_id"] for item in context["child_digests"]
        ],
        "confidence": document.confidence,
        "agenda_count": len(source_agenda_ids),
        "issue_ids": issue_ids,
        "open_issue_ids": open_ids,
        "resolved_issue_ids": resolved_ids,
        "open_issue_count": len(open_ids),
        "resolved_issue_count": len(resolved_ids),
        "generation_review_items": document.review_items,
        "source_agenda_ids": source_agenda_ids,
        "source_doc_ids": source_doc_ids,
        "source_hash": source_hash,
        "schema_version": 2,
        "generation_strategy": "incremental_merge",
        "updated_at": now,
    }


def child_digest_from_page(page: dict[str, Any]) -> ChildDigest:
    history = page.get("weekly_history", [])
    weekly_delta = next(
        (
            str(item["body_markdown"])
            for item in history
            if _normalize_week(item["week"])
            == _normalize_week(page["as_of_week"])
        ),
        "",
    )
    citation_map = [
        item
        if isinstance(item, CitationMapEntry)
        else CitationMapEntry.model_validate(item)
        for item in page.get("citation_map", [])
    ]
    agenda_ids = sorted(
        {agenda_id for item in citation_map for agenda_id in item.agenda_ids}
    )
    return ChildDigest(
        category_id=str(page["category_id"]),
        canonical_id=str(page["canonical_id"]),
        current_body_markdown=str(page["current_body_markdown"]),
        weekly_delta=weekly_delta,
        used_claim_ids=["claim:" + agenda_id for agenda_id in agenda_ids],
        used_issue_ids=[str(item) for item in page.get("issue_ids", [])],
        citation_map=citation_map,
        source_hash=str(page["source_hash"]),
        confidence=str(page.get("confidence", "low")),
    )


def build_incremental_pages(
    taxonomy: TaxonomyDocument,
    agendas: list[dict[str, Any]],
    previous_pages: dict[str, dict[str, Any]],
    issue_result: IssueLedgerResult,
    *,
    as_of_week: str,
    merge: MergeFn,
    rebuild_all: bool = False,
    on_page: Callable[[dict[str, Any]], None] | None = None,
    selected_node_ids: set[str] | None = None,
) -> BuildResult:
    week = _normalize_week(as_of_week)
    eligible_agendas = [
        agenda
        for agenda in agendas
        if not agenda.get("week")
        or _normalize_week(agenda["week"]) <= week
    ]
    delta = select_weekly_delta(eligible_agendas, week)
    selected_ids = (
        selected_node_ids
        if selected_node_ids is not None
        else affected_node_ids(taxonomy, delta, rebuild_all=rebuild_all)
    )
    compatibility_pages = {
        page["category_id"]: page
        for page in build_page_documents(
            taxonomy,
            eligible_agendas,
            as_of_week=week,
        )
    }
    order = {"lotcd": 0, "tech": 1, "domain": 2}
    nodes = sorted(
        category_nodes(taxonomy),
        key=lambda node: (order[node.level], node.id),
    )
    result = BuildResult()
    digests: dict[str, ChildDigest] = {}
    for node in nodes:
        if node.id not in selected_ids:
            continue
        children = [
            child
            for child in _direct_children(node, nodes)
            if child.id in selected_ids
        ]
        failed_children = [
            child.id
            for child in children
            if child.id in result.failures or child.id in result.pending
        ]
        if failed_children:
            result.pending[node.id] = "required child failed"
            print(f"[wiki] pending {node.id}: required child failed")
            continue
        print(f"[wiki] start {node.id}")
        try:
            context = build_merge_context(
                node=node,
                week=week,
                agendas=eligible_agendas,
                delta=delta,
                previous=(
                    {} if rebuild_all else previous_pages.get(node.id, {})
                ),
                issues=issue_result.issues,
                agenda_to_issue=issue_result.agenda_to_issue,
                child_digests=[digests[child.id] for child in children],
                base_page=compatibility_pages[node.id],
            )
            source_hash = merge_source_hash(context)
            previous = previous_pages.get(node.id, {})
            if (
                not rebuild_all
                and previous.get("source_hash") == source_hash
            ):
                result.skipped.append(node.id)
                digests[node.id] = child_digest_from_page(previous)
                continue
            document, citations = generate_with_semantic_retry(
                lambda retry_context: (
                    print(
                        f"[wiki] retry {node.id}: "
                        f"{retry_context['validation_feedback']}"
                    )
                    if "validation_feedback" in retry_context
                    else None
                )
                or merge(retry_context),
                lambda candidate: validate_merged_document(
                    candidate,
                    retry_context_to_validation_context(context),
                ),
                context,
            )
            page = build_page_from_merge(
                node,
                document,
                citations,
                context,
                previous,
                source_hash,
            )
            if on_page:
                on_page(page)
            result.pages.append(page)
            digests[node.id] = child_digest_from_page(page)
        except Exception as exc:
            result.failures[node.id] = str(exc)
            print(f"[wiki] failure {node.id}: {exc}")
    return result


def build_integrated_pages(
    taxonomy: TaxonomyDocument,
    agendas: list[dict[str, Any]],
    previous_pages: dict[str, dict[str, Any]],
    *,
    as_of_week: str,
    analyze: AnalysisFn,
    draft: DraftFn,
) -> BuildResult:
    as_of_week = _normalize_week(as_of_week)
    eligible_agendas = [
        {**agenda, "week": _normalize_week(agenda["week"])}
        if agenda.get("week")
        else agenda
        for agenda in agendas
        if not agenda.get("week")
        or _normalize_week(agenda["week"]) <= as_of_week
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
            node_direct_agendas = direct_agendas_for_node(
                node, eligible_agendas
            )
            is_legacy = _is_legacy_page(previous)
            legacy_prompt_agendas = node_direct_agendas if is_legacy else []
            if not is_legacy:
                legacy_history_agendas = []
            elif node.level == "lotcd":
                legacy_history_agendas = node_direct_agendas
            else:
                legacy_history_agendas = [
                    agenda
                    for agenda in eligible_agendas
                    if agenda.get("review_status", "confirmed") == "confirmed"
                    and agenda_matches_node(agenda, node)
                ]
            legacy_history, legacy_citations = _bootstrap_legacy_history(
                legacy_history_agendas, as_of_week
            )
            current_direct = [
                agenda
                for agenda in node_direct_agendas
                if _normalize_week(agenda.get("week")) == as_of_week
            ]
            direct_agendas_by_id = {
                str(agenda["agenda_id"]): agenda
                for agenda in node_direct_agendas
            }
            previous_agendas = [
                direct_agendas_by_id[agenda_id]
                for agenda_id in sorted(_previous_cited_agenda_ids(previous))
                if agenda_id in direct_agendas_by_id
            ]
            allowed_by_id = {
                str(agenda["agenda_id"]): agenda
                for agenda in [
                    *current_direct,
                    *previous_agendas,
                    *legacy_prompt_agendas,
                ]
            }
            allowed_agendas = [
                allowed_by_id[agenda_id] for agenda_id in sorted(allowed_by_id)
            ]
            child_validation_agendas = _child_agendas(
                child_digests, agendas_by_id
            )
            timeline_agendas = node_direct_agendas
            compact_timelines = _compact_issue_timelines(
                timeline_agendas, as_of_week
            )
            timeline_validation_ids = {
                str(event["agenda_id"])
                for timeline in compact_timelines
                for event in timeline["events"]
            }
            validation_by_id = {
                **allowed_by_id,
                **{
                    str(agenda["agenda_id"]): agenda
                    for agenda in child_validation_agendas
                },
                **{
                    agenda_id: agendas_by_id[agenda_id]
                    for agenda_id in timeline_validation_ids
                },
            }
            validation_agendas = [
                validation_by_id[agenda_id]
                for agenda_id in sorted(validation_by_id)
            ]
            available_reopened_evidence = _reopened_evidence_ids(
                timeline_agendas, child_digests
            )
            expected_issue_statuses = _expected_issue_statuses(
                compact_timelines, child_digests
            )
            deterministic_decisions = deterministic_issue_decisions(
                compact_timelines, child_digests
            )
            context = {
                "node": asdict(node),
                "as_of_week": as_of_week,
                "allowed_agendas": allowed_agendas,
                "issue_timelines": compact_timelines,
                "expected_issue_statuses": expected_issue_statuses,
                "previous_current_body_markdown": previous.get(
                    "current_body_markdown", ""
                ),
                "recent_history": _recent_history(previous),
                "child_digests": [item.model_dump() for item in child_digests],
            }

            def validate_analysis(
                candidate: PageAnalysis,
            ) -> dict[str, list[str]]:
                evidence = validate_stage1_evidence(candidate, validation_agendas)
                validate_issue_decisions(
                    candidate,
                    validation_agendas,
                    expected_issue_statuses,
                    available_reopened_evidence,
                )
                return evidence

            def generate_analysis(
                retry_context: dict[str, Any],
            ) -> PageAnalysis | None:
                candidate = analyze(retry_context)
                if candidate is None:
                    return None
                return candidate.model_copy(
                    update={"issue_decisions": deterministic_decisions}
                )

            analysis, evidence_by_mail = generate_with_semantic_retry(
                generate_analysis,
                validate_analysis,
                context,
            )
            validated_reopened_evidence = {
                decision.issue_id: set(decision.agenda_ids)
                & available_reopened_evidence.get(decision.issue_id, set())
                for decision in analysis.issue_decisions
                if decision.status == "reopened"
                and set(decision.agenda_ids)
                & available_reopened_evidence.get(decision.issue_id, set())
            }
            narrative, validated_citations = generate_with_semantic_retry(
                lambda retry_context: draft(retry_context, analysis),
                lambda candidate: validate_draft(candidate, evidence_by_mail),
                context,
            )
            source_docs_by_mail: dict[str, set[str]] = {}
            for agenda in eligible_agendas:
                source_docs_by_mail.setdefault(str(agenda["mail_id"]), set()).update(
                    str(source_id)
                    for source_id in agenda.get("source_doc_ids", [])
                )
            current_citation_map = [
                *(
                    citation.model_copy(
                        update={
                            "source_doc_ids": sorted(
                                source_docs_by_mail.get(citation.mail_id, set())
                            )
                        }
                    )
                    for citation in validated_citations
                ),
                *legacy_citations,
            ]
            current_body = render_current_body(narrative)
            previous_history = legacy_history or [
                (
                    item
                    if isinstance(item, WeeklyHistoryEntry)
                    else WeeklyHistoryEntry.model_validate(item)
                )
                for item in previous.get("weekly_history", [])
            ]
            current_source_mail_ids = sorted(
                item.mail_id
                for item in current_citation_map
                if "주차별 업데이트 이력" in item.used_in_sections
            )
            current_history_citations = [
                item
                for item in current_citation_map
                if item.mail_id in current_source_mail_ids
            ]
            history = merge_weekly_history(
                previous_history,
                WeeklyHistoryEntry(
                    week=as_of_week,
                    body_markdown=narrative.weekly_update,
                    source_mail_ids=current_source_mail_ids,
                    agenda_ids=sorted(
                        {
                            agenda_id
                            for item in current_history_citations
                            for agenda_id in item.agenda_ids
                        }
                    ),
                    source_doc_ids=sorted(
                        {
                            source_id
                            for item in current_history_citations
                            for source_id in item.source_doc_ids
                        }
                    ),
                ),
            )
            previous_citations = []
            for item in previous.get("citation_map", []):
                citation = (
                    item
                    if isinstance(item, CitationMapEntry)
                    else CitationMapEntry.model_validate(item)
                )
                if not citation.source_doc_ids:
                    citation = citation.model_copy(
                        update={
                            "source_doc_ids": sorted(
                                source_docs_by_mail.get(citation.mail_id, set())
                            )
                        }
                    )
                previous_citations.append(citation)
            path = canonical_path(node)
            citation_map = [
                item.model_copy(update={"category_paths": [path]})
                for item in _merge_citation_maps(
                    current_citation_map,
                    previous_citations,
                    history,
                )
            ]
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
                "child_page_ids": [canonical_path(child) for child in children],
                "confidence": narrative.confidence,
                "open_issue_count": len(base["open_issue_ids"]),
                "resolved_issue_count": len(base["resolved_issue_ids"]),
                "contradictions": list(analysis.contradictions),
                "generation_review_items": list(analysis.review_items),
                "updated_at": base["generated_at"],
            }
            result.pages.append(page)
            digests[node.id] = build_child_digest(
                page,
                analysis,
                narrative,
                validated_reopened_evidence,
            )
        except Exception as exc:
            result.failures[node.id] = str(exc)

    return result


def merge_weekly_history(
    previous: list[WeeklyHistoryEntry], current: WeeklyHistoryEntry
) -> list[WeeklyHistoryEntry]:
    by_week = {_normalize_week(item.week): item for item in previous}
    by_week[_normalize_week(current.week)] = current
    return [by_week[week] for week in sorted(by_week, reverse=True)]


def render_current_body(document: MergedWikiDocument | NarrativeDraft) -> str:
    if isinstance(document, MergedWikiDocument):
        return document.current_body_markdown
    draft = document
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
                    "agenda_ids": {"type": "keyword"},
                    "source_doc_ids": {"type": "keyword"},
                },
            },
            "citation_map": {
                "type": "nested",
                "properties": {
                    "mail_id": {"type": "keyword"},
                    "agenda_ids": {"type": "keyword"},
                    "source_doc_ids": {"type": "keyword"},
                    "used_in_sections": {"type": "keyword"},
                    "category_paths": {"type": "keyword"},
                },
            },
            "child_page_ids": {"type": "keyword"},
            "confidence": {"type": "keyword"},
            "open_issue_count": {"type": "integer"},
            "resolved_issue_count": {"type": "integer"},
            "issue_ids": {"type": "keyword"},
            "schema_version": {"type": "integer"},
            "generation_strategy": {"type": "keyword"},
            "contradictions": {"type": "text", "analyzer": "category_korean"},
            "generation_review_items": {
                "type": "text",
                "analyzer": "category_korean",
            },
            "updated_at": {"type": "date"},
        }
    )
    return definition


def fetch_previous_pages(
    client: OpenSearch, *, page_size: int = 500
) -> dict[str, dict[str, Any]]:
    search_after = None
    pages: dict[str, dict[str, Any]] = {}
    while True:
        body: dict[str, Any] = {
            "size": page_size,
            "query": {"term": {"page_kind": "latest"}},
            "sort": [{"_id": "asc"}],
        }
        if search_after is not None:
            body["search_after"] = search_after
        response = client.search(index=PAGE_INDEX, body=body)
        batch = response.get("hits", {}).get("hits", [])
        for hit in batch:
            page = hit["_source"]
            pages[str(page["category_id"])] = page
        if len(batch) < page_size:
            break
        search_after = batch[-1]["sort"]
    return pages


def validate_source_documents(
    client: OpenSearch, pages: list[dict[str, Any]]
) -> None:
    source_doc_ids = sorted(
        {
            str(source_doc_id)
            for page in pages
            for source_doc_id in page.get("source_doc_ids", [])
        }
    )
    if not source_doc_ids:
        return
    response = client.mget(index=SOURCE_INDEX, body={"ids": source_doc_ids})
    missing = sorted(
        str(document["_id"])
        for document in response.get("docs", [])
        if not document.get("found", False)
    )
    if missing:
        raise NarrativeValidationError(
            f"missing weekly_mail source documents: {', '.join(missing)}"
        )


def save_integrated_page(
    client: OpenSearch,
    page: dict[str, Any],
) -> None:
    validate_source_documents(client, [page])
    canonical = {**page, "page_kind": "latest", "doc_type": "canonical"}
    snapshot = {**page, "page_kind": "snapshot", "doc_type": "snapshot"}
    helpers.bulk(
        client,
        [
            {
                "_index": PAGE_INDEX,
                "_id": page["category_id"],
                "_source": canonical,
            },
            {
                "_index": PAGE_INDEX,
                "_id": page["category_id"] + ":" + page["as_of_week"],
                "_source": snapshot,
            },
        ],
    )
    client.indices.refresh(index=PAGE_INDEX)


def save_integrated_pages(
    client: OpenSearch, pages: list[dict[str, Any]]
) -> int:
    for page in pages:
        save_integrated_page(client, page)
    return len(pages)


def run(
    *,
    weeks: list[str] | None = None,
    taxonomy_path: Path | None = None,
    allow_external_llm: bool = False,
    allow_dummy_taxonomy: bool = False,
    dry_run: bool = False,
    client: OpenSearch | None = None,
    merge: MergeFn | None = None,
    issue_suggester: IssueSuggestionFn | None = None,
    rebuild_all: bool = False,
) -> dict[str, int]:
    taxonomy = load_taxonomy(taxonomy_path)
    if taxonomy.is_dummy and not allow_dummy_taxonomy:
        raise RuntimeError(
            "Dummy taxonomy is active. Import real mapping or explicitly allow it."
        )
    if merge is None and not allow_external_llm:
        raise RuntimeError("External LLM use requires explicit allow_external_llm=True")
    if client is None:
        from embed_vectordb import get_opensearch_client

        client = get_opensearch_client()

    agendas = (
        fetch_agendas(client)
        if client.indices.exists(index=AGENDA_INDEX)
        else []
    )
    available_weeks = sorted(
        {
            _normalize_week(agenda["week"])
            for agenda in agendas
            if agenda.get("week") and agenda.get("week") != "unknown"
        }
    )
    requested_weeks = sorted(
        _normalize_week(week) for week in (weeks or available_weeks)
    )
    missing_weeks = (
        sorted(set(requested_weeks) - set(available_weeks)) if weeks else []
    )
    if missing_weeks:
        raise RuntimeError(
            f"requested weeks are absent from mail_agendas: {', '.join(missing_weeks)}"
        )
    if not requested_weeks:
        return {
            "affected_pages": 0,
            "saved_pages": 0,
            "skipped_pages": 0,
            "failed": 0,
            "pending": 0,
        }
    as_of_week = requested_weeks[-1]
    eligible_agendas = [
        agenda
        for agenda in agendas
        if not agenda.get("week")
        or _normalize_week(agenda["week"]) <= as_of_week
    ]
    delta = select_weekly_delta(eligible_agendas, as_of_week)
    selected_ids = affected_node_ids(
        taxonomy,
        delta,
        rebuild_all=rebuild_all,
    )

    if not dry_run:
        ensure_index(client, PAGE_INDEX, integrated_page_index_definition())
        ensure_index(client, ISSUE_INDEX, issue_index_definition())

    existing_issues = (
        fetch_issue_ledger(client)
        if client.indices.exists(index=ISSUE_INDEX)
        else {}
    )
    issue_result = resolve_issue_ledger(
        eligible_agendas if rebuild_all else delta,
        existing_issues,
        as_of_week=as_of_week,
        suggest=issue_suggester,
    )
    if not dry_run:
        save_issue_ledger(client, issue_result.issues)

    if merge is None:
        merge = build_page_merger()

    previous_pages = (
        fetch_previous_pages(client)
        if client.indices.exists(index=PAGE_INDEX)
        else {}
    )
    def persist_page(page: dict[str, Any]) -> None:
        save_integrated_page(client, page)
        print(f"[wiki] saved {page['category_id']}")

    result = build_incremental_pages(
        taxonomy,
        eligible_agendas,
        previous_pages,
        issue_result,
        as_of_week=as_of_week,
        merge=merge,
        rebuild_all=rebuild_all,
        on_page=None if dry_run else persist_page,
        selected_node_ids=selected_ids,
    )
    return {
        "affected_pages": len(selected_ids),
        "saved_pages": len(result.pages),
        "skipped_pages": len(result.skipped),
        "failed": len(result.failures),
        "pending": len(result.pending),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build integrated narrative Category Wiki pages"
    )
    parser.add_argument("--week", action="append", dest="weeks")
    parser.add_argument("--taxonomy", type=Path)
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--allow-dummy-taxonomy", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rebuild-all",
        action="store_true",
        help="Ignore current fixed-section bodies and rebuild every taxonomy page",
    )
    args = parser.parse_args()
    try:
        stats = run(
            weeks=args.weeks,
            taxonomy_path=args.taxonomy,
            allow_external_llm=args.allow_external_llm,
            allow_dummy_taxonomy=args.allow_dummy_taxonomy,
            dry_run=args.dry_run,
            rebuild_all=args.rebuild_all,
        )
    except (RuntimeError, NarrativeValidationError) as exc:
        print(str(exc))
        return 2
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
