from pathlib import Path

import pytest

from scripts.seed_demo_wiki import seed_demo
from wiki_store import JsonWikiStore


def test_seed_demo_builds_graph_and_evidence_scenarios(tmp_path: Path):
    summary = seed_demo(tmp_path / "wiki", tmp_path / "classification")
    store = JsonWikiStore(tmp_path / "wiki")
    topics = store.topics()
    relations = store.relations()

    assert summary.topic_count >= 10
    assert len({team for topic in topics for team in topic.teams}) >= 4
    assert len(store.weeks()) >= 3
    assert any(len(topic.target_paths) > 1 for topic in topics)
    assert {topic.state for topic in topics} >= {"resolved", "reopened"}
    assert {relation.review_state for relation in relations} >= {"accepted", "pending"}
    assert any(not topic.related_topic_ids for topic in topics)
    assert list((tmp_path / "wiki" / "history" / "evidence").glob("*/*/*.json"))
    assert list((tmp_path / "wiki" / "history" / "weeks").glob("*/*.json"))
    assert list((tmp_path / "classification" / "mail").glob("**/body.html"))
    evidence = store.archived_evidence_for_agenda("DEMO-AGENDA-01")
    assert evidence.item.source_path == (
        "2026-W28/Spica수율/dummy_mail_001/combined.txt"
    )


def test_seed_demo_is_deterministic(tmp_path: Path):
    wiki = tmp_path / "wiki"
    classification = tmp_path / "classification"
    first = seed_demo(wiki, classification)
    first_payloads = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*.json")
    }

    second = seed_demo(wiki, classification, replace_demo=True)
    second_payloads = {
        path.relative_to(tmp_path): path.read_bytes()
        for path in tmp_path.rglob("*.json")
    }

    assert first == second
    assert first_payloads == second_payloads


def test_seed_demo_refuses_unowned_files(tmp_path: Path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "keep.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="non-demo files"):
        seed_demo(wiki, tmp_path / "classification", replace_demo=True)
