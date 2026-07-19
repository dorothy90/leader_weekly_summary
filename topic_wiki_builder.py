from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from knowledge_models import (
    CategoryPath,
    ClassificationItem,
    KnowledgeArea,
    RelationKind,
    StrictModel,
    SupportedClaim,
    TopicAssignment,
    TopicKind,
    TopicRelation,
    TopicRevision,
    TopicSection,
    TopicState,
    WikiTopic,
)


TERMINAL_STATE_HINTS = {
    "resolved",
    "closed",
    "completed",
    "stable",
    "positive",
    "normal",
}
APPROVED_EVIDENCE_STATUSES = {"confirmed", "manually_corrected", "aggregate"}
CITATION_PATTERN = re.compile(r"\[agenda:([^\]\s]+)\]")


class RelationProposal(StrictModel):
    target_topic_id: str = Field(min_length=1)
    kind: RelationKind
    agenda_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class TopicAnalysis(StrictModel):
    title: str
    topic_kind: TopicKind
    primary_area: KnowledgeArea
    secondary_areas: list[KnowledgeArea]
    next_state: TopicState
    importance: Literal["low", "medium", "high", "critical"]
    claims: list[SupportedClaim]
    stale_claims: list[str]
    relation_proposals: list[RelationProposal]
    open_questions: list[str]


class TopicDraft(StrictModel):
    sections: list[TopicSection]


AnalysisFn = Callable[[dict[str, Any]], TopicAnalysis]
DraftFn = Callable[[dict[str, Any], TopicAnalysis], TopicDraft]
WikiLlm = tuple[Any, str]


ANALYSIS_SYSTEM_PROMPT = """Analyze one semiconductor Topic using only the supplied approved Agenda evidence and previous revision. Return supported claims with Agenda IDs, a conservative state transition, and typed relation proposals. Never infer facts outside the supplied evidence."""

DRAFT_SYSTEM_PROMPT = """Write the current Korean Topic narrative from the validated analysis and approved evidence. Return ordered structured sections only. Every factual sentence must cite its evidence as [agenda:<agenda_id>]. Do not return a complete Markdown document or invent facts."""


def build_wiki_llm() -> WikiLlm:
    from langchain_openai import ChatOpenAI

    from agenda_extract import llm_connection

    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
    )
    return llm, connection.model


def build_analysis_fn(wiki_llm: WikiLlm | None = None) -> AnalysisFn:
    llm, model = wiki_llm or build_wiki_llm()
    structured = llm.with_structured_output(TopicAnalysis)

    def analyze(context: dict[str, Any]) -> TopicAnalysis:
        response = structured.invoke(
            [
                ("system", ANALYSIS_SYSTEM_PROMPT),
                ("human", json.dumps(context, ensure_ascii=False)),
            ]
        )
        return TopicAnalysis.model_validate(response)

    analyze.model = model  # type: ignore[attr-defined]
    return analyze


def build_draft_fn(wiki_llm: WikiLlm | None = None) -> DraftFn:
    llm, model = wiki_llm or build_wiki_llm()
    structured = llm.with_structured_output(TopicDraft)

    def draft(context: dict[str, Any], analysis: TopicAnalysis) -> TopicDraft:
        payload = {
            "context": context,
            "analysis": analysis.model_dump(mode="json"),
        }
        response = structured.invoke(
            [
                ("system", DRAFT_SYSTEM_PROMPT),
                ("human", json.dumps(payload, ensure_ascii=False)),
            ]
        )
        return TopicDraft.model_validate(response)

    draft.model = model  # type: ignore[attr-defined]
    return draft


def _factual_chunks(section: TopicSection) -> list[str]:
    if section.key == "open_questions":
        return []
    chunks: list[str] = []
    for line in section.body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        chunks.extend(
            value.strip()
            for value in re.split(
                r"(?<=[.!?;。！？；])\s*(?=(?!\[agenda:)\S)",
                stripped,
            )
            if value.strip() and not value.rstrip().endswith(("?", "？"))
        )
    return chunks


def validate_topic_draft(
    draft: TopicDraft,
    evidence: Mapping[str, ClassificationItem],
) -> set[str]:
    cited_ids: set[str] = set()
    for section in draft.sections:
        section_citations = set(CITATION_PATTERN.findall(section.body))
        cited_ids.update(section_citations)
        for agenda_id in section_citations:
            if agenda_id not in evidence:
                raise ValueError(f"missing Agenda ID: {agenda_id}")
        for chunk in _factual_chunks(section):
            if not CITATION_PATTERN.search(chunk):
                raise ValueError(f"uncited factual claim: {chunk}")
    return cited_ids


def _path_contains(parent: CategoryPath, child: CategoryPath) -> bool:
    return all(
        expected is None or expected == actual
        for expected, actual in (
            (parent.domain, child.domain),
            (parent.tech, child.tech),
            (parent.lotcd, child.lotcd),
        )
    )


def _taxonomy_compatible(
    evidence_path: CategoryPath | None,
    topic_paths: Sequence[CategoryPath],
) -> bool:
    if evidence_path is None:
        return False
    return any(
        _path_contains(evidence_path, topic_path)
        or _path_contains(topic_path, evidence_path)
        for topic_path in topic_paths
    )


def _validate_evidence_ids(
    agenda_ids: Sequence[str],
    evidence: Mapping[str, ClassificationItem],
    topic: WikiTopic,
) -> None:
    for agenda_id in agenda_ids:
        item = evidence.get(agenda_id)
        if item is None:
            raise ValueError(f"missing Agenda ID: {agenda_id}")
        if not _taxonomy_compatible(item.decision.target_path, topic.target_paths):
            raise ValueError(f"incompatible taxonomy path for Agenda ID: {agenda_id}")


def _render_markdown(sections: Sequence[TopicSection]) -> str:
    return "\n\n".join(
        f"## {section.title}\n\n{section.body}" for section in sections
    )


def _build_context(
    topic: WikiTopic,
    evidence: Sequence[ClassificationItem],
    previous_revision: TopicRevision | None,
) -> dict[str, Any]:
    return {
        "topic": topic.model_dump(mode="json"),
        "previous_revision": (
            previous_revision.model_dump(mode="json")
            if previous_revision is not None
            else None
        ),
        "evidence": [item.model_dump(mode="json") for item in evidence],
    }


def _validate_assignments(
    topic: WikiTopic,
    evidence: Sequence[ClassificationItem],
    assignments: Sequence[TopicAssignment],
) -> None:
    by_agenda_id = {assignment.agenda_id: assignment for assignment in assignments}
    if len(by_agenda_id) != len(assignments):
        raise ValueError("duplicate Topic assignment")
    for item in evidence:
        assignment = by_agenda_id.get(item.agenda_id)
        if assignment is None:
            raise ValueError(f"missing accepted assignment: {item.agenda_id}")
        if assignment.topic_id != topic.topic_id:
            raise ValueError(
                f"assignment targets another Topic: {item.agenda_id}"
            )


def _model_name(
    analysis_source: TopicAnalysis | AnalysisFn,
    draft_source: TopicDraft | DraftFn,
    explicit_model: str | None,
) -> str:
    if explicit_model:
        return explicit_model
    candidates = {
        value
        for value in (
            getattr(analysis_source, "model", None),
            getattr(draft_source, "model", None),
        )
        if value
    }
    if len(candidates) != 1:
        raise ValueError("Topic revision requires one recorded connection model")
    return candidates.pop()


def _revision_id(
    topic_id: str,
    week: str,
    body_markdown: str,
    claims: Sequence[SupportedClaim],
    model: str,
) -> str:
    payload = {
        "topic_id": topic_id,
        "week": week,
        "body_markdown": body_markdown,
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "model": model,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16].upper()
    return f"REV-{digest}"


def _build_relations(
    topic: WikiTopic,
    proposals: Sequence[RelationProposal],
    evidence: Mapping[str, ClassificationItem],
    existing_topic_ids: set[str] | None,
) -> list[TopicRelation]:
    if proposals and existing_topic_ids is None:
        raise ValueError("existing Topic IDs are required for relation proposals")
    relations: list[TopicRelation] = []
    for proposal in proposals:
        if proposal.target_topic_id == topic.topic_id:
            raise ValueError("Topic relation cannot target itself")
        if (
            existing_topic_ids is not None
            and proposal.target_topic_id not in existing_topic_ids
        ):
            raise ValueError(f"missing related Topic ID: {proposal.target_topic_id}")
        _validate_evidence_ids(proposal.agenda_ids, evidence, topic)
        identity = "|".join(
            (
                topic.topic_id,
                proposal.target_topic_id,
                proposal.kind,
                *sorted(proposal.agenda_ids),
            )
        )
        relation_id = "REL-" + hashlib.sha256(identity.encode()).hexdigest()[:16].upper()
        relations.append(
            TopicRelation(
                relation_id=relation_id,
                source_topic_id=topic.topic_id,
                target_topic_id=proposal.target_topic_id,
                kind=proposal.kind,
                agenda_ids=sorted(set(proposal.agenda_ids)),
                confidence=proposal.confidence,
                review_state="pending",
            )
        )
    return relations


def build_topic_revision(
    topic: WikiTopic,
    items: Sequence[ClassificationItem],
    assignments: Sequence[TopicAssignment],
    analysis_source: TopicAnalysis | AnalysisFn,
    draft_source: TopicDraft | DraftFn,
    *,
    previous_revision: TopicRevision | None = None,
    week: str | None = None,
    model: str | None = None,
    existing_topic_ids: set[str] | None = None,
) -> tuple[WikiTopic, TopicRevision, list[TopicRelation]]:
    evidence = {item.agenda_id: item for item in items}
    if len(evidence) != len(items):
        raise ValueError("duplicate Agenda ID")
    for item in items:
        if item.decision.status not in APPROVED_EVIDENCE_STATUSES:
            raise ValueError(f"unapproved Agenda ID: {item.agenda_id}")
    _validate_assignments(topic, items, assignments)
    context = _build_context(topic, items, previous_revision)
    analysis = TopicAnalysis.model_validate(
        analysis_source(context) if callable(analysis_source) else analysis_source
    )
    if (
        topic.state != "resolved"
        and analysis.next_state == "resolved"
        and not any(
            item.state_hint.strip().casefold() in TERMINAL_STATE_HINTS
            for item in items
        )
    ):
        raise ValueError("resolved transition requires terminal evidence")
    draft = TopicDraft.model_validate(
        draft_source(context, analysis) if callable(draft_source) else draft_source
    )
    cited_ids = validate_topic_draft(draft, evidence)
    _validate_evidence_ids(sorted(cited_ids), evidence, topic)
    for claim in analysis.claims:
        _validate_evidence_ids(claim.agenda_ids, evidence, topic)

    relations = _build_relations(
        topic,
        analysis.relation_proposals,
        evidence,
        existing_topic_ids,
    )
    model_name = _model_name(analysis_source, draft_source, model)
    revision_week = week or topic.last_updated_week
    body_markdown = _render_markdown(draft.sections)
    revision_id = _revision_id(
        topic.topic_id,
        revision_week,
        body_markdown,
        analysis.claims,
        model_name,
    )
    source_agenda_ids = sorted(
        cited_ids
        | {
            agenda_id
            for claim in analysis.claims
            for agenda_id in claim.agenda_ids
        }
        | {
            agenda_id
            for proposal in analysis.relation_proposals
            for agenda_id in proposal.agenda_ids
        }
    )
    revision = TopicRevision(
        revision_id=revision_id,
        topic_id=topic.topic_id,
        week=revision_week,
        body_markdown=body_markdown,
        sections=draft.sections,
        claims=analysis.claims,
        source_agenda_ids=source_agenda_ids,
        created_at=datetime.now(UTC),
        model=model_name,
    )
    updated = topic.model_copy(
        update={
            "title": analysis.title,
            "topic_kind": analysis.topic_kind,
            "primary_area": analysis.primary_area,
            "secondary_areas": analysis.secondary_areas,
            "state": analysis.next_state,
            "importance": analysis.importance,
            "last_updated_week": revision_week,
            "teams": sorted({*topic.teams, *(item.team for item in items)}),
            "source_agenda_ids": sorted(
                {*topic.source_agenda_ids, *source_agenda_ids}
            ),
            "current_revision_id": revision_id,
        }
    )
    return updated, revision, relations
