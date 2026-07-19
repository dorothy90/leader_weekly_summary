import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from classification_store import JsonClassificationStore
from knowledge_models import (
    CategoryPath,
    TopicAssignment,
    TopicRevision,
    TopicSection,
    WikiReview,
    WikiTopic,
)
from topic_linker import TopicLinkDecision
from topic_wiki_builder import RelationProposal, TopicAnalysis, TopicDraft, build_week
from wiki_projections import (
    LOTCD_SECTION_ORDER,
    build_lotcd_view,
    build_team_view,
    build_topic_detail,
    build_week_view,
    list_topics,
)
from wiki_store import JsonWikiStore


FIXTURE = Path(__file__).parents[1] / "fixtures" / "wiki" / "approved-week.json"
RULES = Path(__file__).parents[1] / "config" / "classification_rules.json"


def path(lotcd="4SA"):
    return CategoryPath(domain="DRAM", tech="Spica", lotcd=lotcd)


def topic(topic_id="T-001", *, week="2026-W30", state="investigating"):
    return WikiTopic(
        topic_id=topic_id,
        title="4SA 수율 하락",
        topic_kind="issue",
        primary_area="yield_defect",
        state=state,
        importance="high",
        first_seen_week="2026-W29",
        last_updated_week=week,
        target_paths=[path(), path("8HBM")],
        teams=["Yield", "Process"],
        source_agenda_ids=["A-001", "A-002", "A-003"],
        current_revision_id=f"REV-{topic_id}",
    )


def revision(value):
    return TopicRevision(
        revision_id=value.current_revision_id,
        topic_id=value.topic_id,
        week=value.last_updated_week,
        body_markdown="## 조치와 의사결정\n\n조건을 조정했다. [agenda:A-001]",
        sections=[
            TopicSection(
                key="actions_and_decisions",
                title="조치와 의사결정",
                body="조건을 조정했다. [agenda:A-001]",
            )
        ],
        claims=[],
        source_agenda_ids=["A-001"],
        created_at=datetime(2026, 7, 19, tzinfo=UTC),
        model="test-model",
    )


@pytest.fixture
def stores(tmp_path, monkeypatch):
    data_dir = tmp_path / "classification_data"
    data_dir.mkdir()
    shutil.copy(FIXTURE, data_dir / "2026-W30.json")
    rules_path = tmp_path / "classification_rules.json"
    shutil.copy(RULES, rules_path)
    classification = JsonClassificationStore(data_dir, rules_path)
    wiki = JsonWikiStore(tmp_path / "wiki_data")
    value = topic()
    wiki.publish_topic(value, revision(value))
    for agenda_id in ("A-001", "A-002", "A-003"):
        wiki.save_assignment(
            TopicAssignment(
                agenda_id=agenda_id,
                topic_id="T-001",
                decision="attach",
                confidence=1,
                rationale="accepted",
                decision_source="manual",
                decided_by="tester",
                decided_at=datetime(2026, 7, 19, tzinfo=UTC),
            )
        )
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.setenv("KNOWLEDGE_LLM_MODEL", "test-model")
    return classification, wiki


def test_four_views_share_canonical_topic_ids(stores):
    classification, wiki = stores

    assert [item.topic_id for item in list_topics(wiki)] == ["T-001"]
    detail = build_topic_detail(wiki, classification, "T-001")
    assert detail.topic.topic_id == "T-001"
    lotcd = build_lotcd_view(wiki, classification, "DRAM", "Spica", "4SA")
    team = build_team_view(wiki, classification, "Yield")
    assert "T-001" in lotcd.topic_ids
    assert "T-001" in team.topic_ids
    assert "T-001" in build_week_view(wiki, "2026-W30").changed_topic_ids


def test_lotcd_projection_has_fixed_sections_and_one_primary_area_membership(stores):
    classification, wiki = stores

    view = build_lotcd_view(
        wiki, classification, "DRAM", "Spica", "4SA"
    )

    assert LOTCD_SECTION_ORDER == (
        "summary",
        "recent_changes",
        "active_topics",
        "knowledge_areas",
        "actions_and_decisions",
        "related_lotcds",
        "closed_topics",
        "activity",
    )
    assert [item.topic_id for item in view.knowledge_areas["yield_defect"]] == [
        "T-001"
    ]
    assert view.active_topics[0].rank_reasons


def test_projection_activity_uses_canonical_topic_agenda_evidence(stores):
    classification, wiki = stores

    lotcd = build_lotcd_view(
        wiki, classification, "DRAM", "Spica", "4SA"
    )
    team = build_team_view(wiki, classification, "Yield")

    assert [item.agenda_id for item in lotcd.activity] == ["A-001", "A-002"]
    assert [item.agenda_id for item in team.recent_activity] == [
        "A-001",
        "A-003",
    ]
    assert lotcd.activity[1].team == "Process"
    assert lotcd.activity[1].week == "2026-W30"
    assert lotcd.activity[1].source_path == "mail/M-002"
    assert team.recent_activity[1].source_quote == "8HBM 수율이 개선됐다."


def test_team_recent_activity_uses_four_week_iso_window_across_year(tmp_path):
    data_dir = tmp_path / "classification_data"
    data_dir.mkdir()
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    source = base["items"]["A-001"]
    for week, agenda_id in (
        ("2025-W48", "A-stale"),
        ("2025-W52", "A-year-end"),
        ("2026-W02", "A-recent"),
        ("2026-W03", "A-latest"),
    ):
        document = {**base, "week": week}
        document["items"] = {
            agenda_id: {
                **source,
                "agenda_id": agenda_id,
                "mail_id": f"M-{agenda_id}",
                "subject": week,
                "source_quote": f"evidence from {week}",
                "classification_context": f"evidence from {week}",
            }
        }
        (data_dir / f"{week}.json").write_text(
            json.dumps(document, ensure_ascii=False), encoding="utf-8"
        )
    rules_path = tmp_path / "classification_rules.json"
    shutil.copy(RULES, rules_path)
    classification = JsonClassificationStore(data_dir, rules_path)
    wiki = JsonWikiStore(tmp_path / "wiki_data")
    value = topic(week="2026-W03").model_copy(
        update={
            "first_seen_week": "2025-W48",
            "target_paths": [path()],
            "teams": ["Yield"],
            "source_agenda_ids": [
                "A-latest",
                "A-recent",
                "A-stale",
                "A-year-end",
            ],
        }
    )
    wiki.publish_topic(value, revision(value))

    view = build_team_view(wiki, classification, "Yield")

    assert [(item.week, item.agenda_id) for item in view.recent_activity] == [
        ("2026-W03", "A-latest"),
        ("2026-W02", "A-recent"),
        ("2025-W52", "A-year-end"),
    ]


def test_pending_relation_review_does_not_block_publication(stores):
    classification, wiki = stores
    wiki.save_review(
        WikiReview(
            review_id="R-relation",
            kind="relation",
            relation_id="REL-001",
        )
    )

    result = build_week(
        "2026-W30",
        classification,
        wiki,
        lambda *_: TopicLinkDecision(
            action="review", rationale="unused", confidence=1
        ),
        fake_analysis,
        fake_draft,
    )

    assert result.status == "published"
    assert result.classification_run_id == "CLASS-001"
    assert wiki.week("2026-W30").build_run_id == result.run_id


def test_build_creates_typed_nonblocking_review_for_relation_proposal(stores):
    classification, wiki = stores
    target = topic("T-002")
    wiki.publish_topic(target, revision(target))

    def analysis_with_relation(_context):
        return fake_analysis(_context).model_copy(
            update={
                "relation_proposals": [
                    RelationProposal(
                        target_topic_id="T-002",
                        kind="possible_cause",
                        agenda_ids=["A-001"],
                        confidence=0.8,
                    )
                ]
            }
        )

    analysis_with_relation.model = "test-model"
    result = build_week(
        "2026-W30",
        classification,
        wiki,
        lambda *_: None,
        analysis_with_relation,
        fake_draft,
    )

    reviews = wiki.reviews("pending")
    assert result.status == "published"
    assert len(reviews) == 1
    assert reviews[0].kind == "relation"
    assert reviews[0].relation_kind == "possible_cause"
    assert reviews[0].relation_agenda_ids == ["A-001"]
    assert reviews[0].relation_id == wiki.relation(reviews[0].relation_id).relation_id
    assert reviews[0].review_id == f"R-{reviews[0].relation_id}"


def test_identical_successful_build_is_reused(stores):
    classification, wiki = stores
    calls = []

    def counting_analysis(context):
        calls.append(context)
        return fake_analysis(context)

    counting_analysis.model = "test-model"
    first = build_week(
        "2026-W30",
        classification,
        wiki,
        lambda *_: None,
        counting_analysis,
        fake_draft,
    )
    second = build_week(
        "2026-W30",
        classification,
        wiki,
        lambda *_: None,
        counting_analysis,
        fake_draft,
    )

    assert second.run_id == first.run_id
    assert len(calls) == 1


def test_build_records_approved_run_taxonomy_version_after_rules_change(stores):
    classification, wiki = stores
    rules = json.loads(classification.rules_path.read_text(encoding="utf-8"))
    rules["version"] = 99
    classification.rules_path.write_text(
        json.dumps(rules, ensure_ascii=False), encoding="utf-8"
    )

    result = build_week(
        "2026-W30",
        classification,
        wiki,
        lambda *_: None,
        fake_analysis,
        fake_draft,
    )

    assert result.classification_run_id == "CLASS-001"
    assert result.taxonomy_version == 1


def test_pending_assignment_review_blocks_publication(stores):
    classification, wiki = stores
    wiki.save_review(
        WikiReview(
            review_id="R-assignment",
            kind="assignment",
            agenda_id="A-001",
        )
    )

    result = build_week(
        "2026-W30",
        classification,
        wiki,
        lambda *_: None,
        fake_analysis,
        fake_draft,
    )

    assert result.status == "review_required"
    assert not (wiki.root / "weeks" / "2026-W30.json").exists()


def test_failed_topic_update_keeps_previous_revision_and_builds_partial(stores):
    classification, wiki = stores
    previous = wiki.topic("T-001").current_revision_id

    def fail_analysis(_context):
        raise ValueError("invalid generated topic")

    fail_analysis.model = "test-model"
    result = build_week(
        "2026-W30",
        classification,
        wiki,
        lambda *_: None,
        fail_analysis,
        fake_draft,
    )

    assert result.status == "partially_failed"
    assert result.failed_topic_ids == ["T-001"]
    assert wiki.topic("T-001").current_revision_id == previous
    assert wiki.week("2026-W30").changed_topic_ids == ["T-001"]


def test_week_revision_is_versioned_from_projection_content(stores):
    _, wiki = stores
    first = build_week_view(wiki, "2026-W30", "RUN-001")
    second = build_week_view(wiki, "2026-W30", "RUN-001")

    assert first.revision_id == second.revision_id
    assert first.build_run_id == "RUN-001"
    assert [item.topic_id for item in first.actions_and_decisions] == ["T-001"]
    assert first.actions_and_decisions[0].title == "4SA 수율 하락"


def test_week_revision_hash_includes_snapshot_action_rows(stores):
    _, wiki = stores
    first = build_week_view(wiki, "2026-W30", "RUN-001")
    changed = topic().model_copy(
        update={"title": "스냅샷 시점 조치", "current_revision_id": "REV-T-001-2"}
    )
    wiki.publish_topic(changed, revision(changed))

    second = build_week_view(wiki, "2026-W30", "RUN-001")

    assert second.actions_and_decisions[0].title == "스냅샷 시점 조치"
    assert second.revision_id != first.revision_id


def fake_analysis(_context):
    return TopicAnalysis(
        title="4SA 수율 하락",
        topic_kind="issue",
        primary_area="yield_defect",
        secondary_areas=[],
        next_state="monitoring",
        importance="high",
        claims=[],
        stale_claims=[],
        relation_proposals=[],
        open_questions=[],
    )


fake_analysis.model = "test-model"


def fake_draft(_context, _analysis):
    return TopicDraft(sections=[])


fake_draft.model = "test-model"
