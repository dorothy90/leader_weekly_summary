from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True)
class RankedHit:
    document_id: str
    rank: int
    raw: dict[str, Any]


@dataclass(frozen=True)
class FusedHit:
    document_id: str
    score: float
    raw: dict[str, Any]


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RankedHit]],
    k: int = 60,
) -> list[FusedHit]:
    scores: dict[str, float] = {}
    raw: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        for hit in ranking:
            scores[hit.document_id] = scores.get(hit.document_id, 0.0) + 1.0 / (
                k + hit.rank
            )
            raw.setdefault(hit.document_id, hit.raw)

    return [
        FusedHit(document_id, score, raw[document_id])
        for document_id, score in sorted(
            scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
