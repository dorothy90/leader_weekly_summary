import re

from pydantic import BaseModel, Field

from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext


class CitationValidation(BaseModel):
    """Result of validating citation references, not their semantic support."""

    valid: bool
    cited_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    unauthorized_ids: list[str] = Field(default_factory=list)
    duplicate_ids: list[str] = Field(default_factory=list)


class CitationValidator:
    """Validate citation targets without judging semantic entailment.

    A citation target must use ``[S<digits>]`` syntax, exist exactly once in
    the supplied evidence, and have a ``user_id`` exactly equal to the policy
    owner. Grounded synthesis and whether evidence supports an answer's claims
    are responsibilities of later pipeline stages.
    """

    def validate(
        self,
        answer: str,
        evidence: list[Evidence],
        policy: PolicyContext,
    ) -> CitationValidation:
        cited_ids = list(dict.fromkeys(re.findall(r"\[(S\d+)\]", answer)))
        evidence_by_id: dict[str, list[Evidence]] = {}
        for item in evidence:
            evidence_by_id.setdefault(item.evidence_id, []).append(item)
        unknown_ids = [
            evidence_id
            for evidence_id in cited_ids
            if evidence_id not in evidence_by_id
        ]
        duplicate_ids = [
            evidence_id
            for evidence_id in cited_ids
            if len(evidence_by_id.get(evidence_id, ())) > 1
        ]
        unauthorized_ids = [
            evidence_id
            for evidence_id in cited_ids
            if evidence_id in evidence_by_id
            and any(
                item.user_id != policy.user_id
                for item in evidence_by_id[evidence_id]
            )
        ]
        return CitationValidation(
            valid=(
                bool(cited_ids)
                and not unknown_ids
                and not unauthorized_ids
                and not duplicate_ids
            ),
            cited_ids=cited_ids,
            unknown_ids=unknown_ids,
            unauthorized_ids=unauthorized_ids,
            duplicate_ids=duplicate_ids,
        )
