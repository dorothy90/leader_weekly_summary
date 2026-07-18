from pathlib import Path

from classification_workbench import classify_context, lotcd_path
from knowledge_models import AliasRecord, CategoryPath, TaxonomyDocument

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
