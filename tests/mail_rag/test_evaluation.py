import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.domain.chat import BM25_FALLBACK_DISCLOSURE
from scripts.evaluate_mail_rag import evaluate_cases


DATASET_PATH = Path("evals/datasets/mail_rag_gold.json")
CORPUS_PATH = Path("evals/datasets/mail_rag_synthetic_corpus.json")
REQUIRED_FIELDS = {
    "id",
    "slice",
    "question",
    "user_id",
    "expected_route",
    "allowed_document_ids",
    "relevant_parent_ids",
    "answer_facts",
    "forbidden_facts",
    "expected_fallback_mode",
}
EXPECTED_IDS = {
    "exact-product",
    "exact-lot",
    "exact-defect",
    "semantic-cause",
    "semantic-action",
    "single-team",
    "three-team",
    "single-week",
    "four-week",
    "twelve-week",
    "statistics-count",
    "statistics-missing",
    "no-evidence",
    "cross-owner-top-hit",
    "missing-owner",
    "wiki-navigation",
    "wiki-stale",
    "embedding-fallback",
    "followup-refine",
    "followup-compare",
    "followup-evidence",
    "followup-new-topic",
    "conflicting-mail",
    "prompt-injection-mail",
    "deep-team-trend",
    "deep-long-trend",
    "deep-causal",
    "deep-report",
    "deep-partial-failure",
    "deep-cancel",
}


def _load_cases():
    return json.loads(DATASET_PATH.read_text(encoding="utf-8"))


def _load_corpus():
    return json.loads(CORPUS_PATH.read_text(encoding="utf-8"))


def _corpus_partitions(corpus):
    partitions = {}
    for document in corpus["documents"]:
        owner = document["user_id"]
        partition = partitions.setdefault(owner, {"documents": set(), "parents": set()})
        partition["documents"].add(document["document_id"])
        if document.get("parent_id"):
            partition["parents"].add(document["parent_id"])
    return partitions


def test_gold_dataset_has_complete_30_case_owner_partitioned_baseline():
    cases = _load_cases()
    corpus = _load_corpus()
    partitions = _corpus_partitions(corpus)

    assert len(cases) >= 30
    assert {case["id"] for case in cases} == EXPECTED_IDS
    assert all(REQUIRED_FIELDS <= set(case) for case in cases)
    assert {case["expected_route"] for case in cases} == {"fast", "deep"}
    assert len({case["question"] for case in cases}) == len(cases)
    for case in cases:
        owner = case["user_id"]
        assert owner in partitions
        assert set(case["allowed_document_ids"]) <= partitions[owner]["documents"]
        assert set(case["relevant_parent_ids"]) <= partitions[owner]["parents"]
        assert case["expected_fallback_mode"] in {"hybrid", "bm25"}
    fallback = next(case for case in cases if case["id"] == "embedding-fallback")
    assert fallback["expected_fallback_mode"] == "bm25"
    assert fallback["expected_disclosures"] == [BM25_FALLBACK_DISCLOSURE]


def test_committed_corpus_has_unique_owned_source_documents_for_every_gold_id():
    cases = _load_cases()
    corpus = _load_corpus()
    documents = corpus["documents"]
    by_document = {item["document_id"]: item for item in documents}
    by_parent = {
        item["parent_id"]: item
        for item in documents
        if item.get("parent_id") is not None
    }

    assert len(by_document) == len(documents)
    assert all(item["text"].strip() for item in documents)
    for case in cases:
        for document_id in case["allowed_document_ids"]:
            assert by_document[document_id]["user_id"] == case["user_id"]
        for parent_id in case["relevant_parent_ids"]:
            assert by_parent[parent_id]["user_id"] == case["user_id"]


def test_evaluator_rejects_a_foreign_gold_allowlist():
    case = {
        "id": "invalid-gold",
        "slice": "owner_security",
        "user_id": "kim",
        "allowed_document_ids": ["lee-mail-001"],
        "expected_route": "fast",
        "answer_facts": [],
        "forbidden_facts": [],
        "relevant_parent_ids": [],
        "expected_fallback_mode": "hybrid",
    }

    with pytest.raises(ValueError, match="gold case owner manifest"):
        evaluate_cases([case], [], corpus=_load_corpus())


def test_complete_gold_and_corpus_produce_a_strict_passing_baseline():
    cases = _load_cases()
    results = []
    for case in cases:
        facts = case["answer_facts"]
        results.append(
            {
                "id": case["id"],
                "route": case["expected_route"],
                "document_ids": case["allowed_document_ids"],
                "parent_ids": case["relevant_parent_ids"],
                "citations": case["allowed_document_ids"],
                "answer_kind": "supported" if facts else "abstention",
                "answer": (
                    " ".join(f"[F:{fact}]" for fact in facts) if facts else "[ABSTAIN]"
                ),
                "answer_facts": facts,
                "fallback_mode": case["expected_fallback_mode"],
                "disclosures": case.get("expected_disclosures", []),
                "multiturn_passed": case["slice"] == "multiturn",
            }
        )

    report = evaluate_cases(cases, results, corpus=_load_corpus())

    assert report.owner_leakage == 0
    assert report.malformed_results == 0
    assert report.passed is True


def test_every_returned_identifier_is_owner_and_case_allowlist_checked():
    case = {
        "id": "owner",
        "slice": "owner_security",
        "user_id": "kim",
        "allowed_document_ids": ["kim-mail-001"],
        "expected_route": "fast",
        "answer_facts": ["ALPHA 수율 저하"],
        "forbidden_facts": [],
        "relevant_parent_ids": ["kim-parent-001"],
        "expected_fallback_mode": "hybrid",
    }
    result = {
        "id": "owner",
        "route": "fast",
        "document_ids": ["kim-mail-001"],
        "parent_ids": ["lee-parent-001"],
        "citations": ["unknown-document"],
        "answer_kind": "supported",
        "answer_facts": ["ALPHA 수율 저하"],
        "answer": "[F:ALPHA 수율 저하]",
        "fallback_mode": "hybrid",
        "disclosures": [],
        "multiturn_passed": None,
    }

    report = evaluate_cases([case], [result], corpus=_load_corpus())

    assert report.owner_leakage == 2
    assert report.passed is False


def test_missing_or_unmarked_fact_reporting_cannot_bypass_unsupported_gate():
    case = {
        "id": "strict",
        "slice": "exact_keyword",
        "user_id": "kim",
        "allowed_document_ids": ["kim-mail-001"],
        "expected_route": "fast",
        "answer_facts": ["ALPHA 수율 저하"],
        "forbidden_facts": [],
        "relevant_parent_ids": ["kim-parent-001"],
        "expected_fallback_mode": "hybrid",
    }
    missing_facts = {
        "id": "strict",
        "route": "fast",
        "document_ids": ["kim-mail-001"],
        "parent_ids": ["kim-parent-001"],
        "citations": ["kim-mail-001"],
        "answer_kind": "supported",
        "answer": "invented arbitrary claim",
        "fallback_mode": "hybrid",
        "disclosures": [],
        "multiturn_passed": None,
    }
    unmarked_claim = {**missing_facts, "answer_facts": []}
    hidden_by_abstention = {
        **missing_facts,
        "answer_kind": "abstention",
        "answer": "[ABSTAIN]",
        "answer_facts": [],
    }
    invented_marker = {
        **missing_facts,
        "answer": "[F:invented arbitrary claim]",
        "answer_facts": ["invented arbitrary claim"],
    }

    for result in (
        missing_facts,
        unmarked_claim,
        hidden_by_abstention,
        invented_marker,
    ):
        report = evaluate_cases([case], [result], corpus=_load_corpus())
        assert report.malformed_results == 1
        assert report.unsupported_claim_rate == 1
        assert report.passed is False


def test_non_object_result_is_malformed_instead_of_crashing():
    case = {
        "id": "strict",
        "slice": "abstention",
        "user_id": "kim",
        "allowed_document_ids": [],
        "expected_route": "fast",
        "answer_facts": [],
        "forbidden_facts": [],
        "relevant_parent_ids": [],
        "expected_fallback_mode": "hybrid",
    }

    report = evaluate_cases([case], [None], corpus=_load_corpus())

    assert report.malformed_results >= 1
    assert report.unsupported_claim_rate == 1
    assert report.passed is False


def test_owner_leakage_is_an_absolute_release_failure():
    cases = [
        {
            "id": "c1",
            "slice": "owner_security",
            "user_id": "kim",
            "allowed_document_ids": ["kim-mail-001"],
            "expected_route": "fast",
            "answer_facts": [],
            "forbidden_facts": [],
            "relevant_parent_ids": [],
            "expected_fallback_mode": "hybrid",
        }
    ]
    results = [
        {
            "id": "c1",
            "route": "fast",
            "document_ids": ["lee-mail-001"],
            "parent_ids": [],
            "citations": [],
            "answer_kind": "abstention",
            "answer_facts": [],
            "answer": "[ABSTAIN]",
            "fallback_mode": "hybrid",
            "disclosures": [],
            "multiturn_passed": None,
        }
    ]

    report = evaluate_cases(cases, results)

    assert report.owner_leakage == 1
    assert report.passed is False


def test_evaluator_reports_all_release_metrics():
    cases = [
        {
            "id": "fast",
            "slice": "exact_keyword",
            "user_id": "kim",
            "allowed_document_ids": ["kim-mail-001"],
            "expected_route": "fast",
            "answer_facts": ["product A"],
            "forbidden_facts": ["invented"],
            "relevant_parent_ids": ["kim-parent-001"],
            "expected_fallback_mode": "hybrid",
        },
        {
            "id": "followup",
            "slice": "multiturn",
            "user_id": "kim",
            "allowed_document_ids": ["kim-mail-002"],
            "expected_route": "fast",
            "answer_facts": ["action B"],
            "forbidden_facts": [],
            "relevant_parent_ids": ["kim-parent-002"],
            "expected_fallback_mode": "hybrid",
        },
    ]
    results = [
        {
            "id": "fast",
            "route": "fast",
            "document_ids": ["kim-mail-001"],
            "parent_ids": ["kim-parent-001"],
            "citations": ["kim-mail-001"],
            "answer_kind": "supported",
            "answer_facts": ["product A"],
            "answer": "[F:product A]",
            "fallback_mode": "hybrid",
            "disclosures": [],
            "multiturn_passed": None,
        },
        {
            "id": "followup",
            "route": "fast",
            "document_ids": ["kim-mail-002"],
            "parent_ids": ["kim-parent-002"],
            "citations": ["kim-mail-002"],
            "answer_kind": "supported",
            "answer_facts": ["action B"],
            "answer": "[F:action B]",
            "fallback_mode": "hybrid",
            "disclosures": [],
            "multiturn_passed": True,
        },
    ]

    report = evaluate_cases(cases, results)

    assert report.retrieval_recall == 1
    assert report.citation_precision == 1
    assert report.unsupported_claim_rate == 0
    assert report.router_accuracy == 1
    assert report.multiturn_accuracy == 1
    assert report.fallback_accuracy == 1
    assert report.owner_leakage == 0
    assert report.passed is True


def test_embedding_failure_requires_exact_bm25_disclosure():
    case = {
        "id": "fallback",
        "slice": "fallback",
        "user_id": "kim",
        "allowed_document_ids": [],
        "expected_route": "fast",
        "answer_facts": [],
        "forbidden_facts": [],
        "relevant_parent_ids": [],
        "expected_fallback_mode": "bm25",
    }
    base = {
        "id": "fallback",
        "route": "fast",
        "document_ids": [],
        "parent_ids": [],
        "citations": [],
        "answer_kind": "abstention",
        "answer_facts": [],
        "answer": "[ABSTAIN]",
        "fallback_mode": "bm25",
        "disclosures": [],
        "multiturn_passed": None,
    }

    missing = evaluate_cases([case], [base])
    exact = evaluate_cases(
        [case],
        [{**base, "disclosures": [BM25_FALLBACK_DISCLOSURE]}],
    )

    assert missing.fallback_accuracy == 0
    assert missing.passed is False
    assert exact.fallback_accuracy == 1
    assert exact.passed is True


def test_fast_and_deep_routes_are_scored_separately():
    cases = [
        {
            "id": "fast",
            "slice": "single_week",
            "user_id": "kim",
            "allowed_document_ids": [],
            "expected_route": "fast",
            "answer_facts": [],
            "forbidden_facts": [],
            "relevant_parent_ids": [],
            "expected_fallback_mode": "hybrid",
        },
        {
            "id": "deep",
            "slice": "deep_route",
            "user_id": "kim",
            "allowed_document_ids": [],
            "expected_route": "deep",
            "answer_facts": [],
            "forbidden_facts": [],
            "relevant_parent_ids": [],
            "expected_fallback_mode": "hybrid",
        },
    ]
    results = [
        {
            "id": "fast",
            "route": "fast",
            "document_ids": [],
            "parent_ids": [],
            "citations": [],
            "answer_kind": "abstention",
            "answer": "[ABSTAIN]",
            "answer_facts": [],
            "fallback_mode": "hybrid",
            "disclosures": [],
            "multiturn_passed": None,
        },
        {
            "id": "deep",
            "route": "fast",
            "document_ids": [],
            "parent_ids": [],
            "citations": [],
            "answer_kind": "abstention",
            "answer": "[ABSTAIN]",
            "answer_facts": [],
            "fallback_mode": "hybrid",
            "disclosures": [],
            "multiturn_passed": None,
        },
    ]

    report = evaluate_cases(cases, results)

    assert report.fast_route_accuracy == 1
    assert report.deep_route_accuracy == 0
    assert report.router_accuracy == 0.5
    assert report.passed is False


def test_documentation_covers_security_architecture_budgets_and_runbooks():
    api = Path("docs/api.md").read_text(encoding="utf-8")
    operations = Path("docs/operations.md").read_text(encoding="utf-8")

    for phrase in (
        "request body",
        "upstream gateway",
        "team is a search facet, not an authorization boundary",
        "Documents without `user_id` are invisible",
        "POST /v1/chat",
        "POST /v1/research/{job_id}/status",
        "POST /v1/research/{job_id}/cancel",
        "POST /v1/research/{job_id}/retry",
        "POST /v1/research/{job_id}/events",
        "POST /v1/mail-content/{content_id}",
        "GET /health",
        "INVALID_REQUEST",
    ):
        assert phrase in api

    for phrase in (
        "`POST /v1/chat` has one execution path",
        "`MultiSourceAgenticWorkflow`",
        "typed agent policy",
        "four agent iterations",
        "eight evidence objects",
        "150-second",
        "backfill_user_id.py",
        "--apply",
        "backfill_parent_child.py",
        "shadow_retrieval.py",
        "Alias cutover",
        "Rollback",
        "TLS",
        "certificate verification",
        "evaluate_mail_rag.py",
        "mail_rag_synthetic_corpus.json",
        "malformed_results",
        'answer_kind="supported"',
        "Every returned parent ID, document ID, and citation",
        BM25_FALLBACK_DISCLOSURE,
        "owner leakage greater than zero",
    ):
        assert phrase in operations


def test_evaluator_script_runs_directly_from_repository_root():
    completed = subprocess.run(
        [sys.executable, "scripts/evaluate_mail_rag.py", "--help"],
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--results" in completed.stdout
