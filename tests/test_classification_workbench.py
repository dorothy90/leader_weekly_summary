from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from agenda_extract import (
    AgendaDraft,
    AgendaDraftList,
    ExtractedAgenda,
    MailExtractionResult,
)
from classification_workbench import (
    classify_context,
    lotcd_path,
    run_week_classification,
)
from knowledge_models import (
    AliasRecord,
    CandidateMatch,
    CategoryPath,
    ClassificationDecision,
    Mail,
    ItemSplitPart,
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


def store_with_completed_run(tmp_path, status: str = "unclassified"):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction(status)
    store.save_classified_extraction(run.id, mail, result)
    store.finish_classification_run(run.id)
    return store, run, result.agendas[0]


def protected_item_snapshot(store, agenda_id):
    with store._connect() as connection:
        return {
            "agenda": connection.execute(
                "SELECT * FROM agenda WHERE id = ?", (agenda_id,)
            ).fetchone(),
            "targets": connection.execute(
                "SELECT * FROM agenda_target WHERE agenda_id = ? ORDER BY category_id",
                (agenda_id,),
            ).fetchall(),
            "trace": connection.execute(
                "SELECT * FROM classification_trace WHERE agenda_id = ?",
                (agenda_id,),
            ).fetchone(),
            "week": connection.execute(
                "SELECT * FROM week_classification WHERE week = '2026-01'"
            ).fetchone(),
            "revision_count": connection.execute(
                "SELECT COUNT(*) FROM classification_revision WHERE agenda_id = ?",
                (agenda_id,),
            ).fetchone()[0],
        }


def orchestration_mail_directory(tmp_path, monkeypatch):
    import process_agendas

    data_dir = tmp_path / "data"
    mail_dir = data_dir / "2026-01" / "Spica" / "mail_001"
    mail_dir.mkdir(parents=True)
    (mail_dir / "combined.txt").write_text("4SA 수율 하락", encoding="utf-8")
    (mail_dir / "meta.json").write_text(
        json.dumps(
            {
                "week": "2026-01",
                "team": "Spica",
                "subject": "주간 수율",
                "sender": "sender@example.com",
                "received": "2026-01-05T09:00:00+09:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(process_agendas, "DATA_DIR", data_dir)
    return mail_dir


def successful_splitter(text):
    return AgendaDraftList(
        agendas=[
            AgendaDraft(
                source_quote=text,
                classification_context=text,
                summary="4SA 수율 하락",
                topic="yield",
                state="investigating",
                item_kind="lotcd_specific",
            )
        ]
    )


def test_run_week_classification_persists_one_mail_trace(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    mail_dir = orchestration_mail_directory(tmp_path, monkeypatch)

    summary = run_week_classification(
        week="2026-01",
        store=store,
        mail_directories=[mail_dir],
        splitter=successful_splitter,
    )

    assert summary.workflow_state == "ready_for_approval"
    assert summary.counts == {"confirmed": 1}
    with store._connect() as connection:
        trace = connection.execute(
            "SELECT * FROM classification_trace WHERE run_id = ?",
            (summary.active_run_id,),
        ).fetchone()
    assert trace["decision_status"] == "confirmed"
    assert trace["prompt_version"] == "agenda-v2"


def test_splitter_failure_records_structured_error_and_preserves_prior_run(
    tmp_path, monkeypatch
):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    mail_dir = orchestration_mail_directory(tmp_path, monkeypatch)
    prior = run_week_classification(
        week="2026-01",
        store=store,
        mail_directories=[mail_dir],
        splitter=successful_splitter,
    )

    def failing_splitter(_text):
        raise RuntimeError("structured output failed")

    failed = run_week_classification(
        week="2026-01",
        store=store,
        mail_directories=[mail_dir],
        splitter=failing_splitter,
    )

    assert failed.workflow_state == "failed"
    with store._connect() as connection:
        failed_run = connection.execute(
            "SELECT * FROM classification_run WHERE id = ?",
            (failed.active_run_id,),
        ).fetchone()
        prior_run = connection.execute(
            "SELECT * FROM classification_run WHERE id = ?",
            (prior.active_run_id,),
        ).fetchone()
        prior_trace_count = connection.execute(
            "SELECT COUNT(*) FROM classification_trace WHERE run_id = ?",
            (prior.active_run_id,),
        ).fetchone()[0]
    assert failed_run["status"] == "failed"
    assert failed_run["prior_run_id"] == prior.active_run_id
    assert json.loads(failed_run["error"]) == {
        "stage": "extract_mail",
        "mail_directory": str(mail_dir),
        "exception_type": "RuntimeError",
        "message": "structured output failed",
    }
    assert prior_run["status"] == "completed"
    assert prior_trace_count == 1


def test_run_week_classification_rejects_ordinary_revalidation_run(
    tmp_path, monkeypatch
):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    mail_dir = orchestration_mail_directory(tmp_path, monkeypatch)
    run_week_classification(
        week="2026-01",
        store=store,
        mail_directories=[mail_dir],
        splitter=successful_splitter,
    )
    store.approve_week("2026-01", "reviewer")
    with store._connect() as connection:
        connection.execute(
            "UPDATE week_classification SET workflow_state = 'revalidation_required'"
        )

    with pytest.raises(ValueError, match="revalidation_required"):
        run_week_classification(
            week="2026-01",
            store=store,
            mail_directories=[mail_dir],
            splitter=successful_splitter,
        )


def test_explicit_revalidation_rerun_links_prior_and_retains_traces(
    tmp_path, monkeypatch
):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    mail_dir = orchestration_mail_directory(tmp_path, monkeypatch)
    prior = run_week_classification(
        week="2026-01",
        store=store,
        mail_directories=[mail_dir],
        splitter=successful_splitter,
    )
    store.approve_week("2026-01", "reviewer")
    with store._connect() as connection:
        connection.execute(
            "UPDATE week_classification SET workflow_state = 'revalidation_required'"
        )

    rerun = run_week_classification(
        week="2026-01",
        store=store,
        mail_directories=[mail_dir],
        splitter=successful_splitter,
        rerun=True,
    )

    with store._connect() as connection:
        row = connection.execute(
            "SELECT prior_run_id FROM classification_run WHERE id = ?",
            (rerun.active_run_id,),
        ).fetchone()
        trace_runs = connection.execute(
            "SELECT run_id FROM classification_trace ORDER BY run_id"
        ).fetchall()
        week = connection.execute(
            "SELECT approved_at, approved_by FROM week_classification"
        ).fetchone()
    assert row["prior_run_id"] == prior.active_run_id
    assert {trace["run_id"] for trace in trace_runs} == {
        prior.active_run_id,
        rerun.active_run_id,
    }
    assert week["approved_at"] is None
    assert week["approved_by"] is None


def test_compare_runs_reports_changed_added_removed_and_unchanged(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    old_run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction("confirmed")
    changed = result.agendas[0].model_copy(
        update={
            "id": "changed",
            "decision": result.agendas[0].decision.model_copy(
                update={
                    "target_path": CategoryPath(
                        domain="DRAM", tech="Spica", lotcd="6SA"
                    )
                }
            ),
            "target_paths": [
                CategoryPath(domain="DRAM", tech="Spica", lotcd="6SA")
            ],
        }
    )
    unchanged = result.agendas[0].model_copy(update={"id": "unchanged"})
    removed = result.agendas[0].model_copy(update={"id": "removed"})
    store.save_classified_extraction(
        old_run.id,
        mail,
        result.model_copy(update={"agendas": [changed, unchanged, removed]}),
    )
    store.finish_classification_run(old_run.id)

    new_run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    changed = result.agendas[0].model_copy(update={"id": "changed"})
    unchanged = result.agendas[0].model_copy(update={"id": "unchanged"})
    added = result.agendas[0].model_copy(update={"id": "added"})
    store.save_classified_extraction(
        new_run.id,
        mail,
        result.model_copy(update={"agendas": [changed, unchanged, added]}),
    )
    store.finish_classification_run(new_run.id)

    comparison = store.compare_runs(old_run.id, new_run.id)

    assert comparison.unchanged_count == 1
    assert [change.agenda_id for change in comparison.changed] == [
        "added", "changed", "removed"
    ]
    changes = {change.agenda_id: change for change in comparison.changed}
    assert changes["changed"].before_status == "confirmed"
    assert changes["changed"].after_status == "confirmed"
    assert changes["changed"].before_lotcd == "6SA"
    assert changes["changed"].after_lotcd == "4SA"
    assert changes["added"].before_status is None
    assert changes["added"].before_lotcd is None
    assert changes["removed"].after_status is None
    assert changes["removed"].after_lotcd is None


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


def test_manual_correction_records_one_lotcd_revision_and_readiness(tmp_path):
    store, _, agenda = store_with_completed_run(tmp_path)

    item = store.correct_classification(
        agenda.id, "4SA", "reviewer", "원문 확인"
    )

    assert item.decision.status == "manually_corrected"
    assert item.decision.target_path.lotcd == "4SA"
    assert item.decision.confidence == 1.0
    revision = store.revisions(agenda.id)[0]
    assert revision.changed_by == "reviewer"
    assert revision.reason == "원문 확인"
    assert store.week_summary("2026-01").workflow_state == "ready_for_approval"


@pytest.mark.parametrize("workflow_state", ["approved", "revalidation_required"])
@pytest.mark.parametrize("action", ["correct", "disposition", "split"])
def test_protected_week_rejects_item_mutations_without_changing_data(
    tmp_path, workflow_state, action
):
    store, _, agenda = store_with_completed_run(tmp_path, "confirmed")
    store.approve_week("2026-01", "approver")
    if workflow_state == "revalidation_required":
        with sqlite3.connect(store.db_path) as connection:
            connection.execute(
                "UPDATE week_classification SET workflow_state = ? WHERE week = ?",
                (workflow_state, "2026-01"),
            )
    before = protected_item_snapshot(store, agenda.id)

    with pytest.raises(ValueError, match=workflow_state):
        if action == "correct":
            store.correct_classification(agenda.id, "6SA", "reviewer", "reason")
        elif action == "disposition":
            store.set_item_disposition(
                agenda.id, "excluded", "reviewer", "reason"
            )
        else:
            store.split_classification_item(
                agenda.id,
                [
                    ItemSplitPart(
                        source_quote="4SA", summary="first", lotcd="4SA"
                    ),
                    ItemSplitPart(
                        source_quote="수율 하락", summary="second", lotcd="6SA"
                    ),
                ],
                "reviewer",
                "reason",
            )

    assert protected_item_snapshot(store, agenda.id) == before


def test_obsolete_run_item_rejects_correction_without_changing_data(tmp_path):
    store, _, agenda = store_with_completed_run(tmp_path, "confirmed")
    replacement = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    before = protected_item_snapshot(store, agenda.id)

    with pytest.raises(ValueError, match="active run"):
        store.correct_classification(agenda.id, "6SA", "reviewer", "reason")

    assert store.week_summary("2026-01").active_run_id == replacement.id
    assert protected_item_snapshot(store, agenda.id) == before


def test_processing_run_item_rejects_disposition_without_changing_data(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction("confirmed")
    store.save_classified_extraction(run.id, mail, result)
    agenda = result.agendas[0]
    before = protected_item_snapshot(store, agenda.id)

    with pytest.raises(ValueError, match="processing"):
        store.set_item_disposition(agenda.id, "excluded", "reviewer", "reason")

    assert protected_item_snapshot(store, agenda.id) == before


@pytest.mark.parametrize("workflow_state", ["approved", "revalidation_required"])
def test_readiness_recalculation_does_not_overwrite_protected_state(
    tmp_path, workflow_state
):
    store, run, _ = store_with_completed_run(tmp_path, "confirmed")
    store.approve_week("2026-01", "approver")
    with store._connect() as connection:
        connection.execute(
            "UPDATE week_classification SET workflow_state = ? WHERE week = ?",
            (workflow_state, "2026-01"),
        )
        before = connection.execute(
            "SELECT * FROM week_classification WHERE week = '2026-01'"
        ).fetchone()
        store._recalculate_week_readiness(connection, run.id)
        after = connection.execute(
            "SELECT * FROM week_classification WHERE week = '2026-01'"
        ).fetchone()

    assert after == before


@pytest.mark.parametrize(
    ("status", "diagnostics"),
    [("aggregate", ["AGGREGATE_METRIC"]), ("excluded", [])],
)
def test_item_disposition_clears_targets_and_recalculates_readiness(
    tmp_path, status, diagnostics
):
    store, _, agenda = store_with_completed_run(tmp_path, "confirmed")

    item = store.set_item_disposition(
        agenda.id, status, "reviewer", "not LOTCD-specific"
    )

    assert item.decision.status == status
    assert item.decision.target_path is None
    assert item.decision.diagnostics == diagnostics
    assert store.week_summary("2026-01").workflow_state == "ready_for_approval"


def test_split_is_deterministic_and_excludes_original(tmp_path):
    store, run, agenda = store_with_completed_run(tmp_path)
    parts = [
        ItemSplitPart(source_quote="4SA", summary="first", lotcd="4SA"),
        ItemSplitPart(source_quote="수율 하락", summary="second", lotcd="6SA"),
    ]

    children = store.split_classification_item(
        agenda.id, parts, "reviewer", "two independent items"
    )

    expected_ids = [
        hashlib.sha256(
            "\0".join((agenda.id, part.source_quote, part.lotcd)).encode()
        ).hexdigest()
        for part in parts
    ]
    assert [child.agenda_id for child in children] == expected_ids
    assert {child.decision.target_path.lotcd for child in children} == {"4SA", "6SA"}
    assert all(child.decision.status == "manually_corrected" for child in children)
    items = {item.agenda_id: item for item in store.classification_items("2026-01")}
    assert items[agenda.id].decision.status == "excluded"
    assert set(expected_ids) <= items.keys()
    assert all(items[child_id].revision_count == 0 for child_id in expected_ids)
    assert store.revisions(agenda.id)[0].reason == "two independent items"
    assert store.week_summary("2026-01").workflow_state == "ready_for_approval"
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM classification_trace WHERE run_id = ?", (run.id,)
        ).fetchone()[0] == 3


def test_split_offsets_are_anchored_to_original_context_occurrence(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail, result = classified_extraction("unclassified")
    repeated = "4SA 수율 하락"
    mail = mail.model_copy(update={"body": f"{repeated}\nignore\n{repeated}"})
    second_start = mail.body.rfind(repeated)
    agenda = result.agendas[0].model_copy(
        update={
            "source_quote": repeated,
            "source_start": second_start,
            "source_end": second_start + len(repeated),
            "classification_context": repeated,
        }
    )
    result = result.model_copy(update={"agendas": [agenda]})
    store.save_classified_extraction(run.id, mail, result)
    store.finish_classification_run(run.id)

    children = store.split_classification_item(
        agenda.id,
        [
            ItemSplitPart(source_quote="4SA", summary="first", lotcd="4SA"),
            ItemSplitPart(
                source_quote="수율 하락", summary="second", lotcd="6SA"
            ),
        ],
        "reviewer",
        "split repeated context",
    )

    with sqlite3.connect(store.db_path) as connection:
        offsets = {
            row[0]: row[1]
            for row in connection.execute(
                "SELECT id, source_start FROM agenda WHERE id IN (?, ?)",
                (children[0].agenda_id, children[1].agenda_id),
            )
        }
    assert offsets[children[0].agenda_id] == second_start
    assert offsets[children[1].agenda_id] == second_start + repeated.index("수율 하락")


def test_revision_snapshots_exclude_derived_revision_count(tmp_path):
    store, _, agenda = store_with_completed_run(tmp_path)

    store.correct_classification(agenda.id, "4SA", "reviewer", "reason")

    revision = store.revisions(agenda.id)[0]
    assert "revision_count" not in revision.before
    assert "revision_count" not in revision.after


@pytest.mark.parametrize(
    "parts, message",
    [
        ([ItemSplitPart(source_quote="4SA", summary="only", lotcd="4SA")], "two"),
        (
            [
                ItemSplitPart(source_quote="4SA 수율", summary="first", lotcd="4SA"),
                ItemSplitPart(source_quote="수율 하락", summary="second", lotcd="6SA"),
            ],
            "overlap",
        ),
        (
            [
                ItemSplitPart(source_quote="4SA", summary="first", lotcd="4SA"),
                ItemSplitPart(source_quote="수율 하락", summary="second", lotcd="9ZZ"),
            ],
            "Unknown LOTCD",
        ),
        (
            [
                ItemSplitPart(source_quote="4SA", summary="first", lotcd="4SA"),
                ItemSplitPart(source_quote="4SA", summary="duplicate", lotcd="4SA"),
            ],
            "duplicate child IDs",
        ),
        (
            [
                ItemSplitPart(source_quote="4SA", summary="first", lotcd="4SA"),
                ItemSplitPart(source_quote="missing", summary="second", lotcd="6SA"),
            ],
            "classification_context",
        ),
    ],
)
def test_invalid_split_is_atomic(tmp_path, parts, message):
    store, run, agenda = store_with_completed_run(tmp_path)
    before = store.classification_items("2026-01")

    with pytest.raises(ValueError, match=message):
        store.split_classification_item(agenda.id, parts, "reviewer", "invalid")

    assert store.classification_items("2026-01") == before
    assert store.revisions(agenda.id) == []
    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM classification_trace WHERE run_id = ?", (run.id,)
        ).fetchone()[0] == 1


@pytest.mark.parametrize("mutation", ["create", "update", "delete", "learned"])
def test_every_alias_mutation_bumps_version_and_revalidates_approved_week(
    tmp_path, mutation
):
    store, run, _ = store_with_completed_run(tmp_path, "confirmed")
    original = store.create_alias(
        "existing alias",
        [CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        "setup",
    )
    store.approve_week("2026-01", "approver")
    if mutation == "create":
        store.create_alias(
            "new alias",
            [CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
            "reviewer",
        )
    elif mutation == "update":
        store.update_alias(
            original.id,
            "updated alias",
            [CategoryPath(domain="DRAM", tech="Spica", lotcd="6SA")],
            "reviewer",
        )
    elif mutation == "delete":
        store.delete_alias(original.id, "reviewer")
    else:
        learned = store.create_learned_alias(
            value="SP 24G",
            lotcd="4SA",
            origin_agenda_id="classification-agenda",
            changed_by="reviewer",
            context_domain="DRAM",
            context_tech="Spica",
        )
        assert learned.origin_agenda_id == "classification-agenda"
        assert learned.context_domain == "DRAM"
        assert learned.context_tech == "Spica"

    with sqlite3.connect(store.db_path) as connection:
        alias_version = int(connection.execute(
            "SELECT value FROM knowledge_meta WHERE key = 'alias_version'"
        ).fetchone()[0])
        week = connection.execute(
            "SELECT active_run_id, workflow_state, approved_at, approved_by "
            "FROM week_classification WHERE week = '2026-01'"
        ).fetchone()
        trace_count = connection.execute(
            "SELECT COUNT(*) FROM classification_trace WHERE run_id = ?", (run.id,)
        ).fetchone()[0]
    assert alias_version == 3
    assert week[0] == run.id
    assert week[1] == "revalidation_required"
    assert week[2] is not None
    assert week[3] == "approver"
    assert trace_count == 1


def test_alias_mutation_and_impact_roll_back_together(tmp_path):
    store, _, _ = store_with_completed_run(tmp_path, "confirmed")
    store.approve_week("2026-01", "approver")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_revalidation
            BEFORE UPDATE OF workflow_state ON week_classification
            WHEN NEW.workflow_state = 'revalidation_required'
            BEGIN
                SELECT RAISE(ABORT, 'impact failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="impact failure"):
        store.create_learned_alias(
            value="rollback alias",
            lotcd="4SA",
            origin_agenda_id="classification-agenda",
            changed_by="reviewer",
        )

    with sqlite3.connect(store.db_path) as connection:
        assert connection.execute(
            "SELECT value FROM knowledge_meta WHERE key = 'alias_version'"
        ).fetchone()[0] == "1"
        assert connection.execute(
            "SELECT workflow_state FROM week_classification"
        ).fetchone()[0] == "approved"
        assert connection.execute(
            "SELECT COUNT(*) FROM alias WHERE value = 'rollback alias'"
        ).fetchone()[0] == 0


def test_contextual_alias_requires_matching_taxonomy_context():
    alias = AliasRecord(
        id=13,
        value="legacy edge",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        context_domain="DRAM",
        context_tech="Spica",
    )

    no_context = classify_context(
        "legacy edge defect", "lotcd_specific", taxonomy(), [alias], "Quality"
    )
    sender_context = classify_context(
        "legacy edge defect", "lotcd_specific", taxonomy(), [alias], "Spica team"
    )

    assert no_context.status == "unclassified"
    assert sender_context.target_path.lotcd == "4SA"
