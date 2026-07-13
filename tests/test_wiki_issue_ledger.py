from wiki_issue_ledger import (
    IssueSuggestion,
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
