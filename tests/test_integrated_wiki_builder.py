from __future__ import annotations

import pytest
from pydantic import ValidationError

from category_wiki_builder import CategoryNode, load_taxonomy
from integrated_wiki_builder import (
    _compact_issue_timelines,
    BuildResult,
    IssueDecision,
    NarrativeDraft,
    NarrativeValidationError,
    PageAnalysis,
    SupportedClaim,
    WeeklyHistoryEntry,
    assemble_body,
    build_child_digest,
    build_integrated_pages,
    canonical_path,
    direct_agendas_for_node,
    fetch_previous_pages,
    integrated_page_index_definition,
    invoke_structured,
    merge_weekly_history,
    render_current_body,
    run,
    save_integrated_pages,
    validate_draft,
    validate_issue_decisions,
    validate_stage1_evidence,
    validate_source_documents,
)


@pytest.fixture
def taxonomy():
    return load_taxonomy()


def empty_draft() -> NarrativeDraft:
    return NarrativeDraft(
        overview="",
        current_status="",
        cause_and_impact="",
        actions_and_effects="",
        pending_and_decisions="",
        accumulated_knowledge="",
        weekly_update="",
        confidence="low",
    )


def analysis_with_expected_issues(context) -> PageAnalysis:
    decisions = []
    seen = set()
    for timeline in context["issue_timelines"]:
        latest = timeline["events"][-1]
        decision = IssueDecision(
            issue_id=timeline["issue_id"],
            status="reopened"
            if timeline["event_type"] == "reopened"
            else "ongoing"
            if timeline["is_open"]
            else "resolved",
            summary=timeline["title"],
            mail_ids=[latest["mail_id"]],
            agenda_ids=[latest["agenda_id"]],
        )
        decisions.append(decision)
        seen.add(decision.issue_id)
    for digest in context["child_digests"]:
        for item in digest["issues"]:
            decision = IssueDecision.model_validate(item)
            if decision.issue_id not in seen:
                decisions.append(decision)
                seen.add(decision.issue_id)
    return PageAnalysis(issue_decisions=decisions, outline=["개요"])


def sample_integrated_page(
    category_id: str = "lotcd:4sa", week: str = "2026-W28"
) -> dict:
    return {
        "category_id": category_id,
        "page_kind": "latest",
        "doc_type": "canonical",
        "as_of_week": week,
        "source_doc_ids": ["chunk-1"],
    }


class FakeIndices:
    def __init__(self, existing=()):
        self.existing = set(existing)
        self.created = []
        self.refreshed = []

    def exists(self, *, index):
        return index in self.existing

    def create(self, *, index, body):
        self.existing.add(index)
        self.created.append((index, body))

    def refresh(self, *, index):
        self.refreshed.append(index)


class SourceMgetClient:
    def __init__(self, found_ids):
        self.found_ids = set(found_ids)
        self.calls = []
        self.indices = FakeIndices()

    def mget(self, *, index, body):
        self.calls.append((index, body))
        return {
            "docs": [
                {"_id": source_id, "found": source_id in self.found_ids}
                for source_id in body["ids"]
            ]
        }


class SearchClient:
    def __init__(self, hits):
        self.hits = hits
        self.query = None

    def search(self, *, index, body):
        self.query = body
        return {"hits": {"hits": self.hits}}


def test_supported_claim_requires_mail_and_agenda_evidence():
    with pytest.raises(ValidationError):
        SupportedClaim(text="4SA 수율이 하락했다", mail_ids=[], agenda_ids=[])


def test_narrative_draft_has_the_approved_sections():
    draft = NarrativeDraft(
        overview="개요 [mail:mail-1]",
        current_status="현재 상태 [mail:mail-1]",
        cause_and_impact="원인 분석 [mail:mail-1]",
        actions_and_effects="조치 결과 [mail:mail-1]",
        pending_and_decisions="후속 확인 [mail:mail-1]",
        accumulated_knowledge="누적 패턴 [mail:mail-1]",
        weekly_update="이번 주 변경 [mail:mail-1]",
        confidence="high",
    )
    assert draft.weekly_update.startswith("이번 주")


def test_integrated_mapping_adds_structured_fields_without_vectors():
    properties = integrated_page_index_definition()["mappings"]["properties"]
    assert properties["doc_type"]["type"] == "keyword"
    assert properties["weekly_history"]["type"] == "nested"
    assert properties["citation_map"]["type"] == "nested"
    assert "embedding" not in properties


def test_canonical_path_uses_lowercase_category_segments():
    node = CategoryNode("lotcd:dram:spica:4sa", "lotcd", "DRAM", "Spica", "4SA", "4SA")

    assert canonical_path(node) == "dram/spica/4sa"


def test_tech_direct_evidence_excludes_lotcd_agenda():
    node = CategoryNode("tech:dram:spica", "tech", "DRAM", "Spica", None, "Spica")
    agendas = [
        {
            "agenda_id": "tech",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": None}],
        },
        {
            "agenda_id": "lot",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        },
    ]

    assert [item["agenda_id"] for item in direct_agendas_for_node(node, agendas)] == [
        "tech"
    ]


def test_domain_direct_evidence_requires_domain_only_target():
    node = CategoryNode("domain:dram", "domain", "DRAM", None, None, "DRAM")
    agendas = [
        {
            "agenda_id": "domain",
            "target_paths": [{"domain": "DRAM", "tech": None, "lotcd": None}],
        },
        {
            "agenda_id": "tech",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": None}],
        },
    ]

    assert [item["agenda_id"] for item in direct_agendas_for_node(node, agendas)] == [
        "domain"
    ]


def test_lotcd_direct_evidence_requires_exact_path_and_confirmed_review():
    node = CategoryNode("lotcd:dram:spica:4sa", "lotcd", "DRAM", "Spica", "4SA", "4SA")
    agendas = [
        {
            "agenda_id": "confirmed",
            "review_status": "confirmed",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        },
        {
            "agenda_id": "pending",
            "review_status": "pending",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        },
        {
            "agenda_id": "other-lot",
            "review_status": "confirmed",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SB"}],
        },
    ]

    assert [item["agenda_id"] for item in direct_agendas_for_node(node, agendas)] == [
        "confirmed"
    ]


def test_history_replaces_same_week_and_preserves_older_entries():
    old = [
        WeeklyHistoryEntry(
            week="2026-W27", body_markdown="W27", source_mail_ids=["m27"]
        ),
        WeeklyHistoryEntry(
            week="2026-W26", body_markdown="W26", source_mail_ids=["m26"]
        ),
    ]
    current = WeeklyHistoryEntry(
        week="2026-W27", body_markdown="W27 fixed", source_mail_ids=["m27b"]
    )

    merged = merge_weekly_history(old, current)

    assert [item.week for item in merged] == ["2026-W27", "2026-W26"]
    assert merged[0].body_markdown == "W27 fixed"
    assert "W26" in assemble_body("## 개요\n현재", merged)


def test_legacy_w27_page_bootstraps_history_when_integrated_w28_is_built(taxonomy):
    agendas = [
        {
            "agenda_id": f"agenda-{week}",
            "mail_id": f"mail-{week}",
            "week": f"2026-W{week}",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-legacy",
            "summary": f"W{week} 상태",
            "subject": f"W{week} weekly",
            "topic": "yield",
            "source_doc_ids": [f"chunk-{week}"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        }
        for week in (27, 28)
    ]
    previous = {
        "lotcd:4sa": {
            "category_id": "lotcd:4sa",
            "page_kind": "latest",
            "as_of_week": "2026-W27",
            "body_markdown": "# 4SA\n\n## 진행 중 이슈\n\nW27 상태 [[agenda-27]]",
        }
    }
    lotcd_allowed_ids = []

    def analyze(context):
        decisions = []
        for timeline in context["issue_timelines"]:
            latest = timeline["events"][-1]
            decisions.append(
                IssueDecision(
                    issue_id=timeline["issue_id"],
                    status="reopened"
                    if timeline["event_type"] == "reopened"
                    else "ongoing"
                    if timeline["is_open"]
                    else "resolved",
                    summary=timeline["title"],
                    mail_ids=[latest["mail_id"]],
                    agenda_ids=[latest["agenda_id"]],
                )
            )
        for digest in context["child_digests"]:
            decisions.extend(IssueDecision.model_validate(item) for item in digest["issues"])
        if context["node"]["id"] == "lotcd:4sa":
            lotcd_allowed_ids.extend(
                agenda["agenda_id"] for agenda in context["allowed_agendas"]
            )
        return PageAnalysis(issue_decisions=decisions, outline=["개요"])

    def make_draft(context, analysis):
        if context["node"]["id"] == "lotcd:4sa":
            return empty_draft().model_copy(
                update={
                    "overview": "W28 현재 상태 [mail:mail-28]",
                    "weekly_update": "W28 변경 [mail:mail-28]",
                }
            )
        return empty_draft()

    result = build_integrated_pages(
        taxonomy,
        agendas,
        previous,
        as_of_week="2026-W28",
        analyze=analyze,
        draft=make_draft,
    )
    page = next(item for item in result.pages if item["category_id"] == "lotcd:4sa")

    assert not result.failures
    assert lotcd_allowed_ids == ["agenda-27", "agenda-28"]
    assert [item["week"] for item in page["weekly_history"]] == [
        "2026-W28",
        "2026-W27",
    ]
    assert page["weekly_history"][1]["source_mail_ids"] == ["mail-27"]
    assert "W27 상태 [mail:mail-27]" in page["body_markdown"]
    assert next(
        item for item in page["citation_map"] if item["mail_id"] == "mail-27"
    )["agenda_ids"] == ["agenda-27"]


def test_retained_history_citations_resolve_without_expanding_next_prompt(taxonomy):
    agendas = [
        {
            "agenda_id": f"agenda-{week}",
            "mail_id": f"mail-{week}",
            "week": f"2026-W{week}",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-history",
            "summary": f"W{week} 상태",
            "subject": f"W{week} weekly",
            "topic": "yield",
            "source_doc_ids": [f"chunk-{week}"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        }
        for week in (27, 28, 29)
    ]
    previous = {
        "lotcd:4sa": {
            "category_id": "lotcd:4sa",
            "page_kind": "latest",
            "as_of_week": "2026-W27",
            "body_markdown": "legacy W27",
        }
    }
    contexts = {}

    def analyze(context):
        contexts[(context["as_of_week"], context["node"]["id"])] = context
        decisions = []
        for timeline in context["issue_timelines"]:
            latest = timeline["events"][-1]
            decisions.append(
                IssueDecision(
                    issue_id=timeline["issue_id"],
                    status="ongoing" if timeline["is_open"] else "resolved",
                    summary=timeline["title"],
                    mail_ids=[latest["mail_id"]],
                    agenda_ids=[latest["agenda_id"]],
                )
            )
        for digest in context["child_digests"]:
            decisions.extend(IssueDecision.model_validate(item) for item in digest["issues"])
        return PageAnalysis(issue_decisions=decisions, outline=["개요"])

    def make_draft(context, analysis):
        if context["node"]["id"] != "lotcd:4sa":
            return empty_draft()
        week = context["as_of_week"][-2:]
        return empty_draft().model_copy(
            update={
                "overview": f"W{week} 현재 [mail:mail-{week}]",
                "weekly_update": f"W{week} 변경 [mail:mail-{week}]",
            }
        )

    w28_result = build_integrated_pages(
        taxonomy,
        agendas,
        previous,
        as_of_week="2026-W28",
        analyze=analyze,
        draft=make_draft,
    )
    w28_pages = {page["category_id"]: page for page in w28_result.pages}
    w29_result = build_integrated_pages(
        taxonomy,
        agendas,
        w28_pages,
        as_of_week="2026-W29",
        analyze=analyze,
        draft=make_draft,
    )
    page = next(
        item for item in w29_result.pages if item["category_id"] == "lotcd:4sa"
    )

    assert not w28_result.failures
    assert not w29_result.failures
    assert [
        item["agenda_id"]
        for item in contexts[("2026-W29", "lotcd:4sa")]["allowed_agendas"]
    ] == ["agenda-28", "agenda-29"]
    assert [item["week"] for item in page["weekly_history"]] == [
        "2026-W29",
        "2026-W28",
        "2026-W27",
    ]
    assert {
        item["mail_id"]: item["agenda_ids"] for item in page["citation_map"]
    } == {
        "mail-27": ["agenda-27"],
        "mail-28": ["agenda-28"],
        "mail-29": ["agenda-29"],
    }
    assert next(
        item for item in page["citation_map"] if item["mail_id"] == "mail-28"
    )["used_in_sections"] == ["2026-W28"]


def test_render_current_body_uses_only_current_narrative_sections():
    draft = NarrativeDraft(
        overview=" overview ",
        current_status="status",
        cause_and_impact="cause",
        actions_and_effects="actions",
        pending_and_decisions="pending",
        accumulated_knowledge="knowledge",
        weekly_update="separate history entry",
        confidence="high",
    )

    rendered = render_current_body(draft)

    assert rendered.startswith("## 개요\n\noverview")
    assert "## 누적 지식\n\nknowledge" in rendered
    assert "separate history entry" not in rendered


def test_structured_invocation_retries_once_after_validation_error():
    valid = PageAnalysis(outline=["개요"])

    class FakeRunnable:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise ValidationError.from_exception_data("PageAnalysis", [])
            return valid

    runnable = FakeRunnable()

    assert (
        invoke_structured(runnable, [{"role": "user", "content": "evidence"}]) == valid
    )
    assert runnable.calls == 2


def test_invalid_mail_citation_is_rejected():
    draft = NarrativeDraft(
        overview="근거 없는 주장 [mail:missing]",
        current_status="",
        cause_and_impact="",
        actions_and_effects="",
        pending_and_decisions="",
        accumulated_knowledge="",
        weekly_update="",
        confidence="low",
    )

    with pytest.raises(NarrativeValidationError, match="missing"):
        validate_draft(
            draft,
            {"mail-1": ["a1"]},
        )


def test_stage1_rejects_an_agenda_outside_the_allowed_scope():
    analysis = PageAnalysis(
        new_claims=[
            SupportedClaim(
                text="다른 LOTCD 주장",
                mail_ids=["mail-other"],
                agenda_ids=["agenda-other"],
            )
        ],
        outline=["개요"],
    )

    with pytest.raises(NarrativeValidationError, match="unknown agenda"):
        validate_stage1_evidence(
            analysis,
            [{"agenda_id": "agenda-allowed", "mail_id": "mail-allowed"}],
        )


def test_stage1_rejects_a_fabricated_mail_for_a_real_agenda():
    analysis = PageAnalysis(
        new_claims=[
            SupportedClaim(
                text="메일 위조 주장",
                mail_ids=["mail-fabricated"],
                agenda_ids=["agenda-allowed"],
            )
        ],
        outline=["개요"],
    )

    with pytest.raises(NarrativeValidationError, match="mail.*agenda"):
        validate_stage1_evidence(
            analysis,
            [{"agenda_id": "agenda-allowed", "mail_id": "mail-allowed"}],
        )


def test_stage1_accepts_and_maps_only_exact_agenda_mail_pairs():
    analysis = PageAnalysis(
        new_claims=[
            SupportedClaim(
                text="정확한 근거",
                mail_ids=["mail-1"],
                agenda_ids=["agenda-1"],
            )
        ],
        outline=["개요"],
    )

    assert validate_stage1_evidence(
        analysis,
        [
            {"agenda_id": "agenda-1", "mail_id": "mail-1"},
            {"agenda_id": "agenda-2", "mail_id": "mail-1"},
        ],
    ) == {"mail-1": ["agenda-1"]}


def test_draft_rejects_an_uncited_second_bullet():
    draft = empty_draft().model_copy(
        update={
            "overview": "- 확인된 변화 [mail:mail-1]\n- 인용 없는 변화",
        }
    )

    with pytest.raises(NarrativeValidationError, match="uncited.*개요"):
        validate_draft(draft, {"mail-1": ["agenda-1"]})


def test_draft_rejects_an_uncited_second_table_row():
    draft = empty_draft().model_copy(
        update={
            "overview": (
                "| 항목 | 상태 |\n"
                "| --- | --- |\n"
                "| 4SA | 개선 [mail:mail-1] |\n"
                "| 6SA | 확인 중 |"
            ),
        }
    )

    with pytest.raises(NarrativeValidationError, match="uncited.*개요"):
        validate_draft(draft, {"mail-1": ["agenda-1"]})


def test_draft_citation_map_uses_only_stage1_exact_agenda_ids():
    draft = empty_draft().model_copy(
        update={"overview": "확인된 변화 [mail:mail-1]"}
    )

    citation = validate_draft(draft, {"mail-1": ["agenda-exact"]})[0]

    assert citation.agenda_ids == ["agenda-exact"]


def test_resolved_decision_requires_terminal_agenda():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="resolved",
                summary="해결",
                mail_ids=["mail-1"],
                agenda_ids=["a1"],
            )
        ],
    )

    with pytest.raises(NarrativeValidationError, match="terminal"):
        validate_issue_decisions(
            analysis,
            [
                {
                    "mail_id": "mail-1",
                    "agenda_id": "a1",
                    "issue_id": "issue-1",
                    "state": "open",
                }
            ],
        )


def test_issue_validation_rejects_a_missing_expected_decision():
    with pytest.raises(NarrativeValidationError, match="missing.*issue-open"):
        validate_issue_decisions(
            PageAnalysis(outline=["개요"]),
            [],
            {"issue-open": "ongoing"},
        )


def test_issue_validation_rejects_terminal_agenda_for_another_issue():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="resolved",
                summary="해결",
                mail_ids=["mail-2"],
                agenda_ids=["agenda-2"],
            )
        ],
    )

    with pytest.raises(NarrativeValidationError, match="issue.*agenda"):
        validate_issue_decisions(
            analysis,
            [
                {
                    "agenda_id": "agenda-2",
                    "mail_id": "mail-2",
                    "issue_id": "issue-2",
                    "state": "resolved",
                }
            ],
            {"issue-1": "resolved"},
        )


def test_issue_validation_accepts_matching_reopened_and_resolved_decisions():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-reopened",
                status="reopened",
                summary="재발",
                mail_ids=["mail-open"],
                agenda_ids=["agenda-open"],
            ),
            IssueDecision(
                issue_id="issue-resolved",
                status="resolved",
                summary="해결",
                mail_ids=["mail-closed"],
                agenda_ids=["agenda-closed"],
            ),
        ],
    )
    agendas = [
        {
            "agenda_id": "agenda-open",
            "mail_id": "mail-open",
            "issue_id": "issue-reopened",
            "state": "open",
        },
        {
            "agenda_id": "agenda-closed",
            "mail_id": "mail-closed",
            "issue_id": "issue-resolved",
            "state": "resolved",
        },
    ]

    validate_stage1_evidence(analysis, agendas)
    validate_issue_decisions(
        analysis,
        agendas,
        {"issue-reopened": "reopened", "issue-resolved": "resolved"},
    )


def test_generation_order_is_lotcd_then_tech_then_domain(taxonomy):
    calls = []

    def analyze(context):
        calls.append(context["node"]["level"])
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        [],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert isinstance(result, BuildResult)
    assert calls == ["lotcd"] * 14 + ["tech"] * 7 + ["domain"] * 2
    assert len(result.pages) == 23


def test_child_page_ids_are_canonical_navigation_paths(taxonomy):
    result = build_integrated_pages(
        taxonomy,
        [],
        {},
        as_of_week="2026-W28",
        analyze=lambda context: PageAnalysis(outline=["개요"]),
        draft=lambda context, analysis: empty_draft(),
    )
    pages = {page["category_id"]: page for page in result.pages}

    assert pages["tech:dram:spica"]["child_page_ids"] == [
        "dram/spica/4sa",
        "dram/spica/6sa",
    ]
    assert pages["domain:dram"]["child_page_ids"] == [
        "dram/canopus",
        "dram/lucy",
        "dram/procyon",
        "dram/spica",
    ]
    assert all(
        ":" not in child_id
        for page in result.pages
        for child_id in page["child_page_ids"]
    )


def test_failed_lotcd_blocks_its_tech_and_domain(taxonomy):
    def analyze(context):
        if context["node"].get("lotcd") == "4SA":
            raise RuntimeError("generation failed")
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        [],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert "lotcd:4sa" in result.failures
    assert "tech:dram:spica" in result.failures
    assert "domain:dram" in result.failures


def test_child_digest_contains_only_traceable_analysis_and_draft_summary():
    claim = SupportedClaim(
        text="4SA 상태",
        mail_ids=["mail-1"],
        agenda_ids=["agenda-1"],
    )
    decision = IssueDecision(
        issue_id="issue-1",
        status="ongoing",
        summary="확인 중",
        mail_ids=["mail-1"],
        agenda_ids=["agenda-1"],
    )
    analysis = PageAnalysis(
        new_claims=[claim],
        issue_decisions=[decision],
        contradictions=["상태 불일치"],
        review_items=["담당자 확인"],
        outline=["개요"],
    )
    draft = NarrativeDraft(
        overview="요약 [mail:mail-1]",
        current_status="상세 [mail:mail-1]",
        cause_and_impact="",
        actions_and_effects="",
        pending_and_decisions="",
        accumulated_knowledge="",
        weekly_update="",
        confidence="medium",
    )

    digest = build_child_digest(
        {
            "canonical_id": "dram/spica/4sa",
            "as_of_week": "2026-W28",
            "confidence": "medium",
        },
        analysis,
        draft,
    )

    assert digest.model_dump() == {
        "canonical_id": "dram/spica/4sa",
        "as_of_week": "2026-W28",
        "summary": "요약 [mail:mail-1]",
        "claims": [claim.model_dump()],
        "issues": [decision.model_dump()],
        "contradictions": ["상태 불일치"],
        "confidence": "medium",
    }


def test_build_preserves_history_and_resolves_child_evidence(taxonomy):
    contexts = {}
    claim = SupportedClaim(
        text="4SA 상태",
        mail_ids=["mail-child"],
        agenda_ids=["agenda-child"],
    )
    agendas = [
        {
            "agenda_id": "agenda-child",
            "mail_id": "mail-child",
            "week": "2026-W28",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "4SA 상태",
            "subject": "4SA weekly",
            "topic": "yield",
            "source_doc_ids": ["chunk-child"],
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
        },
        {
            "agenda_id": "agenda-old",
            "mail_id": "mail-old",
            "week": "2026-W27",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-old",
            "summary": "이전 상태",
            "subject": "4SA previous",
            "topic": "yield",
            "source_doc_ids": ["chunk-old"],
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
        },
        {
            "agenda_id": "agenda-review",
            "mail_id": "mail-review",
            "week": "2026-W28",
            "state": "open",
            "review_status": "pending",
            "issue_id": "issue-review",
            "summary": "분류 검토",
            "subject": "review",
            "topic": "yield",
            "source_doc_ids": ["chunk-review"],
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
        },
    ]
    previous = {
        "lotcd:4sa": {
            "current_body_markdown": "이전 본문",
            "weekly_history": [
                {
                    "week": "2026-W27",
                    "body_markdown": "W27",
                    "source_mail_ids": ["mail-old"],
                },
                {
                    "week": "2026-W26",
                    "body_markdown": "W26",
                    "source_mail_ids": [],
                },
                {
                    "week": "2026-W25",
                    "body_markdown": "W25",
                    "source_mail_ids": [],
                },
            ],
            "citation_map": [
                {
                    "mail_id": "mail-old",
                    "agenda_ids": ["agenda-old"],
                    "used_in_sections": ["개요"],
                    "category_paths": ["dram/spica/4sa"],
                }
            ],
        }
    }

    def analyze(context):
        node_id = context["node"]["id"]
        contexts[node_id] = context
        base = analysis_with_expected_issues(context)
        if node_id in {"lotcd:4sa", "tech:dram:spica"}:
            return base.model_copy(
                update={
                    "retained_claims": [claim],
                    "contradictions": ["상태 불일치"],
                    "review_items": ["추가 검토"],
                }
            )
        return base

    def draft(context, analysis):
        node_id = context["node"]["id"]
        if node_id == "lotcd:4sa":
            return NarrativeDraft(
                overview="LOT 상태 [mail:mail-child]",
                current_status="",
                cause_and_impact="",
                actions_and_effects="",
                pending_and_decisions="",
                accumulated_knowledge="",
                weekly_update="이번 주 [mail:mail-child]",
                confidence="high",
            )
        if node_id == "tech:dram:spica":
            return NarrativeDraft(
                overview="Tech 상태 [mail:mail-child]",
                current_status="",
                cause_and_impact="",
                actions_and_effects="",
                pending_and_decisions="",
                accumulated_knowledge="",
                weekly_update="",
                confidence="medium",
            )
        return empty_draft()

    result = build_integrated_pages(
        taxonomy,
        agendas,
        previous,
        as_of_week="2026-W28",
        analyze=analyze,
        draft=draft,
    )
    pages = {page["category_id"]: page for page in result.pages}

    assert not result.failures
    assert [item["agenda_id"] for item in contexts["lotcd:4sa"]["allowed_agendas"]] == [
        "agenda-child",
        "agenda-old",
    ]
    assert contexts["lotcd:4sa"]["previous_current_body_markdown"] == "이전 본문"
    assert [item["week"] for item in contexts["lotcd:4sa"]["recent_history"]] == [
        "2026-W27",
        "2026-W26",
    ]
    assert contexts["tech:dram:spica"]["child_digests"][0]["claims"][0][
        "agenda_ids"
    ] == ["agenda-child"]
    assert [
        item["agenda_id"] for item in contexts["tech:dram:spica"]["allowed_agendas"]
    ] == ["agenda-child", "agenda-old"]

    page = pages["lotcd:4sa"]
    assert [item["week"] for item in page["weekly_history"]] == [
        "2026-W28",
        "2026-W27",
        "2026-W26",
        "2026-W25",
    ]
    assert page["aliases"] == ["SP LPDDR5 24G Fab4"]
    assert page["contradictions"] == ["상태 불일치"]
    assert page["generation_review_items"] == ["추가 검토"]
    assert page["review_agenda_ids"] == ["agenda-review"]
    assert page["citation_map"][0]["category_paths"] == ["dram/spica/4sa"]


def test_tech_context_receives_child_digest_without_raw_lotcd_timeline(taxonomy):
    contexts = {}
    claim = SupportedClaim(
        text="4SA 상태",
        mail_ids=["mail-child"],
        agenda_ids=["agenda-child"],
    )
    agendas = [
        {
            "agenda_id": "agenda-child",
            "mail_id": "mail-child",
            "week": "2026-W28",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "LOTCD only event",
            "subject": "4SA weekly",
            "topic": "yield",
            "source_doc_ids": ["chunk-child"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        }
    ]

    def analyze(context):
        contexts[context["node"]["id"]] = context
        base = analysis_with_expected_issues(context)
        if context["node"]["id"] == "lotcd:4sa":
            return base.model_copy(update={"new_claims": [claim]})
        return base

    def make_draft(context, analysis):
        if context["node"]["id"] == "lotcd:4sa":
            return empty_draft().model_copy(
                update={"overview": "4SA 상태 [mail:mail-child]"}
            )
        return empty_draft()

    result = build_integrated_pages(
        taxonomy,
        agendas,
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=make_draft,
    )

    assert not result.failures
    tech_context = contexts["tech:dram:spica"]
    assert tech_context["issue_timelines"] == []
    assert tech_context["child_digests"][0]["claims"][0]["text"] == "4SA 상태"


def test_compact_issue_timeline_keeps_first_terminal_and_latest_events():
    states = ["open", "investigating", "monitoring", "resolved", "open"]
    agendas = [
        {
            "agenda_id": f"agenda-{week}",
            "mail_id": f"mail-{week}",
            "week": f"2026-W{week}",
            "state": state,
            "review_status": "confirmed",
            "issue_id": "issue-long",
            "summary": f"state {state}",
        }
        for week, state in zip(range(24, 29), states, strict=True)
    ]

    timeline = _compact_issue_timelines(agendas, "2026-W28")[0]

    assert len(timeline["events"]) == 3
    assert [event["agenda_id"] for event in timeline["events"]] == [
        "agenda-24",
        "agenda-27",
        "agenda-28",
    ]
    assert timeline["current_state"] == "open"
    assert timeline["event_type"] == "reopened"


def test_parent_requires_issue_decisions_promoted_by_child_digest(taxonomy):
    agendas = [
        {
            "agenda_id": "agenda-child",
            "mail_id": "mail-child",
            "week": "2026-W28",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "child issue",
            "subject": "4SA weekly",
            "topic": "yield",
            "source_doc_ids": ["chunk-child"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        }
    ]
    child_decision = IssueDecision(
        issue_id="issue-child",
        status="ongoing",
        summary="child issue",
        mail_ids=["mail-child"],
        agenda_ids=["agenda-child"],
    )

    def analyze(context):
        if context["node"]["id"] == "lotcd:4sa":
            return PageAnalysis(
                issue_decisions=[child_decision],
                outline=["개요"],
            )
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        agendas,
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert "missing issue decision: issue-child" in result.failures[
        "tech:dram:spica"
    ]


def test_build_normalizes_stored_and_requested_week_formats(taxonomy):
    contexts = {}
    agendas = [
        {
            "agenda_id": "agenda-current",
            "mail_id": "mail-current",
            "week": "2026-28",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-1",
            "summary": "Current 4SA status",
            "subject": "current weekly",
            "topic": "yield",
            "source_doc_ids": ["chunk-current"],
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
        },
        {
            "agenda_id": "agenda-future",
            "mail_id": "mail-future",
            "week": "2026-29",
            "state": "resolved",
            "review_status": "confirmed",
            "issue_id": "issue-1",
            "summary": "Future 4SA status",
            "subject": "future weekly",
            "topic": "yield",
            "source_doc_ids": ["chunk-future"],
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
        },
    ]

    def analyze(context):
        contexts[context["node"]["id"]] = context
        return analysis_with_expected_issues(context)

    result = build_integrated_pages(
        taxonomy,
        agendas,
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )
    page = next(page for page in result.pages if page["category_id"] == "lotcd:4sa")

    assert [
        item["agenda_id"] for item in contexts["lotcd:4sa"]["allowed_agendas"]
    ] == ["agenda-current"]
    assert [
        event["agenda_id"]
        for issue in contexts["lotcd:4sa"]["issue_timelines"]
        for event in issue["events"]
    ] == ["agenda-current"]
    assert page["source_agenda_ids"] == ["agenda-current"]


def test_missing_child_digest_agenda_blocks_parent(taxonomy):
    missing_claim = SupportedClaim(
        text="근거 없음",
        mail_ids=["mail-missing"],
        agenda_ids=["agenda-missing"],
    )

    def analyze(context):
        if context["node"]["id"] == "lotcd:4sa":
            return PageAnalysis(retained_claims=[missing_claim], outline=["개요"])
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        [],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert "agenda-missing" in result.failures["lotcd:4sa"]
    assert result.failures["tech:dram:spica"] == "required child failed"
    assert result.failures["domain:dram"] == "required child failed"


def test_save_writes_one_canonical_and_one_snapshot_per_success(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "integrated_wiki_builder.helpers.bulk",
        lambda client, actions: captured.extend(actions),
    )
    client = SourceMgetClient(found_ids=["chunk-1"])

    count = save_integrated_pages(client, [sample_integrated_page()])

    assert count == 1
    assert [item["_id"] for item in captured] == [
        "lotcd:4sa",
        "lotcd:4sa:2026-W28",
    ]
    assert captured[0]["_source"]["page_kind"] == "latest"
    assert captured[0]["_source"]["doc_type"] == "canonical"
    assert captured[1]["_source"]["page_kind"] == "snapshot"
    assert captured[1]["_source"]["doc_type"] == "snapshot"


def test_fetch_previous_pages_returns_only_latest_documents():
    client = SearchClient(
        [
            {
                "_id": "lotcd:4sa",
                "_source": sample_integrated_page("lotcd:4sa", "2026-W27"),
            }
        ]
    )

    pages = fetch_previous_pages(client)

    assert set(pages) == {"lotcd:4sa"}
    assert client.query["query"] == {"term": {"page_kind": "latest"}}


def test_missing_weekly_mail_chunk_blocks_persistence():
    page = sample_integrated_page()
    page["source_doc_ids"] = ["missing-chunk", "another-missing"]

    with pytest.raises(
        NarrativeValidationError, match="another-missing.*missing-chunk"
    ):
        validate_source_documents(SourceMgetClient(found_ids=[]), [page])


def test_missing_weekly_mail_chunk_prevents_bulk_write(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "integrated_wiki_builder.helpers.bulk",
        lambda client, actions: captured.extend(actions),
    )

    with pytest.raises(NarrativeValidationError, match="missing-chunk"):
        save_integrated_pages(
            SourceMgetClient(found_ids=[]),
            [
                {
                    **sample_integrated_page(),
                    "source_doc_ids": ["missing-chunk"],
                }
            ],
        )

    assert captured == []


def test_source_validation_deduplicates_ids_before_mget():
    client = SourceMgetClient(found_ids=["chunk-1", "chunk-2"])
    second = sample_integrated_page("tech:dram:spica")
    second["source_doc_ids"] = ["chunk-2", "chunk-1"]

    validate_source_documents(client, [sample_integrated_page(), second])

    assert client.calls[0][1] == {"ids": ["chunk-1", "chunk-2"]}


def test_run_reads_existing_agendas_and_persists_successful_pages(
    taxonomy, monkeypatch
):
    captured = []

    class RunClient(SourceMgetClient):
        def __init__(self):
            super().__init__(["chunk-1"])
            self.indices = FakeIndices(existing=["mail_agendas"])
            self.searches = []

        def search(self, *, index, body):
            self.searches.append((index, body))
            if index == "mail_agendas":
                return {
                    "hits": {
                        "hits": [
                            {
                                "_id": "agenda-1",
                                "_source": {
                                    "agenda_id": "agenda-1",
                                    "mail_id": "mail-1",
                                    "week": "2026-W28",
                                    "state": "open",
                                    "review_status": "confirmed",
                                    "issue_id": "issue-1",
                                    "summary": "4SA 상태",
                                    "subject": "weekly",
                                    "topic": "yield",
                                    "source_doc_ids": ["chunk-1"],
                                    "target_paths": [
                                        {
                                            "domain": "DRAM",
                                            "tech": "Spica",
                                            "lotcd": "4SA",
                                        }
                                    ],
                                    "candidate_paths": [],
                                },
                            }
                        ]
                    }
                }
            return {"hits": {"hits": []}}

    client = RunClient()
    monkeypatch.setattr("integrated_wiki_builder.load_taxonomy", lambda path=None: taxonomy)
    monkeypatch.setattr(
        "integrated_wiki_builder.helpers.bulk",
        lambda bulk_client, actions: captured.extend(actions),
    )

    stats = run(
        weeks=["2026-W28"],
        allow_dummy_taxonomy=True,
        client=client,
        analyze=analysis_with_expected_issues,
        draft=lambda context, analysis: empty_draft(),
    )

    assert stats == {"pages": 23, "failed": 0, "expected_pages": 23}
    assert [index for index, _ in client.indices.created] == ["category_wiki_pages"]
    assert [index for index, _ in client.searches] == [
        "mail_agendas",
        "category_wiki_pages",
    ]
    assert len(captured) == 46


def test_run_rejects_absent_requested_week_before_llm_or_persistence(
    taxonomy, monkeypatch
):
    class RunClient(SourceMgetClient):
        def __init__(self):
            super().__init__([])
            self.indices = FakeIndices(existing=["mail_agendas"])

        def search(self, *, index, body):
            assert index == "mail_agendas"
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "agenda-28",
                            "_source": {
                                "agenda_id": "agenda-28",
                                "mail_id": "mail-28",
                                "week": "2026-W28",
                                "state": "open",
                                "review_status": "confirmed",
                                "issue_id": "issue-28",
                                "summary": "W28",
                                "target_paths": [],
                            },
                        }
                    ]
                }
            }

    client = RunClient()
    generator_calls = []
    persistence_calls = []
    monkeypatch.setattr("integrated_wiki_builder.load_taxonomy", lambda path=None: taxonomy)
    monkeypatch.setattr(
        "integrated_wiki_builder.build_llm_generators",
        lambda: generator_calls.append(True),
    )
    monkeypatch.setattr(
        "integrated_wiki_builder.save_integrated_pages",
        lambda *args: persistence_calls.append(True),
    )

    with pytest.raises(RuntimeError, match="2026-W29"):
        run(
            weeks=["2026-29"],
            allow_external_llm=True,
            allow_dummy_taxonomy=True,
            client=client,
        )

    assert generator_calls == []
    assert persistence_calls == []
    assert client.indices.created == []


def test_run_rejects_unacknowledged_external_llm_before_index_changes(
    taxonomy, monkeypatch
):
    client = SourceMgetClient(found_ids=[])
    monkeypatch.setattr("integrated_wiki_builder.load_taxonomy", lambda path=None: taxonomy)

    with pytest.raises(RuntimeError, match="External LLM"):
        run(
            weeks=["2026-W28"],
            allow_dummy_taxonomy=True,
            client=client,
        )

    assert client.indices.created == []
