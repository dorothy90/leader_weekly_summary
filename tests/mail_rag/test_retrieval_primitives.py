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


def test_citation_validator_rejects_mixed_owner_duplicate_evidence_ids():
    evidence = [
        Evidence(
            evidence_id="S1",
            source_type="mail",
            document_id="lee-document",
            title="t",
            excerpt="fact",
            score=1,
            user_id="lee",
            acl_decision_id="x",
            content_hash="h1",
        ),
        Evidence(
            evidence_id="S1",
            source_type="mail",
            document_id="kim-document",
            title="t",
            excerpt="fact",
            score=1,
            user_id="kim",
            acl_decision_id="x",
            content_hash="h2",
        ),
    ]

    result = CitationValidator().validate(
        "사실입니다 [S1]",
        evidence,
        PolicyContext.from_user_id("kim"),
    )

    assert result.valid is False
    assert result.cited_ids == ["S1"]
    assert result.duplicate_ids == ["S1"]
    assert result.unauthorized_ids == ["S1"]


def test_citation_validator_reports_same_owner_duplicates_in_citation_order():
    evidence = [
        Evidence(
            evidence_id=evidence_id,
            source_type="mail",
            document_id=document_id,
            title="t",
            excerpt="fact",
            score=1,
            user_id="kim",
            acl_decision_id="x",
            content_hash=document_id,
        )
        for evidence_id, document_id in [
            ("S2", "d2-a"),
            ("S1", "d1-a"),
            ("S2", "d2-b"),
            ("S1", "d1-b"),
        ]
    ]

    result = CitationValidator().validate(
        "두 사실입니다 [S1] [S2] [S1]",
        evidence,
        PolicyContext.from_user_id("kim"),
    )

    assert result.valid is False
    assert result.cited_ids == ["S1", "S2"]
    assert result.duplicate_ids == ["S1", "S2"]
    assert result.unauthorized_ids == []
