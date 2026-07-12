from __future__ import annotations

import json

import pytest

import process_agendas


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
