from __future__ import annotations

import re

from knowledge_models import (
    CandidateMatch,
    CategoryPath,
    ClassificationDecision,
    TaxonomyDocument,
)

CLASSIFIER_VERSION = "lotcd-v1"


def _contains(text: str, phrase: str) -> bool:
    return bool(re.search(
        rf"(?<![A-Za-z0-9]){re.escape(phrase)}(?![A-Za-z0-9])",
        text, re.IGNORECASE,
    ))


def lotcd_path(taxonomy: TaxonomyDocument, code: str) -> CategoryPath:
    for domain in taxonomy.domains:
        for tech in domain.techs:
            for lotcd in tech.lotcds:
                if lotcd.code.casefold() == code.casefold():
                    return CategoryPath(
                        domain=domain.name, tech=tech.name, lotcd=lotcd.code
                    )
    raise ValueError(f"Unknown LOTCD: {code}")


def classify_context(text, item_kind, taxonomy, aliases=None):
    if item_kind == "aggregate":
        return ClassificationDecision(
            status="aggregate",
            diagnostics=["AGGREGATE_METRIC"],
            confidence=1.0,
        )
    matches = []
    for domain in taxonomy.domains:
        for tech in domain.techs:
            for lotcd in tech.lotcds:
                if _contains(text, lotcd.code):
                    matches.append(CandidateMatch(
                        phrase=lotcd.code,
                        lotcd=lotcd.code,
                        match_type="canonical",
                        rule_id=f"canonical:{lotcd.code}",
                        score=1.0,
                    ))
    codes = sorted({match.lotcd for match in matches})
    if len(codes) == 1:
        return ClassificationDecision(
            status="confirmed",
            target_path=lotcd_path(taxonomy, codes[0]),
            matches=matches,
            confidence=1.0,
        )
    if len(codes) > 1:
        return ClassificationDecision(
            status="conflict",
            matches=matches,
            diagnostics=["MULTIPLE_LOTCD_CONFLICT"],
            confidence=0.0,
        )
    return ClassificationDecision(
        status="unclassified",
        diagnostics=["NO_LOTCD_MATCH"],
        confidence=0.0,
    )
