from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

import process_agendas
from knowledge_models import AliasRecord, CategoryPath, TaxonomyDocument


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"


def test_mail_from_directory_builds_stable_unique_id(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    mail_dir = data_dir / "2026-28" / "Spica수율" / "mail_001"
    mail_dir.mkdir(parents=True)
    (mail_dir / "combined.txt").write_text("4SA 수율 하락", encoding="utf-8")
    (mail_dir / "meta.json").write_text(
        json.dumps(
            {
                "week": "2026-28",
                "team": "Spica수율",
                "subject": "주간 수율",
                "sender": "dummy@example.invalid",
                "received": "2026-07-06T09:00:00+09:00",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(process_agendas, "DATA_DIR", data_dir)

    mail = process_agendas.mail_from_directory(mail_dir)

    assert mail.id == "2026-28:Spica수율:mail_001"
    assert mail.body == "4SA 수율 하락"


def test_process_all_requires_explicit_external_llm_opt_in():
    with pytest.raises(RuntimeError, match="explicit"):
        process_agendas.process_all()


def test_process_all_supplies_active_aliases_to_extraction(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    mail_dir = data_dir / "2026-28" / "Spica수율" / "mail_001"
    mail_dir.mkdir(parents=True)
    (mail_dir / "combined.txt").write_text("SP 24G 수율 하락", encoding="utf-8")
    taxonomy = TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )
    alias = AliasRecord(
        id=7,
        value="SP 24G",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
    )
    store = SimpleNamespace(taxonomy=taxonomy, aliases=lambda: [alias])
    captured = {}

    def capture_extraction(_mail, _splitter, resolver):
        captured["aliases"] = resolver.aliases
        return SimpleNamespace(agendas=[])

    monkeypatch.setattr(process_agendas, "DATA_DIR", data_dir)
    monkeypatch.setattr(process_agendas, "SQLiteKnowledgeStore", lambda _path: store)
    monkeypatch.setattr(process_agendas, "build_splitter", lambda _taxonomy: object())
    monkeypatch.setattr(process_agendas, "extract_mail", capture_extraction)

    process_agendas.process_all(
        allow_external_llm=True,
        allow_dummy_taxonomy=True,
        dry_run=True,
        db_path=tmp_path / "knowledge.db",
    )

    assert captured["aliases"] == [alias]


def test_main_requires_week_for_persisted_workbench_run(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["process_agendas.py"])

    with pytest.raises(SystemExit) as error:
        process_agendas.main()

    assert error.value.code == 2


def test_main_routes_persisted_run_to_weekly_orchestration(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    mail_dir = data_dir / "2026-28" / "Spica" / "mail_001"
    mail_dir.mkdir(parents=True)
    (mail_dir / "combined.txt").write_text("4SA 수율 하락", encoding="utf-8")
    taxonomy = TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    ).model_copy(update={"is_dummy": False})
    store = SimpleNamespace(taxonomy=taxonomy)
    captured = {}

    def capture_run(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            workflow_state="ready_for_approval",
            model_dump=lambda mode=None: {
                "week": "2026-28",
                "workflow_state": "ready_for_approval",
            },
        )

    monkeypatch.setattr(process_agendas, "DATA_DIR", data_dir)
    monkeypatch.setattr(process_agendas, "SQLiteKnowledgeStore", lambda _path: store)
    monkeypatch.setattr(process_agendas, "build_splitter", lambda _taxonomy: "splitter")
    monkeypatch.setattr(process_agendas, "run_week_classification", capture_run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "process_agendas.py",
            "--week", "2026-28",
            "--allow-external-llm",
            "--rerun",
            "--db-path", str(tmp_path / "knowledge.db"),
        ],
    )

    assert process_agendas.main() == 0
    assert captured == {
        "week": "2026-28",
        "store": store,
        "mail_directories": [mail_dir],
        "splitter": "splitter",
        "rerun": True,
    }


def test_main_keeps_dry_run_on_legacy_diagnostic_path(monkeypatch):
    captured = {}

    def capture_legacy(**kwargs):
        captured.update(kwargs)
        return {"processed": 0, "failed": 0, "agendas": 0}

    monkeypatch.setattr(process_agendas, "process_all", capture_legacy)
    monkeypatch.setattr(
        process_agendas,
        "run_week_classification",
        lambda **_kwargs: pytest.fail("persisted orchestration must not run"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "process_agendas.py",
            "--dry-run",
            "--allow-external-llm",
            "--allow-dummy-taxonomy",
        ],
    )

    assert process_agendas.main() == 0
    assert captured["dry_run"] is True
