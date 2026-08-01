import re

from pydantic import BaseModel, Field

from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext


class CitationValidation(BaseModel):
    valid: bool
    cited_ids: list[str] = Field(default_factory=list)
    unknown_ids: list[str] = Field(default_factory=list)
    unauthorized_ids: list[str] = Field(default_factory=list)


class CitationValidator:
    def validate(
        self,
        answer: str,
        evidence: list[Evidence],
        policy: PolicyContext,
    ) -> CitationValidation:
        cited_ids = list(dict.fromkeys(re.findall(r"\[(S\d+)\]", answer)))
        evidence_by_id = {item.evidence_id: item for item in evidence}
        unknown_ids = [
            evidence_id
            for evidence_id in cited_ids
            if evidence_id not in evidence_by_id
        ]
        unauthorized_ids = [
            evidence_id
            for evidence_id in cited_ids
            if evidence_id in evidence_by_id
            and evidence_by_id[evidence_id].user_id != policy.user_id
        ]
        return CitationValidation(
            valid=bool(cited_ids) and not unknown_ids and not unauthorized_ids,
            cited_ids=cited_ids,
            unknown_ids=unknown_ids,
            unauthorized_ids=unauthorized_ids,
        )
