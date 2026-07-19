import json
import process_agendas

def test_mail_from_directory_builds_stable_id(tmp_path, monkeypatch):
    root = tmp_path / "data"
    directory = root / "2026-28" / "Spica수율" / "mail_001"
    directory.mkdir(parents=True)
    (directory / "combined.txt").write_text("4SA 수율 하락", encoding="utf-8")
    (directory / "meta.json").write_text(json.dumps({
        "week": "2026-28", "team": "Spica수율", "subject": "주간 수율",
        "sender": "demo@example.invalid", "received": "2026-07-06T09:00:00+09:00",
    }), encoding="utf-8")
    monkeypatch.setattr(process_agendas, "DATA_DIR", root)
    mail = process_agendas.mail_from_directory(directory)
    assert mail.id == "2026-28:Spica수율:mail_001"
    assert mail.body == "4SA 수율 하락"
    assert mail.source_path == "data/2026-28/Spica수율/mail_001/combined.txt"
