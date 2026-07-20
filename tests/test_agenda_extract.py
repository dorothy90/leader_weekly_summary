from pathlib import Path

from agenda_extract import AgendaDraft, AgendaDraftList, CanonicalResolver, extract_mail, llm_connection
from classification_workbench import classify_context
from knowledge_models import MailDocument, TaxonomyDocument

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "fixtures" / "knowledge"

def taxonomy():
    return TaxonomyDocument.model_validate_json((ROOT / "config/classification_rules.json").read_text(encoding="utf-8"))

def test_llm_connection_defaults_to_glm_47_flash(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "http://localhost:8000/v1")
    for name in ("KNOWLEDGE_LLM_MODEL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert llm_connection().model == "z-ai/glm-4.7-flash"

def test_group_alias_stays_conflict_candidate():
    resolved = CanonicalResolver(taxonomy()).resolve("SP LPDDR5 24G Edge defect")
    assert resolved.scope == "multi_lotcd"
    assert {path.lotcd for path in resolved.target_paths} == {"4SA", "6SA"}

def test_direct_tech_and_domain_scope_without_lotcd():
    tech = classify_context("Spica 전체 수율 유지", "unknown", taxonomy())
    domain = classify_context("DRAM 전 Tech 기준 변경", "unknown", taxonomy())
    assert tech.target_path.model_dump() == {"domain": "DRAM", "tech": "Spica", "lotcd": None}
    assert domain.target_path.model_dump() == {"domain": "DRAM", "tech": None, "lotcd": None}

def test_multi_lotcd_metric_is_aggregate_without_fanout():
    decision = classify_context("4SA·6SA 종합 수율 93%", "aggregate", taxonomy())
    assert decision.status == "aggregate"
    assert decision.target_path is None

def test_extract_mail_builds_one_confirmed_lotcd_item():
    mail = next(item for item in MailDocument.model_validate_json(
        (FIXTURES / "mails.json").read_text(encoding="utf-8")
    ).mails if item.id == "dummy_mail_002")
    quote = "4SA는 장비 조건 변경 이후 수율이 1.2%p 하락해 원인 분석 중입니다."
    result = extract_mail(mail, lambda _text: AgendaDraftList(agendas=[AgendaDraft(
        source_quote=quote, classification_context=quote, summary="4SA 수율 하락",
        topic="yield", state="open", item_kind="lotcd_specific",
    )]), CanonicalResolver(taxonomy()))
    assert result.agendas[0].decision.target_path.lotcd == "4SA"
