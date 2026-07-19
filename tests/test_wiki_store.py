import json
from datetime import UTC, datetime, timedelta

import pytest

from knowledge_models import (
    CategoryPath,
    TopicAssignment,
    TopicRevision,
    WeekWikiView,
    WikiBuildRun,
    WikiReview,
    WikiTopic,
)
from wiki_store import JsonWikiStore, WikiStoreLockError


def topic(revision_id: str = "REV-001") -> WikiTopic:
    return WikiTopic(
        topic_id="T-001",
        title="4SA D1 defect",
        topic_kind="issue",
        primary_area="yield_defect",
        state="monitoring",
        importance="high",
        first_seen_week="2026-W29",
        last_updated_week="2026-W30",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        teams=["Yield"],
        source_agenda_ids=["A-001"],
        current_revision_id=revision_id,
    )


def revision(revision_id: str = "REV-001") -> TopicRevision:
    return TopicRevision(
        revision_id=revision_id,
        topic_id="T-001",
        week="2026-W30",
        body_markdown="Current state",
        sections=[],
        claims=[],
        source_agenda_ids=["A-001"],
        created_at=datetime(2026, 7, 19, tzinfo=UTC),
        model="test-model",
    )


def test_publish_revision_keeps_current_and_history(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    store.publish_topic(topic(), revision())

    assert store.topic("T-001").current_revision_id == "REV-001"
    assert store.topic_revision("T-001", "REV-001").topic_id == "T-001"


def test_publish_rejects_mismatched_revision_identity(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")

    with pytest.raises(ValueError, match="identity mismatch"):
        store.publish_topic(topic("REV-002"), revision("REV-001"))


def test_second_build_lock_is_rejected(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    with store.build_lock():
        with pytest.raises(WikiStoreLockError):
            with store.build_lock():
                pass


def test_records_round_trip_and_lists_are_sorted(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    first = TopicAssignment(
        agenda_id="A-002",
        topic_id="T-001",
        decision="attach",
        confidence=0.9,
        rationale="same issue",
        decision_source="auto",
        decided_by="linker",
        decided_at=datetime(2026, 7, 19, tzinfo=UTC),
    )
    store.save_assignment(first)
    store.save_review(WikiReview(review_id="R-002", kind="assignment"))
    store.save_review(WikiReview(review_id="R-001", kind="assignment", status="held"))

    assert store.assignment("A-002") == first
    assert store.assignment("missing") is None
    assert [item.review_id for item in store.reviews()] == ["R-001", "R-002"]
    assert [item.review_id for item in store.reviews("pending")] == ["R-002"]
    assert store.assignment_digest() == store.assignment_digest()


def test_week_replacement_archives_previous_revision(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    published_at = datetime(2026, 7, 19, tzinfo=UTC)
    first = WeekWikiView(
        week="2026-W30",
        revision_id="WREV-001",
        published_at=published_at,
        build_run_id="RUN-001",
        new_topic_ids=[],
        changed_topic_ids=[],
        resolved_topic_ids=[],
        reopened_topic_ids=[],
        new_relation_ids=[],
        pending_assignment_count=0,
        contradictions=[],
        teams=[],
    )
    second = first.model_copy(update={"revision_id": "WREV-002"})

    store.save_week(first)
    store.save_week(second)

    assert store.week("2026-W30").revision_id == "WREV-002"
    history = store.root / "history" / "weeks" / "2026-W30" / "WREV-001.json"
    assert WeekWikiView.model_validate_json(history.read_text()).revision_id == "WREV-001"


def test_rebuild_catalog_uses_canonical_topics(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    store.publish_topic(topic(), revision())

    catalog = store.rebuild_catalog()

    assert [item["topic_id"] for item in catalog] == ["T-001"]
    assert json.loads((store.root / "catalog.json").read_text()) == catalog
    assert not (store.root / "catalog.json.tmp").exists()


def test_recover_incomplete_builds_only_fails_stale_transient_runs(tmp_path):
    now = datetime(2026, 7, 19, 12, tzinfo=UTC)
    store = JsonWikiStore(tmp_path / "wiki_data", recovery_threshold=timedelta(hours=1))
    stale = WikiBuildRun(
        run_id="RUN-stale",
        week="2026-W30",
        classification_run_id="CLASS-001",
        status="generating",
        input_hash="stale",
        model="test-model",
        started_at=now - timedelta(hours=2),
    )
    recent = stale.model_copy(
        update={"run_id": "RUN-recent", "input_hash": "recent", "started_at": now}
    )
    boundary = stale.model_copy(
        update={
            "run_id": "RUN-boundary",
            "input_hash": "boundary",
            "started_at": now - timedelta(hours=1),
        }
    )
    published = stale.model_copy(
        update={"run_id": "RUN-published", "input_hash": "done", "status": "published"}
    )
    store.save_build(stale)
    store.save_build(recent)
    store.save_build(boundary)
    store.save_build(published)
    store.publish_topic(topic(), revision())

    recovered = store.recover_incomplete_builds(now=now)

    assert [run.run_id for run in recovered] == ["RUN-stale"]
    failed = store.build("RUN-stale")
    assert (failed.status, failed.completed_at, failed.error) == (
        "failed",
        now,
        "interrupted build",
    )
    assert store.build("RUN-recent").status == "generating"
    assert store.build("RUN-boundary").status == "generating"
    assert store.build("RUN-published").status == "published"
    assert store.topic("T-001").current_revision_id == "REV-001"


def test_build_lifecycle_and_success_lookup(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    run = store.start_build("2026-W30", "CLASS-001", "hash", "test-model")

    assert store.successful_build("hash") is None
    finished = store.finish_build(run, "published")

    assert store.build(run.run_id) == finished
    assert store.successful_build("hash") == finished
