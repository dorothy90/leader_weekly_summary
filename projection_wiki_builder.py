from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import Field

from knowledge_models import (
    ClassificationItem,
    CategoryPath,
    ProjectionSection,
    ProjectionWeeklyHistory,
    StrictModel,
    SupportedClaim,
    WikiProjectionDocument,
    WikiProjectionSpec,
)
from topic_wiki_builder import CITATION_PATTERN, _factual_chunks
from wiki_store import JsonWikiStore


class ProjectionAnalysis(StrictModel):
    summary: str
    section_titles: list[str] = Field(default_factory=list)


class ProjectionDraft(StrictModel):
    summary: str
    sections: list[ProjectionSection]
    claims: list[SupportedClaim]
    weekly_update: str


ProjectionAnalysisFn = Callable[[dict[str, Any]], ProjectionAnalysis]
ProjectionDraftFn = Callable[[dict[str, Any], ProjectionAnalysis], ProjectionDraft]

PROJECTION_ANALYSIS_SYSTEM_PROMPT = """Analyze one cumulative Wiki projection using only the previous canonical document, validated Topic documents, child projection digests, and approved Agenda evidence. Plan a coherent integrated article, not a list of Topics or Agendas. Preserve established knowledge unless new evidence revises it. Every proposed factual claim must retain its Agenda IDs."""

PROJECTION_DRAFT_SYSTEM_PROMPT = """Rewrite the complete current Korean Wiki article from the validated projection analysis and allowed evidence. Integrate repeated facts, causes, actions, outcomes, pending issues, and accumulated knowledge into coherent prose. Do not concatenate Topic summaries. Every factual sentence must cite one or more sources as [agenda:<agenda_id>]. Return only structured current sections plus one current-week update; prior weekly history is appended by application code."""


def build_projection_analysis_fn(wiki_llm: tuple[Any, str]) -> ProjectionAnalysisFn:
    llm, model = wiki_llm
    structured = llm.with_structured_output(ProjectionAnalysis)

    def analyze(context: dict[str, Any]) -> ProjectionAnalysis:
        response = structured.invoke([
            ("system", PROJECTION_ANALYSIS_SYSTEM_PROMPT),
            ("human", json.dumps(context, ensure_ascii=False)),
        ])
        return ProjectionAnalysis.model_validate(response)

    analyze.model = model  # type: ignore[attr-defined]
    return analyze


def build_projection_draft_fn(wiki_llm: tuple[Any, str]) -> ProjectionDraftFn:
    llm, model = wiki_llm
    structured = llm.with_structured_output(ProjectionDraft)

    def draft(
        context: dict[str, Any],
        analysis: ProjectionAnalysis,
    ) -> ProjectionDraft:
        response = structured.invoke([
            ("system", PROJECTION_DRAFT_SYSTEM_PROMPT),
            ("human", json.dumps({
                "context": context,
                "analysis": analysis.model_dump(mode="json"),
            }, ensure_ascii=False)),
        ])
        return ProjectionDraft.model_validate(response)

    draft.model = model  # type: ignore[attr-defined]
    return draft


def validate_projection_draft(
    draft: ProjectionDraft,
    evidence: dict[str, ClassificationItem],
) -> set[str]:
    cited_ids: set[str] = set()
    sections = [
        *draft.sections,
        ProjectionSection(
            key="weekly_update",
            title="주차별 업데이트",
            body=draft.weekly_update,
        ),
    ]
    for section in sections:
        citations = set(CITATION_PATTERN.findall(section.body))
        cited_ids.update(citations)
        for agenda_id in citations:
            if agenda_id not in evidence:
                raise ValueError(f"missing Agenda ID: {agenda_id}")
        for chunk in _factual_chunks(section):
            if not CITATION_PATTERN.search(chunk):
                raise ValueError(f"uncited factual claim: {chunk}")
    for claim in draft.claims:
        for agenda_id in claim.agenda_ids:
            if agenda_id not in evidence:
                raise ValueError(f"missing Agenda ID: {agenda_id}")
    return cited_ids


def _render_markdown(
    title: str,
    sections: list[ProjectionSection],
    history: list[ProjectionWeeklyHistory],
) -> str:
    current = "\n\n".join(
        f"## {section.title}\n\n{section.body}" for section in sections
    )
    updates = "\n\n".join(
        f"### {entry.week}\n\n{entry.body}" for entry in history
    )
    history_body = f"\n\n## 주차별 업데이트 이력\n\n{updates}" if updates else ""
    return f"# {title}\n\n{current}{history_body}".strip()


def build_projection_revision(
    spec: WikiProjectionSpec,
    week: str,
    evidence: list[ClassificationItem],
    topic_documents: list[dict[str, Any]],
    previous: WikiProjectionDocument | None,
    analysis_source: Any,
    draft_source: Any,
    *,
    model: str,
    build_run_id: str,
    child_documents: Sequence[WikiProjectionDocument] = (),
) -> WikiProjectionDocument:
    by_agenda_id = {item.agenda_id: item for item in evidence}
    if len(by_agenda_id) != len(evidence):
        raise ValueError("duplicate Agenda ID")
    previous_ids = set(previous.source_agenda_ids) if previous else set()
    new_ids = sorted(set(by_agenda_id) - previous_ids)
    context = {
        "projection": spec.model_dump(mode="json"),
        "previous_document": previous.model_dump(mode="json") if previous else None,
        "topic_documents": topic_documents,
        "child_documents": [
            {
                "projection_id": child.projection_id,
                "summary": child.summary,
                "claims": [claim.model_dump(mode="json") for claim in child.claims],
                "as_of_week": child.as_of_week,
            }
            for child in child_documents
        ],
        "evidence": [
            by_agenda_id[key].model_dump(mode="json") for key in sorted(by_agenda_id)
        ],
        "new_evidence": [
            by_agenda_id[key].model_dump(mode="json") for key in new_ids
        ],
        "week": week,
    }
    analysis = ProjectionAnalysis.model_validate(
        analysis_source(context) if callable(analysis_source) else analysis_source
    )
    draft = ProjectionDraft.model_validate(
        draft_source(context, analysis) if callable(draft_source) else draft_source
    )
    validate_projection_draft(draft, by_agenda_id)
    history = [
        ProjectionWeeklyHistory(
            week=week,
            body=draft.weekly_update,
            agenda_ids=new_ids,
        ),
        *(
            entry
            for entry in (previous.weekly_history if previous else [])
            if entry.week != week
        ),
    ]
    history.sort(key=lambda entry: entry.week, reverse=True)
    body_markdown = _render_markdown(spec.title, draft.sections, history)
    revision_payload = {
        "projection_id": spec.projection_id,
        "week": week,
        "body_markdown": body_markdown,
        "claims": [claim.model_dump(mode="json") for claim in draft.claims],
        "model": model,
        "build_run_id": build_run_id,
    }
    revision_id = "PROJ-" + hashlib.sha256(
        json.dumps(
            revision_payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16].upper()
    return WikiProjectionDocument(
        **spec.model_dump(mode="python"),
        summary=draft.summary,
        sections=draft.sections,
        claims=draft.claims,
        source_agenda_ids=sorted(by_agenda_id),
        as_of_week=week,
        revision_id=revision_id,
        previous_revision_id=previous.revision_id if previous else None,
        body_markdown=body_markdown,
        weekly_history=history,
        build_run_id=build_run_id,
        model=model,
        published_at=datetime.now(UTC),
    )


def _path_in_scope(
    path: CategoryPath,
    domain: str,
    tech: str | None,
    lotcd: str | None,
) -> bool:
    return (
        path.domain == domain
        and (tech is None or path.tech == tech)
        and (lotcd is None or path.lotcd == lotcd)
    )


def _topic_items(store: JsonWikiStore, topic_id: str) -> list[ClassificationItem]:
    topic = store.topic(topic_id)
    revision = store.topic_revision(topic_id, topic.current_revision_id)
    values = {
        archived.item.agenda_id: archived.item
        for ref in revision.evidence_refs
        for archived in [store.archived_evidence(ref)]
    }
    return [values[key] for key in sorted(values)]


def _topic_payload(store: JsonWikiStore, topic_id: str) -> dict[str, Any]:
    topic = store.topic(topic_id)
    revision = store.topic_revision(topic_id, topic.current_revision_id)
    return {
        "topic": topic.model_dump(mode="json"),
        "revision": revision.model_dump(mode="json"),
    }


def _category_specs(store: JsonWikiStore) -> list[tuple[WikiProjectionSpec, list[ClassificationItem], list[dict[str, Any]]]]:
    topics = store.topics()
    paths = {
        (path.domain, path.tech, path.lotcd)
        for topic in topics
        for path in topic.target_paths
    }
    scopes = {
        *( (domain, None, None) for domain, _tech, _lotcd in paths ),
        *( (domain, tech, None) for domain, tech, _lotcd in paths if tech ),
        *( (domain, tech, lotcd) for domain, tech, lotcd in paths if tech and lotcd ),
    }
    results = []
    for domain, tech, lotcd in sorted(
        scopes,
        key=lambda value: (
            0 if value[2] else 1 if value[1] else 2,
            *(part or "" for part in value),
        ),
    ):
        scoped_topics = [
            topic for topic in topics
            if any(_path_in_scope(path, domain, tech, lotcd) for path in topic.target_paths)
        ]
        items_by_id: dict[str, ClassificationItem] = {}
        direct_ids: set[str] = set()
        for topic in scoped_topics:
            for item in _topic_items(store, topic.topic_id):
                path = item.decision.target_path
                if path is None or not _path_in_scope(path, domain, tech, lotcd):
                    continue
                items_by_id[item.agenda_id] = item
                if path == CategoryPath(domain=domain, tech=tech, lotcd=lotcd):
                    direct_ids.add(item.agenda_id)
        if not items_by_id:
            continue
        kind = "lotcd" if lotcd else "tech" if tech else "domain"
        key = "/".join(part for part in (domain, tech, lotcd) if part)
        label = lotcd or tech or domain
        topic_ids = sorted(topic.topic_id for topic in scoped_topics)
        direct_topic_ids = sorted(
            topic.topic_id for topic in scoped_topics
            if set(topic.source_agenda_ids) & direct_ids
        )
        child_ids = []
        if kind == "tech":
            child_ids = sorted(
                f"lotcd:{domain}/{tech}/{child_lotcd}"
                for child_domain, child_tech, child_lotcd in paths
                if child_domain == domain and child_tech == tech and child_lotcd
            )
        elif kind == "domain":
            child_ids = sorted(
                f"tech:{domain}/{child_tech}"
                for child_domain, child_tech, _child_lotcd in paths
                if child_domain == domain and child_tech
            )
        spec = WikiProjectionSpec(
            projection_id=f"{kind}:{key}",
            kind=kind,
            key=key,
            title=f"{label} Wiki",
            breadcrumb=[part for part in (domain, tech, lotcd) if part],
            direct_topic_ids=direct_topic_ids,
            rolled_up_topic_ids=sorted(set(topic_ids) - set(direct_topic_ids)),
            direct_agenda_ids=sorted(direct_ids),
            rolled_up_agenda_ids=sorted(set(items_by_id) - direct_ids),
            child_document_ids=child_ids,
        )
        results.append((
            spec,
            [items_by_id[key] for key in sorted(items_by_id)],
            [_topic_payload(store, topic_id) for topic_id in topic_ids],
        ))
    return results


def _team_specs(store: JsonWikiStore) -> list[tuple[WikiProjectionSpec, list[ClassificationItem], list[dict[str, Any]]]]:
    topics = store.topics()
    teams = sorted({team for topic in topics for team in topic.teams})
    results = []
    for team in teams:
        scoped_topics = [topic for topic in topics if team in topic.teams]
        items = {
            item.agenda_id: item
            for topic in scoped_topics
            for item in _topic_items(store, topic.topic_id)
            if item.team == team
        }
        if not items:
            continue
        topic_ids = sorted(topic.topic_id for topic in scoped_topics)
        results.append((
            WikiProjectionSpec(
                projection_id=f"team:{team}",
                kind="team",
                key=team,
                title=f"{team} Wiki",
                breadcrumb=[team],
                direct_topic_ids=topic_ids,
                direct_agenda_ids=sorted(items),
            ),
            [items[key] for key in sorted(items)],
            [_topic_payload(store, topic_id) for topic_id in topic_ids],
        ))
    return results


def _week_spec(
    store: JsonWikiStore,
    week: str,
) -> tuple[WikiProjectionSpec, list[ClassificationItem], list[dict[str, Any]]] | None:
    topic_ids = []
    items: dict[str, ClassificationItem] = {}
    for topic in store.topics():
        revision = store.topic_revision(topic.topic_id, topic.current_revision_id)
        week_items = [
            store.archived_evidence(ref).item
            for ref in revision.evidence_refs
            if ref.split("/", 1)[0] == week
        ]
        if not week_items:
            continue
        topic_ids.append(topic.topic_id)
        items.update({item.agenda_id: item for item in week_items})
    if not items:
        return None
    topic_ids.sort()
    return (
        WikiProjectionSpec(
            projection_id=f"week:{week}",
            kind="week",
            key=week,
            title=f"{week} Wiki",
            breadcrumb=[week],
            direct_topic_ids=topic_ids,
            direct_agenda_ids=sorted(items),
        ),
        [items[key] for key in sorted(items)],
        [_topic_payload(store, topic_id) for topic_id in topic_ids],
    )


def refresh_projection_documents(
    store: JsonWikiStore,
    week: str,
    build_run_id: str,
    analysis_fn: ProjectionAnalysisFn,
    draft_fn: ProjectionDraftFn,
    *,
    model: str,
) -> list[str]:
    failed: list[str] = []
    specs = [*_category_specs(store), *_team_specs(store)]
    week_spec = _week_spec(store, week)
    if week_spec:
        specs.append(week_spec)
    for spec, evidence, topic_documents in specs:
        if any(child_id in failed for child_id in spec.child_document_ids):
            failed.append(spec.projection_id)
            continue
        try:
            try:
                previous = store.projection(spec.kind, spec.key)
            except KeyError:
                previous = None
            if previous and set(item.agenda_id for item in evidence) <= set(previous.source_agenda_ids):
                continue
            children = []
            for child_id in spec.child_document_ids:
                child_kind, child_key = child_id.split(":", 1)
                children.append(store.projection(child_kind, child_key))
            document = build_projection_revision(
                spec,
                week,
                evidence,
                topic_documents,
                previous,
                analysis_fn,
                draft_fn,
                model=model,
                build_run_id=build_run_id,
                child_documents=children,
            )
            store.save_projection(document)
        except Exception:
            failed.append(spec.projection_id)
    return failed
