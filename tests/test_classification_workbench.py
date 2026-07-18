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
