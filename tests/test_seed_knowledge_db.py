from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.seed_knowledge_db import seed_database


ROOT = Path(__file__).resolve().parents[1]


def test_seed_demo_and_production_databases(tmp_path):
    demo = seed_database(tmp_path / "demo.db")
    taxonomy = json.loads(
        (ROOT / "fixtures" / "knowledge" / "taxonomy.json").read_text(
            encoding="utf-8"
        )
    )
    taxonomy["is_dummy"] = False
    taxonomy_path = tmp_path / "production-taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(taxonomy, ensure_ascii=False), encoding="utf-8"
    )
    production = seed_database(
        tmp_path / "production.db", taxonomy_path=taxonomy_path
    )

    assert demo["is_dummy"] is True
    assert demo["agendas"] == 29
    assert production["is_dummy"] is False
    assert production["agendas"] == 0


def test_seed_refuses_taxonomy_switch_without_reset(tmp_path):
    db_path = tmp_path / "knowledge.db"
    seed_database(db_path)
    taxonomy_path = ROOT / "fixtures" / "knowledge" / "taxonomy.json"

    with pytest.raises(RuntimeError, match="cannot switch taxonomy"):
        seed_database(db_path, taxonomy_path=taxonomy_path)
