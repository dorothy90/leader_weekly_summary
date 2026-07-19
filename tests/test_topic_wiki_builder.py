from datetime import UTC, datetime

import pytest

from knowledge_models import (
    CategoryPath,
    ClassificationDecision,
    ClassificationItem,
    SupportedClaim,
    TopicAssignment,
    TopicSection,
    TopicRevision,
    WikiTopic,
)
from topic_wiki_builder import (
    RelationProposal,
    TopicAnalysis,
    TopicDraft,
    build_analysis_fn,
    build_draft_fn,
    build_week,
    build_topic_revision,
    validate_topic_draft,
)
from wiki_store import JsonWikiStore


def path(lotcd: str = "4SA") -> CategoryPath:
    return CategoryPath(domain="DRAM", tech="Spica", lotcd=lotcd)


def item(
    *,
    agenda_id: str = "A-001",
    lotcd: str = "4SA",
    state_hint: str = "open",
    status: str = "confirmed",
) -> ClassificationItem:
    return ClassificationItem(
        agenda_id=agenda_id,
        mail_id="M-001",
        summary="4SA 수율이 하락했다.",
        source_quote="4SA 수율이 하락했다.",
        classification_context="4SA 수율이 하락했다.",
        item_kind="lotcd_specific",
        decision=ClassificationDecision(
            status=status,
            target_path=path(lotcd),
            confidence=1,
        ),
        revision_count=0,
        team="Yield",
        received_at=datetime(2026, 7, 19, tzinfo=UTC),
        state_hint=state_hint,
    )


def assignment(
    *,
    agenda_id: str = "A-001",
    topic_id: str = "T-001",
) -> TopicAssignment:
    return TopicAssignment(
        agenda_id=agenda_id,
        topic_id=topic_id,
        decision="attach",
        confidence=1,
        rationale="accepted assignment",
        decision_source="manual",
        decided_by="reviewer@example.com",
        decided_at=datetime(2026, 7, 19, tzinfo=UTC),
    )


def topic(*, state: str = "investigating") -> WikiTopic:
    return WikiTopic(
        topic_id="T-001",
        title="4SA 수율 하락",
        topic_kind="issue",
        primary_area="yield_defect",
        state=state,
        importance="high",
        first_seen_week="2026-W29",
        last_updated_week="2026-W29",
        target_paths=[path()],
        teams=["Yield"],
        source_agenda_ids=["A-old"],
        current_revision_id="REV-001",
    )


def analysis(*, state: str = "investigating") -> TopicAnalysis:
    return TopicAnalysis(
        title="4SA 수율 하락",
        topic_kind="issue",
        primary_area="yield_defect",
        secondary_areas=[],
        next_state=state,
        importance="high",
        claims=[],
        stale_claims=[],
        relation_proposals=[],
        open_questions=[],
    )


def draft(*, sections: list[TopicSection] | None = None) -> TopicDraft:
    return TopicDraft(sections=sections or [])


def previous_revision() -> TopicRevision:
    return TopicRevision(
        revision_id="REV-001",
        topic_id="T-001",
        week="2026-W28",
        body_markdown="## 관찰\n\n기존 관찰. [agenda:A-old]",
        sections=[
            TopicSection(
                key="observations",
                title="관찰",
                body="기존 관찰. [agenda:A-old]",
            )
        ],
        claims=[SupportedClaim(text="기존 관찰", agenda_ids=["A-old"])],
        source_agenda_ids=["A-old"],
        created_at=datetime(2026, 7, 12, tzinfo=UTC),
        model="old-model",
    )


def test_unapproved_week_cannot_build(tmp_path):
    class UnapprovedStore:
        def approved_week(self, week):
            raise ValueError(f"Week {week} is not approved")

    with pytest.raises(ValueError, match="not approved"):
        build_week(
            "2026-W30",
            UnapprovedStore(),
            JsonWikiStore(tmp_path / "wiki_data"),
            lambda *_: None,
            lambda *_: None,
            lambda *_: None,
        )


def test_uncited_factual_sentence_is_rejected():
    with pytest.raises(ValueError, match="uncited factual claim"):
        validate_topic_draft(
            draft(
                sections=[
                    TopicSection(
                        key="observations",
                        title="관찰",
                        body="4SA 수율이 하락했다.",
                    )
                ]
            ),
            {"A-001": item()},
        )


def test_resolved_state_requires_terminal_evidence():
    with pytest.raises(ValueError, match="terminal evidence"):
        build_topic_revision(
            topic(state="investigating"),
            [item(state_hint="open")],
            [assignment()],
            analysis(state="resolved"),
            draft(),
        )


def test_unknown_citation_is_rejected():
    cited = draft(
        sections=[
            TopicSection(
                key="observations",
                title="관찰",
                body="수율이 하락했다. [agenda:A-999]",
            )
        ]
    )

    with pytest.raises(ValueError, match="missing Agenda ID: A-999"):
        validate_topic_draft(cited, {"A-001": item()})


def test_each_factual_sentence_requires_its_own_citation():
    cited_once = draft(
        sections=[
            TopicSection(
                key="observations",
                title="관찰",
                body="수율이 하락했다. 불량도 증가했다. [agenda:A-001]",
            )
        ]
    )

    with pytest.raises(ValueError, match="uncited factual claim"):
        validate_topic_draft(cited_once, {"A-001": item()})


def test_prose_after_a_post_punctuation_citation_requires_its_own_citation():
    with pytest.raises(ValueError, match="uncited factual claim: 사실 B"):
        validate_topic_draft(
            draft(
                sections=[
                    TopicSection(
                        key="observations",
                        title="관찰",
                        body="사실 A. [agenda:A-001] 사실 B.",
                    )
                ]
            ),
            {"A-001": item()},
        )


def test_prose_immediately_after_a_citation_requires_its_own_citation():
    with pytest.raises(ValueError, match="uncited factual claim: 사실 B"):
        validate_topic_draft(
            draft(
                sections=[
                    TopicSection(
                        key="observations",
                        title="관찰",
                        body="사실 A. [agenda:A-001]사실 B.",
                    )
                ]
            ),
            {"A-001": item()},
        )


def test_non_citation_brackets_do_not_start_a_claim_boundary():
    validate_topic_draft(
        draft(
            sections=[
                TopicSection(
                    key="observations",
                    title="관찰",
                    body="수율은 [정상 범위] 안이다. [agenda:A-001]",
                )
            ]
        ),
        {"A-001": item()},
    )


@pytest.mark.parametrize(
    "body",
    [
        "사실 A [agenda:A-001]; 사실 B",
        "사실 A [agenda:A-001]；사실 B",
        "사실 A [agenda:A-001]。사실 B",
    ],
)
def test_each_semicolon_or_unicode_delimited_claim_requires_citation(body):
    with pytest.raises(ValueError, match="uncited factual claim: 사실 B"):
        validate_topic_draft(
            draft(
                sections=[
                    TopicSection(key="observations", title="관찰", body=body)
                ]
            ),
            {"A-001": item()},
        )


def test_cross_lotcd_accepted_attachment_extends_topic_path_without_fanout():
    cited = draft(
        sections=[
            TopicSection(
                key="observations",
                title="관찰",
                body="8HBM 수율이 하락했다. [agenda:A-002]",
            )
        ]
    )
    analyzed = analysis().model_copy(
        update={
            "claims": [SupportedClaim(text="8HBM 수율 하락", agenda_ids=["A-002"])]
        }
    )

    updated, revision, _ = build_topic_revision(
        topic(),
        [item(agenda_id="A-002", lotcd="8HBM")],
        [assignment(agenda_id="A-002")],
        analyzed,
        cited,
        model="test-model",
    )

    assert updated.target_paths == [path(), path("8HBM")]
    assert revision.source_agenda_ids == ["A-002"]


def test_unapproved_classification_item_is_rejected():
    with pytest.raises(ValueError, match="unapproved Agenda ID: A-001"):
        build_topic_revision(
            topic(),
            [item(status="review_required")],
            [assignment()],
            analysis(),
            draft(
                sections=[
                    TopicSection(
                        key="observations",
                        title="관찰",
                        body="검토 중인 사실이다. [agenda:A-001]",
                    )
                ]
            ),
            model="test-model",
        )


def test_evidence_requires_an_accepted_assignment_for_this_topic():
    with pytest.raises(ValueError, match="missing accepted assignment: A-001"):
        build_topic_revision(
            topic(),
            [item()],
            [],
            analysis(),
            draft(),
            model="test-model",
        )

    with pytest.raises(ValueError, match="assignment targets another Topic: A-001"):
        build_topic_revision(
            topic(),
            [item()],
            [assignment(topic_id="T-002")],
            analysis(),
            draft(),
            model="test-model",
        )


def test_relation_proposal_requires_an_existing_target_topic():
    analyzed = analysis().model_copy(
        update={
            "relation_proposals": [
                RelationProposal(
                    target_topic_id="T-missing",
                    kind="supports",
                    agenda_ids=["A-001"],
                    confidence=0.8,
                )
            ]
        }
    )

    with pytest.raises(ValueError, match="missing related Topic ID: T-missing"):
        build_topic_revision(
            topic(),
            [item()],
            [assignment()],
            analyzed,
            draft(),
            model="test-model",
            existing_topic_ids={"T-001"},
        )


def test_relation_proposal_requires_explicit_known_topics():
    analyzed = analysis().model_copy(
        update={
            "relation_proposals": [
                RelationProposal(
                    target_topic_id="T-002",
                    kind="supports",
                    agenda_ids=["A-001"],
                    confidence=0.8,
                )
            ]
        }
    )

    with pytest.raises(ValueError, match="existing Topic IDs are required"):
        build_topic_revision(
            topic(),
            [item()],
            [assignment()],
            analyzed,
            draft(),
            model="test-model",
        )


def test_revision_is_rendered_deterministically_and_records_model():
    evidence = item(state_hint="stable")
    analyzed = analysis(state="resolved").model_copy(
        update={
            "claims": [SupportedClaim(text="수율 안정", agenda_ids=["A-001"])],
            "relation_proposals": [
                RelationProposal(
                    target_topic_id="T-002",
                    kind="supports",
                    agenda_ids=["A-001"],
                    confidence=0.8,
                )
            ],
        }
    )
    drafted = draft(
        sections=[
            TopicSection(
                key="current_state",
                title="현재 상태",
                body="수율이 안정되었다. [agenda:A-001]",
            ),
            TopicSection(
                key="observations",
                title="관찰",
                body="저하 추세가 멈췄다. [agenda:A-001]",
            ),
        ]
    )

    updated, revision, relations = build_topic_revision(
        topic(),
        [evidence],
        [assignment()],
        analyzed,
        drafted,
        week="2026-W29",
        model="z-ai/glm-5.2",
        existing_topic_ids={"T-001", "T-002"},
    )

    assert revision.body_markdown == (
        "## 현재 상태\n\n수율이 안정되었다. [agenda:A-001]\n\n"
        "## 관찰\n\n저하 추세가 멈췄다. [agenda:A-001]"
    )
    assert revision.model == "z-ai/glm-5.2"
    assert revision.source_agenda_ids == ["A-001"]
    assert updated.current_revision_id == revision.revision_id
    assert updated.state == "resolved"
    assert updated.source_agenda_ids == ["A-001", "A-old"]
    assert len(relations) == 1
    assert relations[0].review_state == "pending"


def test_revision_sources_include_only_referenced_agendas():
    analyzed = analysis().model_copy(
        update={
            "claims": [SupportedClaim(text="수율 하락", agenda_ids=["A-001"])]
        }
    )
    cited = draft(
        sections=[
            TopicSection(
                key="observations",
                title="관찰",
                body="수율이 하락했다. [agenda:A-001]",
            )
        ]
    )

    _, revision, _ = build_topic_revision(
        topic(),
        [item(), item(agenda_id="A-unused")],
        [assignment(), assignment(agenda_id="A-unused")],
        analyzed,
        cited,
        model="test-model",
    )

    assert revision.source_agenda_ids == ["A-001"]


def test_failed_revision_does_not_change_previous_current_pointer():
    previous = topic()

    with pytest.raises(ValueError, match="missing Agenda ID"):
        build_topic_revision(
            previous,
            [item()],
            [assignment()],
            analysis(),
            draft(
                sections=[
                    TopicSection(
                        key="observations",
                        title="관찰",
                        body="근거가 없다. [agenda:A-404]",
                    )
                ]
            ),
            model="test-model",
        )

    assert previous.current_revision_id == "REV-001"


class FakeStructured:
    def __init__(self, response):
        self.response = response
        self.messages = None

    def invoke(self, messages):
        self.messages = messages
        return self.response


class FakeLlm:
    def __init__(self, responses):
        self.responses = responses
        self.schemas = []

    def with_structured_output(self, schema):
        self.schemas.append(schema)
        return FakeStructured(self.responses[schema])


def test_two_llm_stages_use_structured_outputs_and_expose_connection_model():
    analyzed = analysis()
    drafted = draft()
    llm = FakeLlm({TopicAnalysis: analyzed, TopicDraft: drafted})
    wiki_llm = (llm, "connection-model")

    analyze = build_analysis_fn(wiki_llm)
    write = build_draft_fn(wiki_llm)

    assert analyze({"topic": "context"}) == analyzed
    assert write({"topic": "context"}, analyzed) == drafted
    assert llm.schemas == [TopicAnalysis, TopicDraft]
    assert analyze.model == write.model == "connection-model"


def test_revision_builder_invokes_analysis_then_draft_with_previous_revision():
    seen = []
    analyzed = analysis()
    drafted = draft(
        sections=[
            TopicSection(
                key="observations",
                title="관찰",
                body="수율이 하락했다. [agenda:A-001]",
            )
        ]
    )

    def analyze(context):
        seen.append(("analysis", context["previous_revision"]["revision_id"]))
        return analyzed

    def write(context, value):
        seen.append(("draft", value.title, context["topic"]["topic_id"]))
        return drafted

    analyze.model = "injected-model"
    write.model = "injected-model"

    _, revision, _ = build_topic_revision(
        topic(),
        [item()],
        [assignment()],
        analyze,
        write,
        previous_revision=previous_revision(),
    )

    assert seen == [
        ("analysis", "REV-001"),
        ("draft", "4SA 수율 하락", "T-001"),
    ]
    assert revision.model == "injected-model"


def test_second_week_update_retains_prior_claim_and_citation():
    old = item(agenda_id="A-old")
    new = item(agenda_id="A-new")
    analyzed = analysis().model_copy(update={
        "claims": [SupportedClaim(text="새 관찰", agenda_ids=["A-new"])]
    })
    drafted = draft(sections=[TopicSection(
        key="observations", title="관찰", body="새 관찰. [agenda:A-new]"
    )])

    updated, revision, _ = build_topic_revision(
        topic(), [old, new],
        [assignment(agenda_id="A-old"), assignment(agenda_id="A-new")],
        analyzed, drafted, previous_revision=previous_revision(), model="test-model",
    )

    assert {claim.text for claim in revision.claims} == {"기존 관찰", "새 관찰"}
    assert "[agenda:A-old]" in revision.body_markdown
    assert revision.added_agenda_ids == ["A-new"]
    assert updated.source_agenda_ids == ["A-new", "A-old"]


def test_retention_does_not_resurrect_stale_prose_from_same_section():
    old = item(agenda_id="A-old")
    stale = item(agenda_id="A-stale")
    new = item(agenda_id="A-new")
    previous = previous_revision().model_copy(update={
        "claims": [
            SupportedClaim(text="기존 관찰", agenda_ids=["A-old"]),
            SupportedClaim(text="폐기 관찰", agenda_ids=["A-stale"]),
        ],
        "source_agenda_ids": ["A-old", "A-stale"],
        "sections": [TopicSection(
            key="observations", title="관찰",
            body="기존 관찰. [agenda:A-old] 폐기 관찰. [agenda:A-stale]",
        )],
    })
    analyzed = analysis().model_copy(update={
        "claims": [SupportedClaim(text="새 관찰", agenda_ids=["A-new"])],
        "stale_claims": ["폐기 관찰"],
    })

    _, revision, _ = build_topic_revision(
        topic(), [old, stale, new],
        [assignment(agenda_id=value) for value in ("A-old", "A-stale", "A-new")],
        analyzed,
        draft(sections=[TopicSection(
            key="observations", title="관찰", body="새 관찰. [agenda:A-new]",
        )]),
        previous_revision=previous, model="test-model",
    )

    assert "기존 관찰. [agenda:A-old]" in revision.body_markdown
    assert "폐기 관찰" not in revision.body_markdown
    assert "A-stale" not in revision.body_markdown


def test_old_terminal_evidence_cannot_resolve_or_close_topic():
    old_terminal = item(agenda_id="A-old", state_hint="resolved")
    new_open = item(agenda_id="A-new", state_hint="monitoring")
    for next_state in ("resolved", "closed"):
        with pytest.raises(ValueError, match="new terminal evidence"):
            build_topic_revision(
                topic(), [old_terminal, new_open],
                [assignment(agenda_id="A-old"), assignment(agenda_id="A-new")],
                analysis(state=next_state), draft(),
                previous_revision=previous_revision(), model="test-model",
            )


def test_new_terminal_evidence_can_resolve_and_close_topic():
    for next_state in ("resolved", "closed"):
        _, revision, _ = build_topic_revision(
            topic(), [item(agenda_id="A-old"), item(agenda_id="A-new", state_hint="closed")],
            [assignment(agenda_id="A-old"), assignment(agenda_id="A-new")],
            analysis(state=next_state), draft(),
            previous_revision=previous_revision(), model="test-model",
        )
        assert revision.new_state == next_state


def test_reopened_requires_newer_nonterminal_evidence():
    old = item(agenda_id="A-old", state_hint="closed")
    old = old.model_copy(update={"received_at": datetime(2026, 7, 20, tzinfo=UTC)})
    prior = previous_revision().model_copy(update={"source_agenda_ids": ["A-old"]})
    terminal_topic = topic(state="closed")
    too_old = item(agenda_id="A-new", state_hint="monitoring").model_copy(
        update={"received_at": datetime(2026, 7, 19, tzinfo=UTC)}
    )
    with pytest.raises(ValueError, match="newer nonterminal evidence"):
        build_topic_revision(
            terminal_topic, [old, too_old],
            [assignment(agenda_id="A-old"), assignment(agenda_id="A-new")],
            analysis(state="reopened"), draft(), previous_revision=prior,
            model="test-model",
        )

    newer = too_old.model_copy(update={"received_at": datetime(2026, 7, 21, tzinfo=UTC)})
    _, revision, _ = build_topic_revision(
        terminal_topic, [old, newer],
        [assignment(agenda_id="A-old"), assignment(agenda_id="A-new")],
        analysis(state="reopened"), draft(), previous_revision=prior,
        model="test-model",
    )
    assert revision.new_state == "reopened"


def test_added_agendas_are_derived_from_previous_revision_sources():
    _, revision, _ = build_topic_revision(
        topic(), [item(agenda_id="A-old"), item(agenda_id="A-new")],
        [assignment(agenda_id="A-old"), assignment(agenda_id="A-new")],
        analysis(), draft(), previous_revision=previous_revision(),
        model="test-model",
    )
    assert revision.added_agenda_ids == ["A-new"]
