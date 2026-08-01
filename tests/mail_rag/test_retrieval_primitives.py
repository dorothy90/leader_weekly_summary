from app.domain.evidence import Evidence, RetrievalFilters
from app.domain.policy import PolicyContext
from app.retrieval.filters import build_owner_filters
from app.retrieval.fusion import RankedHit, reciprocal_rank_fusion
from app.security.citations import CitationValidator


def test_owner_filter_is_first_and_cannot_be_omitted():
    policy = PolicyContext.from_user_id("kim")
    filters = build_owner_filters(
        policy,
        RetrievalFilters(teams=["YIELD팀"], weeks=["2026-08"]),
    )
    assert filters[0] == {"term": {"user_id": "kim"}}
    assert {"terms": {"team": ["YIELD팀"]}} in filters
    assert {"terms": {"week": ["2026-08"]}} in filters


def test_rrf_combines_rankings_without_raw_score_addition():
    vector = [
        RankedHit(document_id="a", rank=1, raw={}),
        RankedHit(document_id="b", rank=2, raw={}),
    ]
    bm25 = [
        RankedHit(document_id="b", rank=1, raw={}),
        RankedHit(document_id="c", rank=2, raw={}),
    ]
    result = reciprocal_rank_fusion([vector, bm25], k=60)
    assert [item.document_id for item in result] == ["b", "a", "c"]


def test_citation_validator_rejects_unknown_and_cross_owner_sources():
    evidence = [
        Evidence(
            evidence_id="S1",
            source_type="mail",
            document_id="d1",
            title="t",
            excerpt="fact",
            score=1,
            user_id="lee",
            acl_decision_id="x",
            content_hash="h",
        )
    ]
    result = CitationValidator().validate(
        "사실입니다 [S1] [S9]",
        evidence,
        PolicyContext.from_user_id("kim"),
    )
    assert result.valid is False
    assert result.unknown_ids == ["S9"]
    assert result.unauthorized_ids == ["S1"]
