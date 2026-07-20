from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from knowledge_models import (
    CategoryPath,
    LotcdWikiView,
    TeamWikiView,
    TopicListItem,
    TopicRelation,
    WeekWikiView,
    WikiEvidence,
    WikiTopic,
    WikiTopicDetail,
)
from wiki_store import JsonWikiStore
from source_mail import resolve_mail_html


LOTCD_SECTION_ORDER = (
    "summary",
    "recent_changes",
    "active_topics",
    "knowledge_areas",
    "actions_and_decisions",
    "related_lotcds",
    "closed_topics",
    "activity",
)
IMPORTANCE = {"critical": 3, "high": 2, "medium": 1, "low": 0}
UNRESOLVED_STATES = {
    "new",
    "investigating",
    "action_in_progress",
    "monitoring",
    "reopened",
    "review_required",
}


def _relations(store: JsonWikiStore, accepted_only: bool = True) -> list[TopicRelation]:
    values = [
        TopicRelation.model_validate_json(path.read_text(encoding="utf-8"))
        for path in store.root.joinpath("relations").glob("*.json")
    ]
    return sorted(
        (
            value
            for value in values
            if not accepted_only or value.review_state == "accepted"
        ),
        key=lambda value: value.relation_id,
    )


def _week_date(week: str) -> date:
    year, number = week.split("-W", 1)
    return date.fromisocalendar(int(year), int(number), 1)


def _recent(topic: WikiTopic, reference_week: str) -> bool:
    age = (_week_date(reference_week) - _week_date(topic.last_updated_week)).days
    return 0 <= age <= 28


def _ranked_items(
    store: JsonWikiStore,
    topics: list[WikiTopic],
    reference_week: str,
) -> list[TopicListItem]:
    accepted = _relations(store)
    topics_by_id = {topic.topic_id: topic for topic in topics}
    relation_counts: dict[str, int] = {}
    for relation in accepted:
        relation_counts[relation.source_topic_id] = (
            relation_counts.get(relation.source_topic_id, 0) + 1
        )
        relation_counts[relation.target_topic_id] = (
            relation_counts.get(relation.target_topic_id, 0) + 1
        )

    def item(topic: WikiTopic) -> TopicListItem:
        recent = _recent(topic, reference_week)
        unresolved = topic.state in UNRESOLVED_STATES
        reasons = [f"importance:{topic.importance}"]
        if unresolved:
            reasons.append("unresolved")
        if recent:
            reasons.append("recent_change")
        reasons.extend(
            (
                f"team_count:{len(topic.teams)}",
                f"evidence_count:{len(topic.source_agenda_ids)}",
                f"accepted_relation_count:{relation_counts.get(topic.topic_id, 0)}",
            )
        )
        return TopicListItem(
            topic_id=topic.topic_id,
            title=topic.title,
            state=topic.state,
            importance=topic.importance,
            primary_area=topic.primary_area,
            target_paths=topic.target_paths,
            teams=topic.teams,
            last_updated_week=topic.last_updated_week,
            evidence_count=len(topic.source_agenda_ids),
            rank_reasons=reasons,
        )

    return sorted(
        (item(topic) for topic in topics),
        key=lambda value: (
            -IMPORTANCE[value.importance],
            -(value.state in UNRESOLVED_STATES),
            -_recent(topics_by_id[value.topic_id], reference_week),
            -len(value.teams),
            -value.evidence_count,
            -relation_counts.get(value.topic_id, 0),
            value.topic_id,
        ),
    )


def list_topics(
    store: JsonWikiStore,
    *,
    q: str | None = None,
    state: str | None = None,
    area: str | None = None,
    team: str | None = None,
    lotcd: str | None = None,
) -> list[TopicListItem]:
    all_topics = store.topics()
    topics = [
        topic
        for topic in all_topics
        if (not q or q.casefold() in f"{topic.topic_id} {topic.title}".casefold())
        and (not state or topic.state == state)
        and (not area or topic.primary_area == area or area in topic.secondary_areas)
        and (not team or team in topic.teams)
        and (
            not lotcd
            or any(path.lotcd == lotcd for path in topic.target_paths)
        )
    ]
    if not topics:
        return []
    reference_week = max(topic.last_updated_week for topic in all_topics)
    return _ranked_items(store, topics, reference_week)


def _evidence(
    store: JsonWikiStore,
    classification_store: Any,
    agenda_ids: list[str],
    evidence_refs: list[str] | None = None,
    include: Callable[[Any], bool] | None = None,
) -> list[WikiEvidence]:
    values: list[WikiEvidence] = []
    archived_by_id = {}
    for evidence_ref in evidence_refs or []:
        archived = store.archived_evidence(evidence_ref)
        archived_by_id[archived.item.agenda_id] = archived
    for agenda_id in sorted(set(agenda_ids)):
        archived = archived_by_id.get(agenda_id)
        if archived is not None:
            week, item = archived.week, archived.item
        else:
            raise KeyError(f"Missing immutable Wiki evidence: {agenda_id}")
        if include is not None and not include(item):
            continue
        values.append(
            WikiEvidence(
                agenda_id=item.agenda_id,
                mail_id=item.mail_id,
                team=item.team,
                week=week,
                subject=item.subject,
                source_quote=item.source_quote,
                source_path=item.source_path,
                **_mail_reference(item.agenda_id, item.source_path),
            )
        )
    return values


def _mail_reference(agenda_id: str, source_path: str | None) -> dict[str, object]:
    if source_path is None:
        return {"mail_html_available": False, "original_mail_url": None}
    try:
        resolve_mail_html(source_path, Path(os.getenv("MAIL_DATA_DIR", "data")))
    except (FileNotFoundError, ValueError):
        return {"mail_html_available": False, "original_mail_url": None}
    return {
        "mail_html_available": True,
        "original_mail_url": (
            f"/api/knowledge/evidence/{quote(agenda_id, safe='')}/mail#agenda-source"
        ),
    }


def _source_agenda_ids(topics: list[WikiTopic]) -> list[str]:
    return sorted(
        {
            agenda_id
            for topic in topics
            for agenda_id in topic.source_agenda_ids
        }
    )


def _evidence_refs(store: JsonWikiStore, topics: list[WikiTopic]) -> list[str]:
    return sorted({
        ref
        for topic in topics
        for ref in store.topic_revision(
            topic.topic_id, topic.current_revision_id
        ).evidence_refs
    })


def _four_week_activity(
    evidence: list[WikiEvidence],
    projection_week: str,
) -> list[WikiEvidence]:
    anchor = max(
        [_week_date(projection_week), *(_week_date(item.week) for item in evidence)]
    )
    recent = [
        item
        for item in evidence
        if 0 <= (anchor - _week_date(item.week)).days < 28
    ]
    return sorted(
        recent,
        key=lambda item: (-_week_date(item.week).toordinal(), item.agenda_id),
    )


def build_topic_detail(
    store: JsonWikiStore,
    classification_store: Any,
    topic_id: str,
) -> WikiTopicDetail:
    topic = store.topic(topic_id)
    revision = store.topic_revision(topic_id, topic.current_revision_id)
    relations = [
        relation
        for relation in _relations(store)
        if topic_id in {relation.source_topic_id, relation.target_topic_id}
    ]
    return WikiTopicDetail(
        topic=topic,
        body_markdown=revision.body_markdown,
        sections=revision.sections,
        claims=revision.claims,
        evidence=_evidence(
            store, classification_store, revision.source_agenda_ids,
            revision.evidence_refs,
        ),
        relations=relations,
    )


def _has_actions(store: JsonWikiStore, topic: WikiTopic) -> bool:
    revision = store.topic_revision(topic.topic_id, topic.current_revision_id)
    return any(section.key == "actions_and_decisions" for section in revision.sections)


def build_lotcd_view(
    store: JsonWikiStore,
    classification_store: Any,
    domain: str,
    tech: str,
    lotcd: str,
) -> LotcdWikiView:
    return build_category_view(
        store, classification_store, domain, tech, lotcd
    )


def _path_in_scope(
    path: CategoryPath,
    domain: str,
    tech: str | None,
    lotcd: str | None,
) -> bool:
    if path.domain != domain:
        return False
    if tech is not None and path.tech != tech:
        return False
    if lotcd is not None and path.lotcd != lotcd:
        return False
    return True


def build_category_view(
    store: JsonWikiStore,
    classification_store: Any,
    domain: str,
    tech: str | None = None,
    lotcd: str | None = None,
) -> LotcdWikiView:
    if lotcd is not None and tech is None:
        raise ValueError("LOTCD scope requires a Tech")
    selected_path = CategoryPath(domain=domain, tech=tech, lotcd=lotcd)
    topics = [
        topic
        for topic in store.topics()
        if any(
            _path_in_scope(path, domain, tech, lotcd)
            for path in topic.target_paths
        )
    ]
    reference_week = max(
        (topic.last_updated_week for topic in store.topics()), default="1970-W01"
    )
    ranked = _ranked_items(store, topics, reference_week)
    by_id = {topic.topic_id: topic for topic in topics}
    knowledge_areas: dict[str, list[TopicListItem]] = {}
    for item in ranked:
        knowledge_areas.setdefault(item.primary_area, []).append(item)
    related_lotcds = {
        path.lotcd
        for topic in topics
        for path in topic.target_paths
        if path.lotcd and path.lotcd != lotcd
    }
    accepted = _relations(store)
    all_topics = {topic.topic_id: topic for topic in store.topics()}
    topic_ids = set(by_id)
    for relation in accepted:
        if relation.source_topic_id in topic_ids:
            other_id = relation.target_topic_id
        elif relation.target_topic_id in topic_ids:
            other_id = relation.source_topic_id
        else:
            continue
        related_lotcds.update(
            path.lotcd
            for path in all_topics[other_id].target_paths
            if path.lotcd and path.lotcd != lotcd
        )
    closed: dict[str, list[TopicListItem]] = {}
    for item in ranked:
        if item.state not in {"resolved", "closed"}:
            continue
        week_date = _week_date(item.last_updated_week)
        quarter = f"{week_date.year}-Q{(week_date.month - 1) // 3 + 1}"
        closed.setdefault(quarter, []).append(item)
    activity = _evidence(
        store,
        classification_store,
        _source_agenda_ids(topics),
        _evidence_refs(store, topics),
        lambda item: item.decision.target_path is not None
        and _path_in_scope(item.decision.target_path, domain, tech, lotcd),
    )
    direct_activity = [
        item
        for item in activity
        if store.archived_evidence(
            next(
                ref
                for ref in _evidence_refs(store, topics)
                if ref.endswith(f"/{item.agenda_id}")
            )
        ).item.decision.target_path == selected_path
    ]
    direct_ids = {item.agenda_id for item in direct_activity}
    scope_level = "lotcd" if lotcd is not None else "tech" if tech is not None else "domain"
    label = lotcd or tech or domain
    return LotcdWikiView(
        domain=domain,
        tech=tech,
        lotcd=lotcd,
        scope_level=scope_level,
        breadcrumb=[part for part in (domain, tech, lotcd) if part is not None],
        summary=f"{label}: {len(ranked)} Topics",
        recent_changes=[
            item
            for item in ranked
            if _recent(by_id[item.topic_id], reference_week)
        ],
        active_topics=[item for item in ranked if item.state in UNRESOLVED_STATES],
        knowledge_areas=knowledge_areas,
        actions_and_decisions=[
            item
            for item in ranked
            if _has_actions(store, by_id[item.topic_id])
        ],
        related_lotcds=sorted(related_lotcds),
        closed_topics=closed,
        activity=activity,
        direct_activity=direct_activity,
        rolled_up_activity=[
            item for item in activity if item.agenda_id not in direct_ids
        ],
        topic_ids=sorted(by_id),
    )


def build_team_view(
    store: JsonWikiStore,
    classification_store: Any,
    team: str,
) -> TeamWikiView:
    topics = [topic for topic in store.topics() if team in topic.teams]
    reference_week = max(
        (topic.last_updated_week for topic in store.topics()), default="1970-W01"
    )
    ranked = _ranked_items(store, topics, reference_week)
    paths = {
        (path.domain, path.tech, path.lotcd)
        for topic in topics
        for path in topic.target_paths
    }
    team_evidence = _evidence(
        store,
        classification_store,
        _source_agenda_ids(topics),
        _evidence_refs(store, topics),
        lambda item: item.team == team,
    )
    projection_week = max(
        (topic.last_updated_week for topic in topics), default=reference_week
    )
    return TeamWikiView(
        team=team,
        topics=ranked,
        topic_ids=sorted(topic.topic_id for topic in topics),
        recent_activity=_four_week_activity(team_evidence, projection_week),
        partner_teams=sorted(
            {partner for topic in topics for partner in topic.teams if partner != team}
        ),
        target_paths=[
            CategoryPath(domain=domain, tech=tech, lotcd=lotcd)
            for domain, tech, lotcd in sorted(
                paths,
                key=lambda value: tuple(part or "" for part in value),
            )
        ],
        actions_and_decisions=[
            item
            for item in ranked
            if _has_actions(
                store,
                next(
                    topic
                    for topic in topics
                    if topic.topic_id == item.topic_id
                ),
            )
        ],
    )


def build_week_view(
    store: JsonWikiStore,
    week: str,
    build_run_id: str | None = None,
) -> WeekWikiView:
    try:
        previous_snapshot = store.week(week)
        review_events = previous_snapshot.relation_review_events
    except KeyError:
        review_events = []
    resolved_build_run_id = build_run_id
    if resolved_build_run_id is None:
        try:
            resolved_build_run_id = store.week(week).build_run_id
        except KeyError:
            resolved_build_run_id = ""
    revisions = []
    topics = []
    for topic in store.topics():
        revision = store.topic_revision(topic.topic_id, topic.current_revision_id)
        if (
            revision.week == week
            and (
                not resolved_build_run_id
                or not revision.build_run_id  # pre-audit migration revision
                or revision.build_run_id == resolved_build_run_id
            )
        ):
            topics.append(topic)
            revisions.append(revision)
    ranked = _ranked_items(store, topics, week)
    topics_by_id = {topic.topic_id: topic for topic in topics}
    accepted = _relations(store)
    changed_ids = sorted(revision.topic_id for revision in revisions)
    new_ids = sorted(
        topic.topic_id for topic in topics if topic.first_seen_week == week
    )
    resolved_ids = sorted(
        revision.topic_id for revision in revisions
        if (
            revision.previous_state not in {"resolved", "closed"}
            and revision.new_state in {"resolved", "closed"}
        ) or (
            revision.previous_state is None and topics_by_id[revision.topic_id].state in {"resolved", "closed"}
        )
    )
    reopened_ids = sorted(
        revision.topic_id for revision in revisions
        if (
            revision.previous_state in {"resolved", "closed"}
            and revision.new_state == "reopened"
        ) or (
            revision.previous_state is None and topics_by_id[revision.topic_id].state == "reopened"
        )
    )
    relation_ids = sorted({
        relation.relation_id for relation in accepted
        if relation.relation_id in {
            change.relation_id
            for revision in revisions
            for change in revision.relation_changes
            if change.action == "accepted"
        } or (
            not relation.created_build_run_id
            and {relation.source_topic_id, relation.target_topic_id} & set(changed_ids)
        )
    } | {
        event.relation_id for event in review_events if event.action == "accepted"
    })
    target_agenda_ids: set[str] = set()
    if resolved_build_run_id:
        try:
            build = store.build(resolved_build_run_id)
            target_agenda_ids = set(
                store.archived_week(week, build.classification_run_id).items
            )
        except KeyError:
            pass
    if not target_agenda_ids:
        target_agenda_ids = {
            agenda_id for revision in revisions for agenda_id in revision.source_agenda_ids
        }
    pending_count = sum(
        review.kind == "assignment" and review.agenda_id in target_agenda_ids
        for review in store.reviews("pending")
    )
    payload = {
        "week": week,
        "build_run_id": resolved_build_run_id,
        "new_topic_ids": new_ids,
        "changed_topic_ids": changed_ids,
        "resolved_topic_ids": resolved_ids,
        "reopened_topic_ids": reopened_ids,
        "actions_and_decisions": [
            item
            for item in ranked
            if _has_actions(store, topics_by_id[item.topic_id])
        ],
        "new_relation_ids": relation_ids,
        "relation_review_events": review_events,
        "pending_assignment_count": pending_count,
        "contradictions": sorted(
            relation.relation_id
            for relation in accepted
            if relation.kind == "contradicts" and relation.relation_id in relation_ids
        ),
        "teams": sorted({team for topic in topics for team in topic.teams}),
    }
    hash_payload = {
        **payload,
        "actions_and_decisions": [
            item.model_dump(mode="json")
            for item in payload["actions_and_decisions"]
        ],
        "relation_review_events": [
            event.model_dump(mode="json")
            for event in payload["relation_review_events"]
        ],
    }
    revision_id = "WREV-" + hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16].upper()
    return WeekWikiView(
        revision_id=revision_id,
        published_at=datetime.now(UTC),
        **payload,
    )
