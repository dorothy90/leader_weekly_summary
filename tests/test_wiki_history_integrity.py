from datetime import UTC, datetime

import pytest

from knowledge_models import (
    ArchivedApprovedEvidence,
    CategoryPath,
    ClassificationDecision,
    ClassificationItem,
    TopicAssignment,
    TopicRevision,
    TopicSection,
    TopicRelation,
    SupportedClaim,
    WikiTopic,
    WeekRelationReviewEvent,
    WeekWikiView,
    WikiReview,
    WikiReviewResolution,
)
from topic_linker import resolve_wiki_review
from wiki_store import JsonWikiStore
from wiki_projections import build_topic_detail


def _item(agenda_id: str = "A-001", lotcd: str = "4SA") -> ClassificationItem:
    return ClassificationItem(
        agenda_id=agenda_id,
        mail_id="M-001",
        summary="approved summary",
        source_quote="immutable quote",
        classification_context="approved context",
        item_kind="lotcd_specific",
        decision=ClassificationDecision(
            status="confirmed",
            target_path=CategoryPath(domain="DRAM", tech="Spica", lotcd=lotcd),
            confidence=1,
        ),
        revision_count=0,
        team="Yield",
        subject="approved subject",
        received_at=datetime(2026, 7, 20, tzinfo=UTC),
        source_path="mail/M-001",
    )


def test_approved_evidence_archive_is_immutable_and_addressed_by_run(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki")
    first = ArchivedApprovedEvidence(
        evidence_ref="2026-W30/CLASS-1/A-001",
        week="2026-W30",
        classification_run_id="CLASS-1",
        item=_item(),
        archived_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    store.archive_evidence(first)

    changed = first.model_copy(update={"item": _item().model_copy(update={"source_quote": "rerun quote"})})
    store.archive_evidence(changed)

    assert store.archived_evidence(first.evidence_ref).item.source_quote == "immutable quote"


def test_review_transition_recovers_after_fault_between_target_and_review(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    store = JsonWikiStore(root)
    review = WikiReview(review_id="R-1", kind="assignment", agenda_id="A-001")
    store.save_review(review)
    original = store._atomic_write

    def fail_review(path, value):
        if path.name == "R-1.json" and getattr(value, "status", None) == "resolved":
            raise OSError("injected transition fault")
        original(path, value)

    monkeypatch.setattr(store, "_atomic_write", fail_review)
    try:
        resolve_wiki_review(
            store, "R-1", WikiReviewResolution(action="create", title="new"), "operator"
        )
    except OSError:
        pass

    recovered = JsonWikiStore(root)
    assert recovered.assignment("A-001") is not None
    resolved = recovered.reviews()[0]
    assert (resolved.status, resolved.resolved_by, resolved.resolution_action) == (
        "resolved", "operator", "create"
    )


def test_revision_audit_fields_round_trip_with_explicit_delta():
    revision = TopicRevision(
        revision_id="REV-1",
        topic_id="T-1",
        week="2026-W30",
        body_markdown="body",
        sections=[],
        claims=[],
        source_agenda_ids=["A-001"],
        evidence_refs=["2026-W30/CLASS-1/A-001"],
        previous_state="investigating",
        new_state="monitoring",
        added_agenda_ids=["A-001"],
        added_claims=[], removed_claims=[], changed_claims=[], relation_changes=[],
        build_run_id="RUN-1", prompt_version="p1", builder_version="b1",
        validation_results=["citations:ok"],
        created_at=datetime(2026, 7, 20, tzinfo=UTC), model="test-model",
    )
    assert revision.new_state == "monitoring"


def test_topic_detail_resolves_revision_evidence_from_archive_not_mutable_store(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki")
    archived = ArchivedApprovedEvidence(
        evidence_ref="2026-W30/CLASS-1/A-001", week="2026-W30",
        classification_run_id="CLASS-1", item=_item(),
        archived_at=datetime(2026, 7, 20, tzinfo=UTC),
    )
    store.archive_evidence(archived)
    topic = WikiTopic(
        topic_id="T-1", title="topic", topic_kind="issue", primary_area="yield_defect",
        state="monitoring", importance="high", first_seen_week="2026-W30",
        last_updated_week="2026-W30", target_paths=[_item().decision.target_path],
        teams=["Yield"], source_agenda_ids=["A-001"], current_revision_id="REV-1",
    )
    revision = TopicRevision(
        revision_id="REV-1", topic_id="T-1", week="2026-W30",
        body_markdown="approved [agenda:A-001]", sections=[], claims=[],
        source_agenda_ids=["A-001"], evidence_refs=[archived.evidence_ref],
        created_at=datetime(2026, 7, 20, tzinfo=UTC), model="test",
    )
    store.publish_topic(topic, revision)

    class MutatedClassification:
        def classification_item(self, _agenda_id):
            raise AssertionError("removed mutable classification evidence was read")

    detail = build_topic_detail(store, MutatedClassification(), "T-1")
    assert [(value.week, value.source_quote) for value in detail.evidence] == [
        ("2026-W30", "immutable quote")
    ]


def test_relation_review_transition_recovers_week_snapshot_write(tmp_path, monkeypatch):
    root = tmp_path / "wiki"
    store = JsonWikiStore(root)
    now = datetime(2026, 7, 20, tzinfo=UTC)
    base = WeekWikiView(
        week="2026-W30", revision_id="WREV-OLD", published_at=now,
        build_run_id="RUN-1", new_topic_ids=[], changed_topic_ids=[],
        resolved_topic_ids=[], reopened_topic_ids=[], actions_and_decisions=[],
        new_relation_ids=[], pending_assignment_count=0, contradictions=[], teams=[],
    )
    store.save_week(base)
    relation = TopicRelation(
        relation_id="REL-1", source_topic_id="T-1", target_topic_id="T-2",
        kind="supports", agenda_ids=["A-001"], confidence=.8,
        review_state="accepted", creation_week="2026-W30",
    )
    review = WikiReview(
        review_id="R-REL-1", kind="relation", relation_id="REL-1",
        status="resolved", resolved_by="operator", resolved_at=now,
        resolution_action="accept",
    )
    event = WeekRelationReviewEvent(
        relation_id="REL-1", relation_kind="supports", origin_week="2026-W30",
        action="accepted", actor="operator", reviewed_at=now,
    )
    original = store._atomic_write

    def fail_week(path, value):
        if path == root / "weeks" / "2026-W30.json" and getattr(value, "revision_id", None) != "WREV-OLD":
            raise OSError("injected week fault")
        original(path, value)

    monkeypatch.setattr(store, "_atomic_write", fail_week)
    with pytest.raises(OSError, match="week fault"):
        store.apply_review_transition(review, relation=relation, relation_event=event)

    recovered = JsonWikiStore(root)
    assert recovered.relation("REL-1").review_state == "accepted"
    assert recovered.reviews()[0].status == "resolved"
    assert recovered.week("2026-W30").revision_id != "WREV-OLD"
    assert recovered.week("2026-W30").new_relation_ids == ["REL-1"]


def test_concurrent_relation_events_merge_latest_week_without_lost_update(tmp_path):
    root = tmp_path / "wiki"
    store = JsonWikiStore(root)
    now = datetime(2026, 7, 20, tzinfo=UTC)
    base = WeekWikiView(
        week="2026-W30", revision_id="WREV-BASE", published_at=now,
        build_run_id="RUN-1", new_topic_ids=[], changed_topic_ids=[],
        resolved_topic_ids=[], reopened_topic_ids=[], actions_and_decisions=[],
        new_relation_ids=[], pending_assignment_count=0, contradictions=[], teams=[],
    )
    store.save_week(base)

    prepared = []
    for relation_id, kind in (("REL-1", "supports"), ("REL-2", "contradicts")):
        relation = TopicRelation(
            relation_id=relation_id, source_topic_id="T-1", target_topic_id="T-2",
            kind=kind, agenda_ids=["A-001"], confidence=.8,
            review_state="accepted", creation_week="2026-W30",
        )
        review = WikiReview(
            review_id=f"R-{relation_id}", kind="relation", relation_id=relation_id,
            status="resolved", resolved_by="operator", resolved_at=now,
            resolution_action="accept",
        )
        event = WeekRelationReviewEvent(
            relation_id=relation_id, relation_kind=kind, origin_week="2026-W30",
            action="accepted", actor="operator", reviewed_at=now,
        )
        prepared.append((relation, review, event))

    for relation, review, event in prepared:
        store.apply_review_transition(
            review, relation=relation, relation_event=event
        )
    relation, review, event = prepared[0]
    store.apply_review_transition(review, relation=relation, relation_event=event)

    current = store.week("2026-W30")
    assert current.new_relation_ids == ["REL-1", "REL-2"]
    assert current.contradictions == ["REL-2"]
    assert [value.relation_id for value in current.relation_review_events] == [
        "REL-1", "REL-2"
    ]
    history = list((root / "history" / "weeks" / "2026-W30").glob("*.json"))
    assert len(history) == 2
    assert "WREV-BASE" in {path.stem for path in history}

    reverse = JsonWikiStore(tmp_path / "wiki-reverse")
    reverse.save_week(base)
    for relation, review, event in reversed(prepared):
        reverse.apply_review_transition(
            review, relation=relation, relation_event=event
        )
    reverse_current = reverse.week("2026-W30")
    assert reverse_current.revision_id == current.revision_id
    assert reverse_current.new_relation_ids == current.new_relation_ids
    assert reverse_current.contradictions == current.contradictions
