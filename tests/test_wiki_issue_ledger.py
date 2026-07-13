from wiki_issue_ledger import (
    IssueStateEvent,
    IssueSuggestion,
    WikiIssue,
    issue_index_definition,
    resolve_issue_ledger,
)


def agenda(
    agenda_id: str,
    week: str,
    topic: str,
    state: str,
    summary: str,
    subject: str = "[Spica] 4SA 주간 수율",
):
    return {
        "agenda_id": agenda_id,
        "mail_id": "mail-" + agenda_id,
        "week": week,
        "updated_week": week,
        "topic": topic,
        "state": state,
        "summary": summary,
        "subject": subject,
        "review_status": "confirmed",
        "confidence": 0.99,
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "source_doc_ids": ["chunk-" + agenda_id],
    }


def test_reply_subject_events_form_one_issue_and_resolve():
    result = resolve_issue_ledger(
        [
            agenda("004", "2026-W28", "yield", "investigating", "수율 1.2%p 하락"),
            agenda(
                "028",
                "2026-W29",
                "root_cause",
                "confirmed",
                "chamber A 원인 확인",
                "RE: [Spica] 4SA 주간 수율",
            ),
            agenda(
                "029",
                "2026-W29",
                "action",
                "in_progress",
                "조건 원복 후 재측정",
                "RE: [Spica] 4SA 주간 수율",
            ),
            agenda(
                "041",
                "2026-W30",
                "yield",
                "resolved",
                "수율 정상화",
                "RE: [Spica] 4SA 주간 수율",
            ),
        ],
        {},
        as_of_week="2026-W30",
    )

    assert len(result.issues) == 1
    issue = next(iter(result.issues.values()))
    assert issue.current_status == "resolved"
    assert issue.agenda_ids == ["004", "028", "029", "041"]
    assert issue.resolved_week == "2026-W30"


def test_later_open_evidence_reopens_resolved_issue():
    resolved = resolve_issue_ledger(
        [
            agenda("004", "2026-W28", "yield", "open", "수율 하락"),
            agenda("041", "2026-W29", "yield", "resolved", "수율 정상화"),
        ],
        {},
        as_of_week="2026-W29",
    )
    reopened = resolve_issue_ledger(
        [agenda("052", "2026-W30", "yield", "open", "수율 재하락")],
        resolved.issues,
        as_of_week="2026-W30",
        suggest=lambda context: IssueSuggestion(
            agenda_id="052",
            issue_id=next(iter(resolved.issues)),
            decision="link",
            confidence="high",
            reason="same LOTCD yield issue reopened after resolution",
        ),
    )

    issue = next(iter(reopened.issues.values()))
    assert issue.current_status == "reopened"
    assert issue.reopened_count == 1


def test_ambiguous_suggestion_creates_provisional_issue_and_review():
    result = resolve_issue_ledger(
        [agenda("099", "2026-W30", "defect", "open", "Edge defect 증가", "공통 개선안")],
        {},
        as_of_week="2026-W30",
        suggest=lambda context: IssueSuggestion(
            agenda_id="099",
            issue_id=None,
            decision="review",
            confidence="low",
            reason="two active defect issues are plausible",
        ),
    )

    issue = next(iter(result.issues.values()))
    assert issue.review_required is True
    assert result.review_items == ["099: two active defect issues are plausible"]


def test_issue_index_is_structured_without_embedding():
    properties = issue_index_definition()["mappings"]["properties"]
    assert properties["state_history"]["type"] == "nested"
    assert properties["agenda_ids"]["type"] == "keyword"
    assert "embedding" not in properties


def existing_issue(
    issue_id: str,
    *,
    path: str = "dram/spica/4sa",
    topic: str = "yield",
    subject: str = "공통 제목",
) -> WikiIssue:
    return WikiIssue(
        issue_id=issue_id,
        category_paths=[path],
        subject_keys=[subject.casefold()],
        topic=topic,
        title=issue_id,
        current_status="ongoing",
        agenda_ids=[issue_id + "-old"],
        state_history=[
            IssueStateEvent(
                week="2026-W28",
                agenda_id=issue_id + "-old",
                state="open",
                event_type="created",
            )
        ],
        first_seen_week="2026-W28",
        last_updated_week="2026-W28",
    )


def test_corrected_agenda_replaces_event_and_recomputes_issue_state():
    initial = resolve_issue_ledger(
        [
            agenda("004", "2026-W28", "yield", "open", "수율 하락"),
            agenda("041", "2026-W29", "yield", "resolved", "수율 정상화"),
        ],
        {},
        as_of_week="2026-W29",
    )
    corrected = {
        **agenda("041", "2026-W29", "yield", "in_progress", "재측정 중"),
        "updated_week": "2026-W30",
    }

    result = resolve_issue_ledger(
        [corrected],
        initial.issues,
        as_of_week="2026-W30",
    )

    issue = next(iter(result.issues.values()))
    assert issue.current_status == "ongoing"
    assert issue.resolved_week is None
    assert issue.agenda_ids == ["004", "041"]
    assert [(event.agenda_id, event.state) for event in issue.state_history] == [
        ("004", "open"),
        ("041", "in_progress"),
    ]
    assert issue.last_updated_week == "2026-W30"


def test_deleted_agenda_is_removed_before_issue_state_is_recomputed():
    initial = resolve_issue_ledger(
        [
            agenda("004", "2026-W28", "yield", "open", "수율 하락"),
            agenda("041", "2026-W29", "yield", "resolved", "수율 정상화"),
        ],
        {},
        as_of_week="2026-W29",
    )
    tombstone = {
        **agenda("041", "2026-W29", "yield", "resolved", "수율 정상화"),
        "updated_week": "2026-W30",
        "is_deleted": True,
    }

    result = resolve_issue_ledger(
        [tombstone],
        initial.issues,
        as_of_week="2026-W30",
    )

    issue = next(iter(result.issues.values()))
    assert issue.current_status == "ongoing"
    assert issue.resolved_week is None
    assert issue.agenda_ids == ["004"]
    assert [event.agenda_id for event in issue.state_history] == ["004"]


def test_deleting_an_issues_only_agenda_marks_the_issue_for_removal():
    initial = resolve_issue_ledger(
        [agenda("004", "2026-W28", "yield", "open", "수율 하락")],
        {},
        as_of_week="2026-W28",
    )
    tombstone = {
        **agenda("004", "2026-W28", "yield", "open", "수율 하락"),
        "updated_week": "2026-W29",
        "is_deleted": True,
    }

    result = resolve_issue_ledger(
        [tombstone],
        initial.issues,
        as_of_week="2026-W29",
    )

    assert result.issues == {}
    assert result.agenda_to_issue == {}
    assert result.deleted_issue_ids == list(initial.issues)


def test_subject_collision_links_only_the_compatible_issue():
    dram = existing_issue("issue-dram")
    nand = existing_issue(
        "issue-nand",
        path="nand/heraion/4h1",
        topic="defect",
    )
    incoming = agenda(
        "099", "2026-W29", "yield", "open", "수율 재하락", "공통 제목"
    )

    result = resolve_issue_ledger(
        [incoming],
        {dram.issue_id: dram, nand.issue_id: nand},
        as_of_week="2026-W29",
    )

    assert result.agenda_to_issue["099"] == "issue-dram"
    assert result.issues["issue-nand"].agenda_ids == ["issue-nand-old"]


def test_multiple_compatible_subject_candidates_require_review_without_llm():
    first = existing_issue("issue-first")
    second = existing_issue("issue-second")
    incoming = agenda(
        "099", "2026-W29", "yield", "open", "수율 재하락", "공통 제목"
    )

    result = resolve_issue_ledger(
        [incoming],
        {first.issue_id: first, second.issue_id: second},
        as_of_week="2026-W29",
    )

    new_issue_id = result.agenda_to_issue["099"]
    assert new_issue_id not in {"issue-first", "issue-second"}
    assert result.issues[new_issue_id].review_required is True
    assert result.review_items == [
        "099: multiple compatible Issues share this subject"
    ]
