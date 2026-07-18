from pathlib import Path

from classification_workbench import classify_context, lotcd_path
from knowledge_models import TaxonomyDocument

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"


def taxonomy():
    return TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )


def test_lotcd_path_derives_upper_hierarchy():
    assert lotcd_path(taxonomy(), "4SA").model_dump() == {
        "domain": "DRAM", "tech": "Spica", "lotcd": "4SA"
    }


def test_single_code_is_confirmed_with_trace():
    decision = classify_context("4SA 수율 하락", "lotcd_specific", taxonomy())
    assert decision.status == "confirmed"
    assert decision.target_path.lotcd == "4SA"
    assert decision.matches[0].rule_id == "canonical:4SA"
