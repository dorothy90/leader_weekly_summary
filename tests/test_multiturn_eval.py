import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

motor_asyncio = ModuleType("motor.motor_asyncio")
motor_asyncio.AsyncIOMotorClient = object
sys.modules["motor.motor_asyncio"] = motor_asyncio

import rag_api_opensearch_v3 as rag_api


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = ROOT / "fixtures" / "multiturn_eval.json"


def _graph_edges():
    graph = rag_api.get_naive_rag_graph().get_graph()
    return {(edge.source, edge.target) for edge in graph.edges}


def test_multiturn_feature_flag_enables_contextualizer_start(monkeypatch):
    monkeypatch.setattr(rag_api, "MULTITURN_V2_ENABLED", True)
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    edges = _graph_edges()

    assert ("__start__", "contextualize_turn") in edges
    assert ("__start__", "router") not in edges


def test_multiturn_feature_flag_restores_single_legacy_start(monkeypatch):
    monkeypatch.setattr(rag_api, "MULTITURN_V2_ENABLED", False)
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    edges = _graph_edges()

    assert ("__start__", "router") in edges
    assert ("__start__", "contextualize_turn") not in edges


def test_multiturn_eval_fixture_is_broad_and_scores_above_threshold():
    from scripts.eval_multiturn_rag import evaluate_cases

    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    required_tags = {
        "refine",
        "continue",
        "evidence",
        "compare",
        "expand",
        "explicit_team_override",
        "explicit_week_override",
        "inherited_filters",
        "new_topic_reset",
        "greeting_pronoun_clarification",
        "statistics_continuation",
        "topic_switch",
        "evidence_present",
        "evidence_absent",
    }

    assert len(cases) >= 15
    assert required_tags <= {tag for case in cases for tag in case["tags"]}

    summary = evaluate_cases(cases)

    assert summary["total"] == len(cases)
    assert summary["accuracy"] >= 0.9, summary["failures"]


def test_multiturn_eval_runner_executes_from_repository_root():
    result = subprocess.run(
        [sys.executable, "scripts/eval_multiturn_rag.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["accuracy"] >= 0.9
