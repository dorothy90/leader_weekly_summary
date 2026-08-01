"""Deterministic offline release metrics for mail RAG evaluation cases."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Literal, Sequence

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.chat import BM25_FALLBACK_DISCLOSURE

DEFAULT_CORPUS_PATH = Path("evals/datasets/mail_rag_synthetic_corpus.json")


class CorpusDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_id: str = Field(min_length=1)
    parent_id: str | None
    user_id: str = Field(min_length=1)
    source_type: Literal["mail", "wiki", "statistics"]
    text: str = Field(min_length=1)
    facts: list[str] = Field(default_factory=list)


class SyntheticCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_version: str = Field(min_length=1)
    documents: list[CorpusDocument] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_identifiers(self):
        document_ids = [item.document_id for item in self.documents]
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("corpus document_id values must be unique")
        parent_owners: dict[str, str] = {}
        for item in self.documents:
            if item.parent_id is None:
                continue
            prior = parent_owners.setdefault(item.parent_id, item.user_id)
            if prior != item.user_id:
                raise ValueError("one parent_id cannot cross owner partitions")
        return self


class EvaluationResult(BaseModel):
    """Strict adapter output; factual answers are canonical fact markers only."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1)
    route: Literal["fast", "deep", "clarify", "general"]
    document_ids: list[str]
    parent_ids: list[str]
    citations: list[str]
    answer_kind: Literal["supported", "abstention"]
    answer: str = Field(min_length=1)
    answer_facts: list[str]
    fallback_mode: Literal["hybrid", "bm25"]
    disclosures: list[str]
    multiturn_passed: bool | None

    @model_validator(mode="after")
    def exact_fact_representation(self):
        if len(self.answer_facts) != len(set(self.answer_facts)):
            raise ValueError("answer_facts must be unique")
        if self.answer_kind == "abstention":
            if self.answer != "[ABSTAIN]" or self.answer_facts:
                raise ValueError("abstention must be [ABSTAIN] with no facts")
            return self
        if not self.answer_facts:
            raise ValueError("supported answers require at least one fact")
        expected = " ".join(f"[F:{fact}]" for fact in self.answer_facts)
        if self.answer != expected:
            raise ValueError("answer must contain only ordered canonical fact markers")
        return self


class EvaluationReport(BaseModel):
    cases: int = Field(ge=0)
    retrieval_recall: float = Field(ge=0, le=1)
    citation_precision: float = Field(ge=0, le=1)
    unsupported_claim_rate: float = Field(ge=0, le=1)
    router_accuracy: float = Field(ge=0, le=1)
    fast_route_accuracy: float = Field(ge=0, le=1)
    deep_route_accuracy: float = Field(ge=0, le=1)
    multiturn_accuracy: float = Field(ge=0, le=1)
    fallback_accuracy: float = Field(ge=0, le=1)
    owner_leakage: int = Field(ge=0)
    malformed_results: int = Field(ge=0)
    passed: bool


def _ratio(numerator: int, denominator: int, *, empty: float = 1.0) -> float:
    return numerator / denominator if denominator else empty


def _returned_ids(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    return [str(item) for item in values]


def evaluate_cases(
    cases: Sequence[dict[str, Any]],
    results: Sequence[Any],
    *,
    corpus: dict[str, Any] | SyntheticCorpus | None = None,
) -> EvaluationReport:
    """Score caller-produced results without accessing live services."""
    if corpus is None:
        corpus = json.loads(DEFAULT_CORPUS_PATH.read_text(encoding="utf-8"))
    manifest = (
        corpus
        if isinstance(corpus, SyntheticCorpus)
        else SyntheticCorpus.model_validate(corpus)
    )
    document_owners = {item.document_id: item.user_id for item in manifest.documents}
    parent_owners = {
        item.parent_id: item.user_id
        for item in manifest.documents
        if item.parent_id is not None
    }
    declared_case_ids = [str(case["id"]) for case in cases]
    if len(declared_case_ids) != len(set(declared_case_ids)):
        raise ValueError("gold case ids must be unique")
    for case in cases:
        owner = case["user_id"]
        invalid_documents = [
            item
            for item in case.get("allowed_document_ids", [])
            if document_owners.get(item) != owner
        ]
        invalid_parents = [
            item
            for item in case.get("relevant_parent_ids", [])
            if parent_owners.get(item) != owner
        ]
        if invalid_documents or invalid_parents:
            raise ValueError(f"gold case owner manifest mismatch: {case['id']}")
    raw_by_id: dict[str, dict[str, Any]] = {}
    validated_by_id: dict[str, EvaluationResult] = {}
    malformed_ids: set[str] = set()
    malformed = 0
    cases_by_id = {str(case["id"]): case for case in cases}
    case_ids = set(cases_by_id)
    leakage = 0
    for item in results:
        if not isinstance(item, dict):
            malformed += 1
            continue
        item_id = str(item.get("id") or "")
        case = cases_by_id.get(item_id)
        allowed = set(case.get("allowed_document_ids", [])) if case else set()
        relevant = set(case.get("relevant_parent_ids", [])) if case else set()
        owner = case.get("user_id") if case else None
        leakage += sum(
            document_id not in allowed or document_owners.get(document_id) != owner
            for document_id in _returned_ids(item.get("document_ids"))
        )
        leakage += sum(
            parent_id not in relevant or parent_owners.get(parent_id) != owner
            for parent_id in _returned_ids(item.get("parent_ids"))
        )
        leakage += sum(
            citation not in allowed or document_owners.get(citation) != owner
            for citation in _returned_ids(item.get("citations"))
        )
        if not item_id or item_id not in case_ids or item_id in raw_by_id:
            malformed += 1
            continue
        raw_by_id[item_id] = item
        try:
            validated_by_id[item_id] = EvaluationResult.model_validate(item)
        except ValidationError:
            malformed_ids.add(item_id)
            malformed += 1
    route_hits = fast_hits = deep_hits = 0
    fast_cases = deep_cases = 0
    relevant_total = relevant_hits = 0
    citation_total = citation_hits = 0
    reported_claims = unsupported_claims = 0
    multiturn_total = multiturn_hits = 0
    fallback_hits = 0
    for case in cases:
        case_id = str(case["id"])
        raw_result = raw_by_id.get(case_id, {})
        result = validated_by_id.get(case_id)
        if result is None and case_id not in malformed_ids:
            malformed += 1
            malformed_ids.add(case_id)
        expected_route = case["expected_route"]
        route_matches = result is not None and result.route == expected_route
        route_hits += int(route_matches)
        if expected_route == "fast":
            fast_cases += 1
            fast_hits += int(route_matches)
        elif expected_route == "deep":
            deep_cases += 1
            deep_hits += int(route_matches)

        relevant = set(case.get("relevant_parent_ids", []))
        raw_parents = _returned_ids(raw_result.get("parent_ids"))
        retrieved = set(raw_parents)
        relevant_total += len(relevant)
        relevant_hits += len(relevant & retrieved)

        allowed = set(case.get("allowed_document_ids", []))
        citations = _returned_ids(raw_result.get("citations"))
        citation_total += len(citations)
        citation_hits += sum(
            citation in allowed and document_owners.get(citation) == case["user_id"]
            for citation in citations
        )

        expected_facts = set(case.get("answer_facts", []))
        if result is None:
            reported_claims += 1
            unsupported_claims += 1
        else:
            claims = set(result.answer_facts)
            forbidden = claims & set(case.get("forbidden_facts", []))
            missing = expected_facts - claims
            unsupported = (claims - expected_facts) | forbidden | missing
            if missing:
                malformed += 1
            reported_claims += len(claims | missing)
            unsupported_claims += len(unsupported)

        if case.get("slice") == "multiturn":
            multiturn_total += 1
            multiturn_hits += int(
                result is not None and result.multiturn_passed is True
            )

        expected_mode = case.get("expected_fallback_mode", "hybrid")
        mode_matches = result is not None and result.fallback_mode == expected_mode
        if expected_mode == "bm25":
            exact_disclosure = result is not None and result.disclosures == [
                BM25_FALLBACK_DISCLOSURE
            ]
            mode_matches = mode_matches and exact_disclosure
        fallback_hits += int(mode_matches)

    count = len(cases)
    report = EvaluationReport(
        cases=count,
        retrieval_recall=_ratio(relevant_hits, relevant_total),
        citation_precision=_ratio(citation_hits, citation_total),
        unsupported_claim_rate=_ratio(unsupported_claims, reported_claims, empty=0.0),
        router_accuracy=_ratio(route_hits, count, empty=0.0),
        fast_route_accuracy=_ratio(fast_hits, fast_cases),
        deep_route_accuracy=_ratio(deep_hits, deep_cases),
        multiturn_accuracy=_ratio(multiturn_hits, multiturn_total),
        fallback_accuracy=_ratio(fallback_hits, count, empty=0.0),
        owner_leakage=leakage,
        malformed_results=malformed,
        passed=False,
    )
    passed = bool(count) and all(
        (
            report.retrieval_recall == 1,
            report.citation_precision == 1,
            report.unsupported_claim_rate == 0,
            report.router_accuracy == 1,
            report.multiturn_accuracy == 1,
            report.fallback_accuracy == 1,
            report.owner_leakage == 0,
            report.malformed_results == 0,
        )
    )
    return report.model_copy(update={"passed": passed})


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate deterministic mail RAG results"
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/datasets/mail_rag_gold.json"),
    )
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    results = json.loads(args.results.read_text(encoding="utf-8"))
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    report = evaluate_cases(cases, results, corpus=corpus)
    print(report.model_dump_json(indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
