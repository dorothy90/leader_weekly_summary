import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from knowledge_models import (
    CategoryPath,
    ClassificationDecision,
    ClassificationItem,
    WikiReviewResolution,
    WikiTopic,
)
from topic_linker import (
    TopicLinkDecision,
    link_agenda,
    persist_link_proposal,
    rank_topic_candidates,
    resolve_wiki_review,
)
from wiki_store import JsonWikiStore


FIXTURE = Path(__file__).parents[1] / "fixtures" / "wiki" / "expected-topic-links.json"


def path(lotcd: str | None = "4SA") -> CategoryPath:
    return CategoryPath(domain="DRAM", tech="Spica", lotcd=lotcd)


def item(
    summary: str,
    lotcd: str | None = "4SA",
    *,
    kind: str = "lotcd_specific",
    topic_hint: str = "",
) -> ClassificationItem:
    status = "aggregate" if kind == "aggregate" else "confirmed"
    return ClassificationItem(
        agenda_id="A-001",
        mail_id="M-001",
        summary=summary,
        source_quote=summary,
        classification_context="",
        item_kind=kind,
        decision=ClassificationDecision(
            status=status,
            target_path=path(lotcd),
            confidence=1,
        ),
        revision_count=0,
        topic_hint=topic_hint,
    )


def topic(
    title: str,
    lotcd: str = "4SA",
    *,
    topic_id: str = "T-001",
) -> WikiTopic:
    return WikiTopic(
        topic_id=topic_id,
        title=title,
        topic_kind="issue",
        primary_area="yield_defect",
        state="investigating",
        importance="high",
        first_seen_week="2026-W29",
        last_updated_week="2026-W29",
        target_paths=[path(lotcd)],
        teams=["Yield"],
        source_agenda_ids=["A-old"],
        current_revision_id="REV-001",
    )


def decision(action: str, topic_id: str | None = None) -> TopicLinkDecision:
    return TopicLinkDecision(
        action=action,
        topic_id=topic_id,
        title="새 Topic" if action == "create" else None,
        rationale="structured decision",
        confidence=0.9,
    )


def fake_decider(action: str, topic_id: str | None = None):
    return lambda _item, _topics: decision(action, topic_id)


def test_same_lotcd_and_terms_rank_existing_topic_first():
    candidates = rank_topic_candidates(
        item("4SA D1 불량 감소"),
        [
            topic("4SA D1 불량 증가"),
            topic("4SA 출하 일정", topic_id="T-002"),
        ],
    )

    assert candidates[0].topic_id == "T-001"
    assert candidates[0].rank_reasons


def test_ranking_filters_incompatible_lotcds_and_breaks_ties_by_topic_id():
    candidates = rank_topic_candidates(
        item("공통 표현"),
        [
            topic("공통 표현", topic_id="T-002"),
            topic("공통 표현", topic_id="T-001"),
            topic("공통 표현", "8HBM", topic_id="T-000"),
        ],
    )

    assert [candidate.topic_id for candidate in candidates] == ["T-001", "T-002"]


def test_no_plausible_candidate_creates_without_calling_decider():
    def unexpected_decider(_item, _topics):  # pragma: no cover - must not run
        raise AssertionError("decider should not run")

    proposal = link_agenda(item("신규 고객 요청", "4SA"), [], unexpected_decider)

    assert proposal.action == "create"
    assert proposal.topic_id is not None


def test_same_lotcd_alone_does_not_auto_attach():
    proposal = link_agenda(
        item("4SA 출하 일정"),
        [topic("4SA D1 불량")],
        fake_decider("attach", "T-001"),
    )

    assert proposal.action == "review"


def test_high_score_and_clear_margin_allows_requested_attach():
    proposal = link_agenda(
        item("D1 불량 증가", topic_hint="D1 불량"),
        [topic("D1 불량 증가")],
        fake_decider("attach", "T-001"),
    )

    assert (proposal.action, proposal.topic_id) == ("attach", "T-001")


def test_close_candidate_scores_force_review():
    proposal = link_agenda(
        item("D1 불량 증가", topic_hint="D1 불량"),
        [topic("D1 불량 증가"), topic("D1 불량 증가", topic_id="T-002")],
        fake_decider("attach", "T-001"),
    )

    assert proposal.action == "review"


def test_structured_create_decision_creates_distinct_topic():
    proposal = link_agenda(
        item("D1 불량 증가", topic_hint="D1 불량"),
        [topic("D1 불량 증가")],
        fake_decider("create"),
    )

    assert proposal.action == "create"
    assert proposal.topic_id != "T-001"


def test_aggregate_item_against_lotcd_topic_forces_review():
    proposal = link_agenda(
        item("Spica D1 불량", None, kind="aggregate", topic_hint="D1 불량"),
        [topic("D1 불량 증가")],
        fake_decider("attach", "T-001"),
    )

    assert proposal.action == "review"


def test_low_overlap_aggregate_against_lotcd_topic_forces_review():
    proposal = link_agenda(
        item("Spica 종합 출하 계획", None, kind="aggregate"),
        [topic("D1 불량 증가")],
        fake_decider("create"),
    )

    assert proposal.candidates[0].score < 2.0
    assert proposal.action == "review"


def test_decider_receives_only_the_five_ranked_topics():
    seen = []
    topics = [topic("D1 불량", topic_id=f"T-{number:03}") for number in range(8)]

    def recording_decider(_item, candidates):
        seen.extend(candidates)
        return decision("review")

    link_agenda(item("D1 불량", topic_hint="D1"), topics, recording_decider)

    assert len(seen) == 5
    assert [value.topic_id for value in seen] == [f"T-{number:03}" for number in range(5)]


def test_proposal_persistence_and_manual_review_resolution(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    agenda = item("4SA 출하 일정")
    proposal = link_agenda(
        agenda,
        [topic("4SA D1 불량")],
        fake_decider("attach", "T-001"),
    )

    persisted = persist_link_proposal(store, agenda, proposal)

    assert persisted.kind == "assignment"
    assert store.assignment(agenda.agenda_id) is None
    resolved = resolve_wiki_review(
        store,
        persisted.review_id,
        WikiReviewResolution(action="attach", topic_id="T-001"),
        "operator@example.com",
    )
    assignment = store.assignment(agenda.agenda_id)
    assert resolved.status == "resolved"
    assert assignment is not None
    assert (assignment.topic_id, assignment.decision_source, assignment.decided_by) == (
        "T-001",
        "manual",
        "operator@example.com",
    )
    assert assignment.decided_at <= datetime.now(UTC)


def test_hold_resolution_keeps_review_blocking_and_unassigned(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    agenda = item("4SA 출하 일정")
    proposal = link_agenda(
        agenda,
        [topic("4SA D1 불량")],
        fake_decider("attach", "T-001"),
    )
    review = persist_link_proposal(store, agenda, proposal)

    held = resolve_wiki_review(
        store,
        review.review_id,
        WikiReviewResolution(action="hold"),
        "operator@example.com",
    )

    assert held.status == "held"
    assert store.assignment(agenda.agenda_id) is None


def test_manual_create_rejects_caller_supplied_topic_id(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    agenda = item("4SA 출하 일정")
    proposal = link_agenda(
        agenda,
        [topic("4SA D1 불량")],
        fake_decider("attach", "T-001"),
    )
    review = persist_link_proposal(store, agenda, proposal)

    with pytest.raises(ValueError, match="must not supply topic_id"):
        resolve_wiki_review(
            store,
            review.review_id,
            WikiReviewResolution(
                action="create", topic_id="T-CALLER", title="새 Topic"
            ),
            "operator@example.com",
        )

    assert store.assignment(agenda.agenda_id) is None
    assert store.reviews()[0].status == "pending"


def test_manual_create_always_uses_stable_new_topic_id(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    agenda = item("4SA 출하 일정")
    expected_topic_id = link_agenda(agenda, [], fake_decider("review")).topic_id
    proposal = link_agenda(
        agenda,
        [topic("4SA D1 불량")],
        fake_decider("attach", "T-001"),
    )
    review = persist_link_proposal(store, agenda, proposal)

    resolve_wiki_review(
        store,
        review.review_id,
        WikiReviewResolution(action="create", title="새 Topic"),
        "operator@example.com",
    )

    assignment = store.assignment(agenda.agenda_id)
    assert assignment is not None
    assert assignment.topic_id == expected_topic_id


def test_gold_fixture_has_zero_incorrect_auto_merges():
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    existing = [topic(**record) for record in fixture["topics"]]
    incorrect = []
    for record in fixture["cases"]:
        proposal = link_agenda(
            item(
                record["summary"],
                record["lotcd"],
                kind=record.get("item_kind", "lotcd_specific"),
                topic_hint=record.get("topic_hint", ""),
            ),
            existing,
            fake_decider(record["decision"], record.get("decision_topic_id")),
        )
        if proposal.action == "attach" and proposal.topic_id != record.get("expected_topic_id"):
            incorrect.append(record["name"])
        assert proposal.action == record["expected_action"]

    assert incorrect == []
