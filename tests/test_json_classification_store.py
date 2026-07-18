import json
import shutil
from pathlib import Path

from agenda_extract import AgendaDraft, AgendaDraftList, CanonicalResolver, extract_mail
from classification_store import JsonClassificationStore
from knowledge_models import MailDocument

ROOT = Path(__file__).resolve().parents[1]

def make_store(tmp_path):
    rules = tmp_path / "rules.json"
    shutil.copy(ROOT / "config/classification_rules.json", rules)
    return JsonClassificationStore(tmp_path / "classification_data", rules)

def save_one(store):
    mail = MailDocument.model_validate_json((ROOT / "fixtures/knowledge/mails.json").read_text(encoding="utf-8")).mails[1]
    quote = "4SA는 장비 조건 변경 이후 수율이 1.2%p 하락해 원인 분석 중입니다."
    run = store.start_classification_run("2026-28", "prompt", "classifier")
    result = extract_mail(mail, lambda _text: AgendaDraftList(agendas=[AgendaDraft(
        source_quote=quote, classification_context=quote, summary="4SA 수율 하락",
        topic="yield", state="open", item_kind="lotcd_specific",
    )]), CanonicalResolver(store.taxonomy, store.aliases()))
    store.save_classified_extraction(run.id, mail, result)
    return run, result, store.finish_classification_run(run.id)

def test_json_store_persists_and_reloads(tmp_path):
    store = make_store(tmp_path)
    _run, result, summary = save_one(store)
    reloaded = JsonClassificationStore(store.data_dir, store.rules_path)
    assert summary.workflow_state == "ready_for_approval"
    assert reloaded.classification_items("2026-28")[0].agenda_id == result.agendas[0].id
    assert not list(tmp_path.rglob("*.db"))

def test_correction_and_alias_are_written_to_json(tmp_path):
    store = make_store(tmp_path)
    _run, result, _summary = save_one(store)
    item = store.correct_classification(result.agendas[0].id, "6SA", "owner", "원문 확인")
    alias = store.create_learned_alias("SP 24G", "4SA", item.agenda_id, "owner")
    rules = json.loads(store.rules_path.read_text(encoding="utf-8"))
    assert item.decision.target_path.lotcd == "6SA"
    assert alias.target_paths[0].lotcd == "4SA"
    assert rules["learned_aliases"][0]["value"] == "SP 24G"

def test_rerun_archives_previous_run_for_comparison(tmp_path):
    store = make_store(tmp_path)
    first, _result, _summary = save_one(store)
    second = store.start_classification_run("2026-28", "prompt", "classifier", rerun=True)
    store.finish_classification_run(second.id)
    comparison = store.compare_runs(first.id, second.id)
    assert comparison.changed[0].before_status == "confirmed"
    assert comparison.changed[0].after_status is None
    assert (store.history_dir / "2026-28" / f"{first.id}.json").exists()
