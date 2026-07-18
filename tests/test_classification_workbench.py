from datetime import UTC, datetime
from pathlib import Path
import sqlite3

import pytest

from agenda_extract import ExtractedAgenda, MailExtractionResult
from classification_workbench import classify_context, lotcd_path
from knowledge_models import (
    AliasRecord,
    CandidateMatch,
    CategoryPath,
    ClassificationDecision,
    Mail,
    TaxonomyDocument,
)
from knowledge_store import SQLiteKnowledgeStore

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"


def taxonomy():
    return TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )


def classified_extraction(status: str = "confirmed"):
    target_path = (
        CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")
        if status == "confirmed"
        else None
    )
    decision = ClassificationDecision(
        status=status,
        target_path=target_path,
        matches=(
            [
                CandidateMatch(
                    phrase="4SA",
                    lotcd="4SA",
                    match_type="canonical",
                    rule_id="canonical:4SA",
                    score=1.0,
                )
            ]
            if target_path
            else []
        ),
        diagnostics=[] if target_path else ["NO_LOTCD_MATCH"],
        confidence=1.0 if target_path else 0.0,
    )
    mail = Mail(
        id="classification-mail",
        subject="classification",
        sender_team="DRAM",
        sender="sender@example.com",
        received_at=datetime(2026, 1, 5, tzinfo=UTC),
        body="4SA 수율 하락",
    )
    agenda = ExtractedAgenda(
        id="classification-agenda",
        mail_id=mail.id,
        source_quote=mail.body,
        source_start=0,
        source_end=len(mail.body),
        classification_context=mail.body,
        summary="4SA 수율 하락",
        scope="lotcd" if target_path else "unknown",
        target_paths=[target_path] if target_path else [],
        candidate_paths=[],
        topic="yield",
        state="investigating",
        item_kind="lotcd_specific",
        decision=decision,
        confidence=decision.confidence,
        review_required=status != "confirmed",
    )
    return mail, MailExtractionResult(mail_id=mail.id, agendas=[agenda])


def test_run_and_trace_survive_reload(tmp_path):
    db_path = tmp_path / "knowledge.db"
    store = SQLiteKnowledgeStore(db_path)
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction()
    store.save_classified_extraction(run.id, mail, result)
    store.finish_classification_run(run.id)

    reloaded = SQLiteKnowledgeStore(db_path)
    summary = reloaded.week_summary("2026-01")
    items = reloaded.classification_items("2026-01")

    assert summary.active_run_id == run.id
    assert len(run.id) == 32
    assert run.taxonomy_version == taxonomy().version
    assert run.alias_version == 1
    assert summary.workflow_state == "ready_for_approval"
    assert summary.counts == {"confirmed": 1}
    assert items[0].agenda_id == result.agendas[0].id
    assert items[0].decision == result.agendas[0].decision


def test_approval_rejects_unresolved_items(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction("unclassified")
    store.save_classified_extraction(run.id, mail, result)
    store.finish_classification_run(run.id)

    with pytest.raises(ValueError, match="unresolved"):
        store.approve_week("2026-01", "reviewer")


def test_failed_run_survives_reload(tmp_path):
    db_path = tmp_path / "knowledge.db"
    store = SQLiteKnowledgeStore(db_path)
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )

    store.fail_classification_run(run.id, "splitter failed")

    reloaded = SQLiteKnowledgeStore(db_path)
    assert reloaded.week_summary("2026-01").workflow_state == "failed"
    assert reloaded.week_summaries()[0].active_run_id == run.id


def test_only_active_processing_run_can_finish(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )

    store.fail_classification_run(run.id, "splitter failed")
    with pytest.raises(ValueError, match="active processing"):
        store.finish_classification_run(run.id)


def test_concurrent_run_start_is_rejected_without_stranding_first_run(tmp_path):
    db_path = tmp_path / "knowledge.db"
    store = SQLiteKnowledgeStore(db_path)
    first = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )

    with pytest.raises(ValueError, match="already processing"):
        store.start_classification_run(
            week="2026-01",
            prompt_version="agenda-v2",
            classifier_version="lotcd-v1",
        )

    with sqlite3.connect(db_path) as connection:
        runs = connection.execute(
            "SELECT id, status FROM classification_run ORDER BY started_at"
        ).fetchall()
    assert runs == [(first.id, "processing")]
    assert store.week_summary("2026-01").active_run_id == first.id


def test_inactive_processing_run_cannot_save_or_move_trace(tmp_path):
    db_path = tmp_path / "knowledge.db"
    store = SQLiteKnowledgeStore(db_path)
    inactive = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO classification_run(
                id, week, status, prompt_version, classifier_version,
                taxonomy_version, alias_version, prior_run_id, started_at
            ) VALUES (?, ?, 'processing', ?, ?, ?, ?, ?, ?)
            """,
            (
                "active-run",
                inactive.week,
                inactive.prompt_version,
                inactive.classifier_version,
                inactive.taxonomy_version,
                inactive.alias_version,
                inactive.id,
                datetime.now(UTC).isoformat(),
            ),
        )
        connection.execute(
            "UPDATE week_classification SET active_run_id = ? WHERE week = ?",
            ("active-run", inactive.week),
        )
    mail, result = classified_extraction()

    with pytest.raises(ValueError, match="active processing"):
        store.save_classified_extraction(inactive.id, mail, result)

    assert store.week_summary("2026-01").active_run_id == "active-run"
    assert store.classification_items("2026-01") == []
    assert mail.id not in store.mails


@pytest.mark.parametrize("workflow_state", ["approved", "revalidation_required"])
def test_protected_week_rejects_rerun_without_losing_provenance(
    tmp_path, workflow_state
):
    db_path = tmp_path / "knowledge.db"
    store = SQLiteKnowledgeStore(db_path)
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction()
    store.save_classified_extraction(run.id, mail, result)
    store.finish_classification_run(run.id)
    store.approve_week("2026-01", "reviewer")
    with sqlite3.connect(db_path) as connection:
        if workflow_state == "revalidation_required":
            connection.execute(
                "UPDATE week_classification SET workflow_state = ? WHERE week = ?",
                (workflow_state, "2026-01"),
            )
        before = connection.execute(
            """
            SELECT active_run_id, workflow_state, approved_at, approved_by
            FROM week_classification WHERE week = ?
            """,
            ("2026-01",),
        ).fetchone()

    with pytest.raises(ValueError, match="cannot start"):
        store.start_classification_run(
            week="2026-01",
            prompt_version="agenda-v2",
            classifier_version="lotcd-v1",
        )

    with sqlite3.connect(db_path) as connection:
        after = connection.execute(
            """
            SELECT active_run_id, workflow_state, approved_at, approved_by
            FROM week_classification WHERE week = ?
            """,
            ("2026-01",),
        ).fetchone()
    assert after == before


def test_empty_run_stays_in_review_and_cannot_be_approved(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )

    summary = store.finish_classification_run(run.id)

    assert summary.workflow_state == "review_in_progress"
    assert summary.counts == {}
    with pytest.raises(ValueError, match="no classification items"):
        store.approve_week("2026-01", "reviewer")


def test_classified_save_is_atomic(tmp_path):
    db_path = tmp_path / "knowledge.db"
    store = SQLiteKnowledgeStore(db_path)
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction()
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_classification_trace
            BEFORE INSERT ON classification_trace
            BEGIN
                SELECT RAISE(ABORT, 'trace failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="trace failure"):
        store.save_classified_extraction(run.id, mail, result)

    assert all(agenda.id != result.agendas[0].id for agenda in store.agendas)
    assert mail.id not in store.mails


def test_approval_records_reviewer_and_timestamp(tmp_path):
    db_path = tmp_path / "knowledge.db"
    store = SQLiteKnowledgeStore(db_path)
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction()
    store.save_classified_extraction(run.id, mail, result)
    store.finish_classification_run(run.id)

    summary = store.approve_week("2026-01", "reviewer")

    with sqlite3.connect(db_path) as connection:
        approved_at, approved_by = connection.execute(
            "SELECT approved_at, approved_by FROM week_classification WHERE week = ?",
            ("2026-01",),
        ).fetchone()
    assert summary.workflow_state == "approved"
    assert approved_at is not None
    assert approved_by == "reviewer"


def test_lotcd_path_derives_upper_hierarchy():
    assert lotcd_path(taxonomy(), "4SA").model_dump() == {
        "domain": "DRAM", "tech": "Spica", "lotcd": "4SA"
    }


def test_single_code_is_confirmed_with_trace():
    decision = classify_context("4SA 수율 하락", "lotcd_specific", taxonomy())
    assert decision.status == "confirmed"
    assert decision.target_path.lotcd == "4SA"
    assert decision.matches[0].rule_id == "canonical:4SA"


def test_group_metric_never_fans_out():
    decision = classify_context(
        "4SA/6SA 수율 종합지수 93.2", "aggregate", taxonomy()
    )
    assert decision.status == "aggregate"
    assert decision.target_path is None


def test_multiple_codes_require_review():
    decision = classify_context(
        "4SA와 6SA 조건 비교", "unknown", taxonomy()
    )
    assert decision.status == "conflict"
    assert decision.target_path is None


def test_single_target_alias_records_rule_id():
    alias = AliasRecord(
        id=7,
        value="SP 24G",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
    )
    result = classify_context(
        "SP 24G Edge defect", "lotcd_specific", taxonomy(), [alias]
    )
    assert result.target_path.lotcd == "4SA"
    assert result.matches[0].rule_id == "alias:7"


def test_group_alias_is_a_conflict_not_a_multi_target():
    alias = AliasRecord(
        id=8,
        value="SP family",
        target_paths=[
            CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA"),
            CategoryPath(domain="DRAM", tech="Spica", lotcd="6SA"),
        ],
    )
    result = classify_context(
        "SP family 품질지수", "unknown", taxonomy(), [alias]
    )
    assert result.status == "conflict"
    assert result.diagnostics == ["ALIAS_COLLISION"]


def test_alias_with_multiple_target_paths_is_a_conflict():
    alias = AliasRecord(
        id=9,
        value="mixed alias",
        target_paths=[
            CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA"),
            CategoryPath(domain="DRAM", tech="Spica", lotcd=None),
        ],
    )

    result = classify_context(
        "mixed alias 품질지수", "unknown", taxonomy(), [alias]
    )

    assert result.status == "conflict"
    assert result.diagnostics == ["ALIAS_COLLISION"]


def test_mixed_valid_and_colliding_aliases_retain_all_matching_phrases():
    aliases = [
        AliasRecord(
            id=10,
            value="SP family",
            target_paths=[
                CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA"),
                CategoryPath(domain="DRAM", tech="Spica", lotcd="6SA"),
            ],
        ),
        AliasRecord(
            id=11,
            value="Edge family",
            target_paths=[
                CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")
            ],
        ),
    ]

    result = classify_context(
        "SP family 및 Edge family 품질지수", "unknown", taxonomy(), aliases
    )

    assert result.status == "conflict"
    assert result.diagnostics == ["ALIAS_COLLISION"]
    assert {match.phrase for match in result.matches} == {
        "SP family", "Edge family"
    }


def test_alias_with_unknown_lotcd_target_requires_review_with_trace():
    alias = AliasRecord(
        id=12,
        value="legacy code",
        target_paths=[
            CategoryPath(domain="DRAM", tech="Spica", lotcd="9ZZ")
        ],
    )

    result = classify_context(
        "legacy code 수율 하락", "lotcd_specific", taxonomy(), [alias]
    )

    assert result.status == "review_required"
    assert result.target_path is None
    assert result.diagnostics == ["UNKNOWN_LOTCD_CODE"]
    assert result.matches[0].rule_id == "alias:12"
    assert result.matches[0].lotcd == "9ZZ"
