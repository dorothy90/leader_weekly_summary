from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from knowledge_models import (
    ArchivedApprovedEvidence,
    ArchivedApprovedWeek,
    CategoryPath,
    ClaimChange,
    ClassificationItem,
    KnowledgeArea,
    RelationKind,
    StrictModel,
    SupportedClaim,
    TopicAssignment,
    TopicKind,
    TopicRelation,
    TopicRelationChange,
    TopicRevision,
    TopicSection,
    TopicState,
    WikiTopic,
    WikiBuildRun,
    WikiReview,
)
from topic_linker import DecisionFn, link_agenda, persist_link_proposal
from wiki_store import JsonWikiStore


TERMINAL_STATE_HINTS = {
    "resolved",
    "closed",
    "completed",
    "stable",
    "positive",
    "normal",
}
NONTERMINAL_STATE_HINTS = {
    "open", "investigating", "action_in_progress", "monitoring", "ongoing",
    "active", "reopened",
}
APPROVED_EVIDENCE_STATUSES = {"confirmed", "manually_corrected", "aggregate"}
CITATION_PATTERN = re.compile(r"\[agenda:([^\]\s]+)\]")
CLAIM_PUNCTUATION = ".!?;。！？；"


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

TOPIC_PROMPT_VERSION = "topic-wiki-v1"
WIKI_BUILDER_VERSION = "json-wiki-v2-projections"


def llm_connection() -> Any:
    from agenda_extract import llm_connection as connection_factory

    return connection_factory()


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


def build_input_hash(
    document: Any,
    *,
    taxonomy_version: int,
    assignment_digest: str,
    prompt_version: str,
    builder_version: str,
    model: str,
) -> str:
    payload = {
        "active_run_id": document.active_run_id,
        "items": [
            document.items[key].model_dump(mode="json")
            for key in sorted(document.items)
        ],
        "taxonomy_version": taxonomy_version,
        "assignment_digest": assignment_digest,
        "prompt_version": prompt_version,
        "builder_version": builder_version,
        "model": model,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _new_topic(
    topic_id: str,
    week: str,
    items: Sequence[ClassificationItem],
) -> WikiTopic:
    first = items[0]
    target_paths = sorted(
        {
            (
                item.decision.target_path.domain,
                item.decision.target_path.tech,
                item.decision.target_path.lotcd,
            )
            for item in items
            if item.decision.target_path is not None
        },
        key=lambda value: tuple(part or "" for part in value),
    )
    return WikiTopic(
        topic_id=topic_id,
        title=first.topic_hint or first.summary,
        topic_kind="knowledge",
        primary_area="other",
        state="new",
        importance="medium",
        first_seen_week=week,
        last_updated_week=week,
        target_paths=[
            CategoryPath(domain=domain, tech=tech, lotcd=lotcd)
            for domain, tech, lotcd in target_paths
        ],
        teams=sorted({item.team for item in items}),
        source_agenda_ids=[],
        current_revision_id="pending",
    )


def _group_assigned_items(
    items: Sequence[ClassificationItem],
    store: JsonWikiStore,
) -> dict[str, list[ClassificationItem]]:
    grouped: dict[str, list[ClassificationItem]] = {}
    for item in sorted(items, key=lambda value: value.agenda_id):
        assignment = store.assignment(item.agenda_id)
        if assignment is not None:
            grouped.setdefault(assignment.topic_id, []).append(item)
    return dict(sorted(grouped.items()))


def _pending_relation_review(relation: TopicRelation) -> WikiReview:
    return WikiReview(
        review_id=f"R-{relation.relation_id}",
        kind="relation",
        relation_id=relation.relation_id,
        relation_kind=relation.kind,
        relation_agenda_ids=relation.agenda_ids,
        rationale=(
            f"{relation.source_topic_id} -> {relation.target_topic_id} "
            f"{relation.kind} relation proposed from Agenda evidence."
        ),
    )


def build_week(
    week: str,
    classification_store: Any,
    wiki_store: JsonWikiStore,
    link_decider: DecisionFn,
    analysis_fn: AnalysisFn,
    draft_fn: DraftFn,
    projection_analysis_fn: Any | None = None,
    projection_draft_fn: Any | None = None,
) -> WikiBuildRun:
    from wiki_projections import build_week_view

    document = classification_store.approved_week(week)
    active_run = document.runs[document.active_run_id]
    taxonomy_version = active_run.taxonomy_version
    model = llm_connection().model
    with wiki_store.build_lock():
        eligible = sorted(
            (
                item
                for item in document.items.values()
                if item.decision.status in APPROVED_EVIDENCE_STATUSES
                and item.decision.target_path is not None
            ),
            key=lambda item: item.agenda_id,
        )
        archived_at = datetime.now(UTC)
        current_refs: dict[str, str] = {}
        for item in eligible:
            evidence_ref = f"{week}/{document.active_run_id}/{item.agenda_id}"
            current_refs[item.agenda_id] = evidence_ref
            wiki_store.archive_evidence(ArchivedApprovedEvidence(
                evidence_ref=evidence_ref,
                week=week,
                classification_run_id=document.active_run_id,
                item=item,
                archived_at=archived_at,
            ))
        wiki_store.archive_approved_week(ArchivedApprovedWeek(
            week=week,
            classification_run_id=document.active_run_id,
            taxonomy_version=taxonomy_version,
            approved_at=document.approved_at,
            approved_by=document.approved_by,
            runs=document.runs,
            items=document.items,
            revisions=document.revisions,
            evidence_refs=sorted(current_refs.values()),
            archived_at=archived_at,
        ))
        for item in eligible:
            if wiki_store.assignment(item.agenda_id) is not None:
                continue
            proposal = link_agenda(item, wiki_store.topics(), link_decider)
            persist_link_proposal(wiki_store, item, proposal)
        input_hash = build_input_hash(
            document,
            taxonomy_version=taxonomy_version,
            assignment_digest=wiki_store.assignment_digest(),
            prompt_version=TOPIC_PROMPT_VERSION,
            builder_version=WIKI_BUILDER_VERSION,
            model=model,
        )
        prior = wiki_store.successful_build(input_hash)
        if prior is not None:
            return prior
        run = wiki_store.start_build(
            week,
            document.active_run_id,
            input_hash,
            model,
            taxonomy_version=taxonomy_version,
        )
        eligible_ids = {item.agenda_id for item in eligible}
        if any(
            review.kind == "assignment" and review.agenda_id in eligible_ids
            for review in wiki_store.reviews("pending")
        ):
            return wiki_store.finish_build(run, status="review_required")

        grouped = _group_assigned_items(eligible, wiki_store)
        run = run.model_copy(
            update={"status": "generating", "affected_topic_ids": list(grouped)}
        )
        wiki_store.save_build(run)
        existing_topics = {value.topic_id: value for value in wiki_store.topics()}
        known_topic_ids = {*existing_topics, *grouped}
        failed: list[str] = []
        for topic_id, items in grouped.items():
            try:
                current = existing_topics.get(topic_id) or _new_topic(
                    topic_id, week, items
                )
                previous_revision = (
                    wiki_store.topic_revision(
                        topic_id, current.current_revision_id
                    )
                    if topic_id in existing_topics
                    else None
                )
                archived_items: dict[str, ClassificationItem] = {}
                archived_refs: dict[str, str] = {}
                if previous_revision is not None:
                    for evidence_ref in previous_revision.evidence_refs:
                        archived = wiki_store.archived_evidence(evidence_ref)
                        archived_items[archived.item.agenda_id] = archived.item
                        archived_refs[archived.item.agenda_id] = evidence_ref
                for item in items:
                    archived_items[item.agenda_id] = item
                    archived_refs[item.agenda_id] = current_refs[item.agenda_id]
                accumulated_items = [archived_items[key] for key in sorted(archived_items)]
                assignments = [
                    assignment
                    for item in accumulated_items
                    if (assignment := wiki_store.assignment(item.agenda_id))
                    is not None
                ]
                updated, revision, relations = build_topic_revision(
                    current,
                    accumulated_items,
                    assignments,
                    analysis_fn,
                    draft_fn,
                    previous_revision=previous_revision,
                    week=week,
                    model=model,
                    existing_topic_ids=known_topic_ids,
                    evidence_refs=archived_refs,
                    build_run_id=run.run_id,
                )
                relation_changes: list[TopicRelationChange] = []
                previous_relation_actions = {
                    change.relation_id: change.action
                    for change in (previous_revision.relation_changes if previous_revision else [])
                }
                for relation in relations:
                    try:
                        stored = wiki_store.relation(relation.relation_id)
                        action = (
                            stored.review_state
                            if stored.review_state in {"accepted", "rejected"}
                            else "proposed"
                        )
                    except KeyError:
                        action = "proposed"
                    if previous_relation_actions.get(relation.relation_id) == action:
                        continue
                    relation_changes.append(TopicRelationChange(
                        relation_id=relation.relation_id,
                        action=action,
                    ))
                revision = revision.model_copy(update={
                    "relation_changes": relation_changes
                })
                wiki_store.publish_topic(updated, revision)
                for relation in relations:
                    try:
                        stored_relation = wiki_store.relation(relation.relation_id)
                    except KeyError:
                        wiki_store.save_relation(relation)
                        wiki_store.save_review(_pending_relation_review(relation))
                        continue
                    review_id = f"R-{stored_relation.relation_id}"
                    if (
                        stored_relation.review_state == "pending"
                        and not any(
                            review.review_id == review_id
                            for review in wiki_store.reviews()
                        )
                    ):
                        wiki_store.save_review(
                            _pending_relation_review(stored_relation)
                        )
            except Exception:
                failed.append(topic_id)
        wiki_store.rebuild_catalog()
        failed_projections: list[str] = []
        if (projection_analysis_fn is None) != (projection_draft_fn is None):
            raise ValueError("Projection synthesis requires analysis and draft functions")
        if projection_analysis_fn is not None and projection_draft_fn is not None:
            from projection_wiki_builder import refresh_projection_documents

            failed_projections = refresh_projection_documents(
                wiki_store,
                week,
                run.run_id,
                projection_analysis_fn,
                projection_draft_fn,
                model=model,
            )
        wiki_store.save_week(build_week_view(wiki_store, week, run.run_id))
        status = "partially_failed" if failed or failed_projections else "published"
        return wiki_store.finish_build(
            run,
            status=status,
            failed_topic_ids=failed,
        )


def _split_after_citations(value: str) -> list[str]:
    chunks: list[str] = []
    chunk_start = 0
    for citation in CITATION_PATTERN.finditer(value):
        next_claim_start = citation.end()
        while (
            next_claim_start < len(value)
            and value[next_claim_start].isspace()
        ):
            next_claim_start += 1
        if (
            next_claim_start == len(value)
            or CITATION_PATTERN.match(value, next_claim_start)
            or value[next_claim_start] in CLAIM_PUNCTUATION
        ):
            continue
        chunks.append(value[chunk_start:citation.end()].strip())
        chunk_start = next_claim_start
    chunks.append(value[chunk_start:].strip())
    return [chunk for chunk in chunks if chunk]


def _factual_chunks(section: TopicSection) -> list[str]:
    if section.key == "open_questions":
        return []
    chunks: list[str] = []
    for line in section.body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        punctuation_chunks = re.split(
            rf"(?<=[{CLAIM_PUNCTUATION}])\s*(?=(?!\[agenda:)\S)",
            stripped,
        )
        for value in punctuation_chunks:
            chunks.extend(
                chunk
                for chunk in _split_after_citations(value)
                if not chunk.rstrip().endswith(("?", "？"))
            )
    return chunks


def _normalize_claim_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", CITATION_PATTERN.sub("", value))
    normalized = " ".join(normalized.casefold().split())
    while normalized and unicodedata.category(normalized[0])[0] in {"P", "S"}:
        normalized = normalized[1:].lstrip()
    while normalized and unicodedata.category(normalized[-1])[0] in {"P", "S"}:
        normalized = normalized[:-1].rstrip()
    return normalized


def _claim_identity(claim: SupportedClaim) -> tuple[str, tuple[str, ...]]:
    return _normalize_claim_text(claim.text), tuple(claim.agenda_ids)


def _draft_claim_identities(draft: TopicDraft) -> set[tuple[str, tuple[str, ...]]]:
    return {
        (
            _normalize_claim_text(chunk),
            tuple(CITATION_PATTERN.findall(chunk)),
        )
        for section in draft.sections
        for chunk in _factual_chunks(section)
        if CITATION_PATTERN.search(chunk)
    }


def _render_retained_claim(claim: SupportedClaim) -> str:
    citations = " ".join(f"[agenda:{agenda_id}]" for agenda_id in claim.agenda_ids)
    return f"{claim.text.strip()} {citations}"


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
    build_run_id: str = "",
) -> str:
    payload = {
        "topic_id": topic_id,
        "week": week,
        "body_markdown": body_markdown,
        "claims": [claim.model_dump(mode="json") for claim in claims],
        "model": model,
        "build_run_id": build_run_id,
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
    build_run_id: str = "",
    creation_week: str = "",
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
                creation_source="llm",
                created_by="topic-wiki-builder",
                created_at=datetime.now(UTC),
                created_build_run_id=build_run_id,
                creation_week=creation_week or topic.last_updated_week,
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
    evidence_refs: Mapping[str, str] | None = None,
    build_run_id: str = "",
) -> tuple[WikiTopic, TopicRevision, list[TopicRelation]]:
    evidence = {item.agenda_id: item for item in items}
    if len(evidence) != len(items):
        raise ValueError("duplicate Agenda ID")
    for item in items:
        if item.decision.status not in APPROVED_EVIDENCE_STATUSES:
            raise ValueError(f"unapproved Agenda ID: {item.agenda_id}")
    _validate_assignments(topic, items, assignments)
    proposed_paths = sorted(
        {
            *( (path.domain, path.tech, path.lotcd) for path in topic.target_paths ),
            *( (item.decision.target_path.domain, item.decision.target_path.tech,
                item.decision.target_path.lotcd) for item in items
               if item.decision.target_path is not None ),
        },
        key=lambda value: tuple(part or "" for part in value),
    )
    topic_for_build = topic.model_copy(update={
        "target_paths": [CategoryPath(domain=d, tech=t, lotcd=l) for d, t, l in proposed_paths]
    })
    context = _build_context(topic_for_build, items, previous_revision)
    analysis = TopicAnalysis.model_validate(
        analysis_source(context) if callable(analysis_source) else analysis_source
    )
    retained_previous_claims: list[SupportedClaim] = []
    if previous_revision is not None:
        stale = {value.strip() for value in analysis.stale_claims}
        claims_by_text = {
            claim.text: claim
            for claim in previous_revision.claims
            if claim.text not in stale
            and set(claim.agenda_ids) <= set(evidence)
        }
        claims_by_text.update({claim.text: claim for claim in analysis.claims})
        retained_previous_claims = [
            claim for claim in previous_revision.claims
            if claim.text not in stale
            and set(claim.agenda_ids) <= set(evidence)
            and claims_by_text.get(claim.text) == claim
        ]
        analysis = analysis.model_copy(update={
            "claims": [claims_by_text[key] for key in sorted(claims_by_text)]
        })
    previous_ids = set(previous_revision.source_agenda_ids) if previous_revision else set()
    if previous_revision and previous_revision.evidence_refs:
        previous_ids.update(ref.rsplit("/", 1)[-1] for ref in previous_revision.evidence_refs)
    computed_added_ids = sorted(set(evidence) - previous_ids)
    new_items = [evidence[agenda_id] for agenda_id in computed_added_ids]
    if (
        analysis.next_state in {"resolved", "closed"}
        and topic.state != analysis.next_state
        and not any(
            item.state_hint.strip().casefold() in TERMINAL_STATE_HINTS
            for item in new_items
        )
    ):
        raise ValueError(f"{analysis.next_state} transition requires new terminal evidence")
    if analysis.next_state == "reopened":
        if topic.state not in {"resolved", "closed"}:
            raise ValueError("reopened transition requires a terminal previous state")

        def evidence_order(item: ClassificationItem) -> tuple[str, str]:
            evidence_ref = (evidence_refs or {}).get(item.agenda_id, "")
            evidence_week = evidence_ref.split("/", 1)[0] if evidence_ref else ""
            received = item.received_at.isoformat() if item.received_at else ""
            return evidence_week, received

        prior_orders = [
            evidence_order(item) for item in items if item.agenda_id in previous_ids
        ]
        newest_prior = max(prior_orders, default=("", ""))
        valid_reopen = any(
            item.state_hint.strip().casefold() in NONTERMINAL_STATE_HINTS
            and evidence_order(item) > newest_prior
            for item in new_items
        )
        if not valid_reopen:
            raise ValueError("reopened transition requires newer nonterminal evidence")
    draft = TopicDraft.model_validate(
        draft_source(context, analysis) if callable(draft_source) else draft_source
    )
    if previous_revision is not None:
        next_sections = list(draft.sections)
        rendered_identities = _draft_claim_identities(draft)
        missing_claims = [
            claim for claim in retained_previous_claims
            if _claim_identity(claim) not in rendered_identities
        ]
        if missing_claims:
            retained_body = "\n".join(
                _render_retained_claim(claim) for claim in missing_claims
            )
            observations_index = next(
                (
                    index for index, section in enumerate(next_sections)
                    if section.key == "observations"
                ),
                None,
            )
            if observations_index is None:
                next_sections.append(TopicSection(
                    key="observations",
                    title="이전 리비전에서 유지된 주장",
                    body=retained_body,
                ))
            else:
                current = next_sections[observations_index]
                next_sections[observations_index] = current.model_copy(update={
                    "body": f"{current.body}\n\n{retained_body}".strip()
                })
        draft = draft.model_copy(update={"sections": next_sections})
        rendered_identities = _draft_claim_identities(draft)
        for claim in retained_previous_claims:
            if _claim_identity(claim) not in rendered_identities:
                raise ValueError(f"retained claim missing citation prose: {claim.text}")
    cited_ids = validate_topic_draft(draft, evidence)
    _validate_evidence_ids(sorted(cited_ids), evidence, topic_for_build)
    for claim in analysis.claims:
        _validate_evidence_ids(claim.agenda_ids, evidence, topic_for_build)

    revision_week = week or topic.last_updated_week
    relations = _build_relations(
        topic_for_build,
        analysis.relation_proposals,
        evidence,
        existing_topic_ids,
        build_run_id,
        revision_week,
    )
    model_name = _model_name(analysis_source, draft_source, model)
    body_markdown = _render_markdown(draft.sections)
    revision_id = _revision_id(
        topic.topic_id,
        revision_week,
        body_markdown,
        analysis.claims,
        model_name,
        build_run_id,
    )
    source_agenda_ids = sorted(
        cited_ids
        | {agenda_id for claim in analysis.claims for agenda_id in claim.agenda_ids}
        | {
            agenda_id
            for proposal in analysis.relation_proposals
            for agenda_id in proposal.agenda_ids
        }
    )
    previous_claims = previous_revision.claims if previous_revision else []
    previous_by_text = {claim.text: claim for claim in previous_claims}
    next_by_text = {claim.text: claim for claim in analysis.claims}
    changed_claims = [
        ClaimChange(before=previous_by_text[text], after=next_by_text[text])
        for text in sorted(previous_by_text.keys() & next_by_text.keys())
        if previous_by_text[text].agenda_ids != next_by_text[text].agenda_ids
    ]
    revision = TopicRevision(
        revision_id=revision_id,
        topic_id=topic.topic_id,
        week=revision_week,
        body_markdown=body_markdown,
        sections=draft.sections,
        claims=analysis.claims,
        source_agenda_ids=source_agenda_ids,
        evidence_refs=sorted(
            (evidence_refs or {}).get(agenda_id, "")
            for agenda_id in sorted(evidence)
            if (evidence_refs or {}).get(agenda_id)
        ),
        previous_state=topic.state,
        new_state=analysis.next_state,
        added_agenda_ids=computed_added_ids,
        added_claims=[next_by_text[key] for key in sorted(next_by_text.keys() - previous_by_text.keys())],
        removed_claims=[previous_by_text[key] for key in sorted(previous_by_text.keys() - next_by_text.keys())],
        changed_claims=changed_claims,
        relation_changes=[
            TopicRelationChange(relation_id=relation.relation_id, action="proposed")
            for relation in sorted(relations, key=lambda value: value.relation_id)
        ],
        build_run_id=build_run_id,
        prompt_version=TOPIC_PROMPT_VERSION,
        builder_version=WIKI_BUILDER_VERSION,
        summary=(draft.sections[0].body if draft.sections else analysis.title),
        validation_results=["approved-evidence:ok", "citations:ok", "taxonomy:ok"],
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
            "target_paths": topic_for_build.target_paths,
            "teams": sorted({*topic.teams, *(item.team for item in items)}),
            "source_agenda_ids": sorted(
                {*topic.source_agenda_ids, *evidence}
            ),
            "current_revision_id": revision_id,
        }
    )
    return updated, revision, relations
