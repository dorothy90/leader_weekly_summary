from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import integrated_wiki_builder as wiki_builder_module
import pytest
import wiki_issue_ledger as issue_ledger_module
from langchain_core.exceptions import OutputParserException
from pydantic import SecretStr, ValidationError

from category_wiki_builder import (
    AGENDA_INDEX,
    PAGE_INDEX,
    SOURCE_INDEX,
    CategoryNode,
    load_taxonomy,
)
from integrated_wiki_builder import (
    _compact_issue_timelines,
    BuildResult,
    ChildDigest,
    IssueDecision,
    MergeValidationContext,
    MergedWikiDocument,
    NarrativeDraft,
    NarrativeValidationError,
    PageAnalysis,
    SupportedClaim,
    WeeklyHistoryEntry,
    affected_node_ids,
    assemble_body,
    build_child_digest,
    build_incremental_pages,
    build_integrated_pages,
    build_llm_generators,
    canonical_path,
    direct_agendas_for_node,
    fetch_previous_pages,
    integrated_page_index_definition,
    invoke_structured,
    merge_weekly_history,
    render_current_body,
    run,
    save_integrated_page,
    save_integrated_pages,
    select_weekly_delta,
    validate_draft,
    validate_issue_decisions,
    validate_merged_document,
    validate_stage1_evidence,
    validate_source_documents,
)
from wiki_issue_ledger import (
    IssueLedgerResult,
    IssueStateEvent,
    WikiIssue,
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


def single_lotcd_taxonomy(taxonomy):
    domain = next(item for item in taxonomy.domains if item.name == "DRAM")
    tech = next(item for item in domain.techs if item.name == "Spica")
    lotcd = next(item for item in tech.lotcds if item.code == "4SA")
    return taxonomy.model_copy(
        update={
            "domains": [
                domain.model_copy(
                    update={
                        "techs": [tech.model_copy(update={"lotcds": [lotcd]})]
                    }
                )
            ]
        }
    )


def one_open_agenda():
    return {
        "agenda_id": "agenda-28",
        "mail_id": "mail-28",
        "week": "2026-W28",
        "state": "open",
        "review_status": "confirmed",
        "issue_id": "issue-open",
        "summary": "4SA 수율 하락",
        "subject": "4SA weekly",
        "topic": "yield",
        "source_doc_ids": ["chunk-28"],
        "target_paths": [
            {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
        ],
        "candidate_paths": [],
    }


def complete_page_fixture(
    category_id: str,
    domain: str,
    tech: str | None,
    lotcd: str | None,
) -> dict[str, Any]:
    level = "lotcd" if lotcd else "tech" if tech else "domain"
    title = lotcd or tech or domain
    canonical_id = "/".join(
        value.casefold() for value in (domain, tech, lotcd) if value
    )
    return {
        "category_id": category_id,
        "page_kind": "latest",
        "doc_type": "canonical",
        "canonical_id": canonical_id,
        "level": level,
        "domain": domain,
        "tech": tech,
        "lotcd": lotcd,
        "title": title,
        "product": None,
        "fab_id": lotcd[:1] if lotcd else None,
        "aliases": [],
        "as_of_week": "2026-W28",
        "current_body_markdown": "## 기존 상태\n\n수율 분석 중이다. [mail:mail-28]",
        "weekly_history": [],
        "body_markdown": "## 기존 상태\n\n수율 분석 중이다. [mail:mail-28]",
        "citation_map": [
            {
                "mail_id": "mail-28",
                "agenda_ids": ["agenda-28"],
                "source_doc_ids": ["chunk-28"],
                "used_in_sections": ["기존 상태"],
                "category_paths": [canonical_id],
            }
        ],
        "child_page_ids": [],
        "confidence": "medium",
        "agenda_count": 1,
        "open_issue_ids": ["issue:4sa-yield"],
        "resolved_issue_ids": [],
        "open_issue_count": 1,
        "resolved_issue_count": 0,
        "contradictions": [],
        "generation_review_items": [],
        "review_agenda_ids": [],
        "source_agenda_ids": ["agenda-28"],
        "source_doc_ids": ["chunk-28"],
        "source_hash": "old",
        "taxonomy_version": 1,
        "schema_version": 2,
        "generation_strategy": "incremental_merge",
        "generated_at": "2026-07-07T00:00:00+00:00",
        "updated_at": "2026-07-07T00:00:00+00:00",
    }


def w29_4sa_agenda() -> dict[str, Any]:
    return {
        **one_open_agenda(),
        "agenda_id": "agenda-29",
        "mail_id": "mail-29",
        "week": "2026-W29",
        "updated_week": "2026-W29",
        "summary": "chamber A 원인 확인 후 조건 원복",
        "topic": "root_cause",
        "state": "in_progress",
        "source_doc_ids": ["chunk-29"],
        "content_hash": "w29",
    }


def previous_page_chain() -> dict[str, dict[str, Any]]:
    return {
        "lotcd:4sa": complete_page_fixture(
            "lotcd:4sa", "DRAM", "Spica", "4SA"
        ),
        "tech:dram:spica": complete_page_fixture(
            "tech:dram:spica", "DRAM", "Spica", None
        ),
        "domain:dram": complete_page_fixture(
            "domain:dram", "DRAM", None, None
        ),
    }


def issue_result_for(agendas: list[dict[str, Any]]) -> IssueLedgerResult:
    issue_id = "issue:4sa-yield"
    agenda_ids = [str(item["agenda_id"]) for item in agendas]
    events = [
        IssueStateEvent(
            week=str(item["week"]),
            agenda_id=str(item["agenda_id"]),
            state=str(item["state"]),
            event_type="created" if index == 0 else "updated",
        )
        for index, item in enumerate(agendas)
    ]
    issue = WikiIssue(
        issue_id=issue_id,
        category_paths=["dram/spica/4sa"],
        topic="yield",
        title="4SA 수율 하락",
        current_status="ongoing",
        agenda_ids=agenda_ids,
        state_history=events,
        first_seen_week=str(agendas[0]["week"]),
        last_updated_week=str(agendas[-1]["updated_week"]),
    )
    return IssueLedgerResult(
        issues={issue_id: issue},
        agenda_to_issue={agenda_id: issue_id for agenda_id in agenda_ids},
        changed_issue_ids=[issue_id],
        review_items=[],
    )


def valid_dynamic_document(context: dict[str, Any]) -> MergedWikiDocument:
    mail_ids = sorted(context["evidence_by_mail"])
    citations = "".join("[mail:" + mail_id + "]" for mail_id in mail_ids)
    body = "## 이번 주 상태\n\n"
    if citations:
        body += "근거가 반영됐다. " + citations
    return MergedWikiDocument(
        title=context["node"]["title"],
        current_body_markdown=body,
        used_claim_ids=sorted(context["required_claim_ids"]),
        used_issue_ids=sorted(context["required_issue_ids"]),
        weekly_delta=(
            "이번 주 근거가 반영됐다. " + citations
            if context["has_weekly_change"]
            else ""
        ),
        confidence="high" if citations else "low",
        review_items=[],
    )


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


class StatefulIndices:
    def __init__(self, owner):
        self.owner = owner
        self.refreshed: list[str] = []

    def exists(self, *, index):
        return index in self.owner.documents

    def create(self, *, index, body):
        self.owner.documents[index] = {}
        self.owner.mappings[index] = body

    def get_mapping(self, *, index):
        return {index: self.owner.mappings[index]}

    def put_mapping(self, *, index, body):
        properties = self.owner.mappings[index]["mappings"]["properties"]
        properties.update(body["properties"])

    def refresh(self, *, index):
        self.refreshed.append(index)


class StatefulWikiOpenSearch:
    def __init__(self, *, weekly_mail, mail_agendas):
        self.documents = {
            SOURCE_INDEX: dict(weekly_mail),
            AGENDA_INDEX: dict(mail_agendas),
        }
        self.mappings = {
            SOURCE_INDEX: {"mappings": {"properties": {}}},
            AGENDA_INDEX: {"mappings": {"properties": {}}},
        }
        self.indices = StatefulIndices(self)

    def search(self, *, index, body):
        records = sorted(self.documents.get(index, {}).items())
        page_kind = body.get("query", {}).get("term", {}).get("page_kind")
        if page_kind:
            records = [
                (document_id, source)
                for document_id, source in records
                if source.get("page_kind") == page_kind
            ]
        search_after = body.get("search_after")
        if search_after:
            records = [
                item for item in records if item[0] > str(search_after[0])
            ]
        hits = [
            {"_id": document_id, "_source": source, "sort": [document_id]}
            for document_id, source in records[: body.get("size", 10)]
        ]
        return {"hits": {"hits": hits}}

    def mget(self, *, index, body):
        records = self.documents.get(index, {})
        return {
            "docs": [
                (
                    {
                        "_id": document_id,
                        "found": True,
                        "_source": records[document_id],
                    }
                    if document_id in records
                    else {"_id": document_id, "found": False}
                )
                for document_id in body["ids"]
            ]
        }

    def get(self, *, index, id):
        return {
            "_id": id,
            "_source": self.documents[index][id],
            "found": True,
        }

    def apply_bulk(self, actual_client, actions):
        assert actual_client is self
        batch = list(actions)
        for action in batch:
            self.documents.setdefault(action["_index"], {})[
                action["_id"]
            ] = dict(action["_source"])
        return len(batch), []

    def latest_page(self, category_id):
        return self.documents[PAGE_INDEX][category_id]


def source_part(mail_id: str, week: str, text: str) -> dict[str, Any]:
    return {
        "mail_id": mail_id,
        "week": week,
        "text": text,
        "part_index": 0,
        "subject": "[Spica] 4SA 주간 수율",
    }


def agenda_document(
    agenda_id: str,
    mail_id: str,
    source_doc_id: str,
    week: str,
    state: str,
) -> dict[str, Any]:
    return {
        "agenda_id": agenda_id,
        "mail_id": mail_id,
        "week": week,
        "updated_week": week,
        "summary": "4SA " + state,
        "source_quote": "4SA " + state,
        "state": state,
        "topic": "yield",
        "scope": "lotcd",
        "subject": (
            "[Spica] 4SA 주간 수율"
            if week == "2026-W28"
            else "RE: [Spica] 4SA 주간 수율"
        ),
        "target_paths": [
            {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
        ],
        "candidate_paths": [],
        "review_status": "confirmed",
        "confidence": 0.99,
        "source_doc_ids": [source_doc_id],
        "content_hash": agenda_id + ":" + state,
        "created_at": "2026-07-07T00:00:00+00:00",
        "updated_at": "2026-07-07T00:00:00+00:00",
        "issue_id_source": "derived",
    }


def recording_merger(calls: list[str]):
    def merge(context: dict[str, Any]) -> MergedWikiDocument:
        calls.append(context["node"]["id"])
        citations = "".join(
            "[mail:" + mail_id + "]"
            for mail_id in sorted(context["evidence_by_mail"])
        )
        current_body = "## 통합 현황"
        if citations:
            current_body += "\n\n근거를 통합했다. " + citations
        return MergedWikiDocument(
            title=context["node"]["title"],
            current_body_markdown=current_body,
            used_claim_ids=sorted(context["required_claim_ids"]),
            used_issue_ids=sorted(context["required_issue_ids"]),
            weekly_delta=(
                "이번 주 근거를 갱신했다. " + citations
                if context["has_weekly_change"]
                else ""
            ),
            confidence="high" if citations else "low",
            review_items=[],
        )

    return merge


def test_supported_claim_requires_mail_and_agenda_evidence():
    with pytest.raises(ValidationError):
        SupportedClaim(text="4SA 수율이 하락했다", mail_ids=[], agenda_ids=[])


def test_dynamic_document_accepts_content_specific_headings():
    document = MergedWikiDocument(
        title="4SA",
        current_body_markdown=(
            "## Chamber A 편차와 수율 하락\n\n"
            "수율 하락 원인은 chamber A 편차로 확인됐다. [mail:mail-28]\n\n"
            "## 조건 원복 후 검증\n\n"
            "조건 원복 후 재측정 중이다. [mail:mail-29]"
        ),
        used_claim_ids=["claim-root-cause", "claim-action"],
        used_issue_ids=["issue-4sa-yield"],
        weekly_delta="원인 확인과 조건 원복이 진행됐다. [mail:mail-28][mail:mail-29]",
        confidence="high",
        review_items=[],
    )
    context = MergeValidationContext(
        evidence_by_mail={
            "mail-28": ["agenda-28"],
            "mail-29": ["agenda-29"],
        },
        source_docs_by_mail={
            "mail-28": ["chunk-28"],
            "mail-29": ["chunk-29"],
        },
        required_claim_ids={"claim-root-cause", "claim-action"},
        required_issue_ids={"issue-4sa-yield"},
        resolved_issue_ids=set(),
        reopened_issue_ids=set(),
        stale_claim_texts=[],
    )

    citations = validate_merged_document(document, context)

    assert [item.mail_id for item in citations] == ["mail-28", "mail-29"]
    assert {item for citation in citations for item in citation.source_doc_ids} == {
        "chunk-28",
        "chunk-29",
    }


def test_dynamic_document_rejects_missing_claim_coverage():
    document = MergedWikiDocument(
        title="4SA",
        current_body_markdown="## 상태\n\n조건 원복 중이다. [mail:mail-29]",
        used_claim_ids=["claim-action"],
        used_issue_ids=["issue-4sa-yield"],
        weekly_delta="조건 원복 중이다. [mail:mail-29]",
        confidence="medium",
        review_items=[],
    )
    context = MergeValidationContext(
        evidence_by_mail={"mail-29": ["agenda-29"]},
        source_docs_by_mail={"mail-29": ["chunk-29"]},
        required_claim_ids={"claim-root-cause", "claim-action"},
        required_issue_ids={"issue-4sa-yield"},
        resolved_issue_ids=set(),
        reopened_issue_ids=set(),
        stale_claim_texts=[],
    )

    with pytest.raises(NarrativeValidationError, match="missing claim coverage"):
        validate_merged_document(document, context)


def test_integrated_mapping_adds_structured_fields_without_vectors():
    properties = integrated_page_index_definition()["mappings"]["properties"]
    assert properties["doc_type"]["type"] == "keyword"
    assert properties["weekly_history"]["type"] == "nested"
    assert properties["citation_map"]["type"] == "nested"
    assert "embedding" not in properties


def test_incremental_mapping_adds_strategy_issue_and_source_fields():
    properties = integrated_page_index_definition()["mappings"]["properties"]
    assert properties["issue_ids"]["type"] == "keyword"
    assert properties["schema_version"]["type"] == "integer"
    assert properties["generation_strategy"]["type"] == "keyword"
    assert properties["weekly_history"]["properties"]["agenda_ids"]["type"] == "keyword"
    assert properties["weekly_history"]["properties"]["source_doc_ids"]["type"] == "keyword"
    assert properties["citation_map"]["properties"]["source_doc_ids"]["type"] == "keyword"
    assert "embedding" not in properties


def test_weekly_delta_contains_new_and_corrected_agendas_only():
    agendas = [
        {**one_open_agenda(), "week": "2026-W28", "updated_week": "2026-W28"},
        {
            **one_open_agenda(),
            "agenda_id": "agenda-corrected",
            "week": "2026-W28",
            "updated_week": "2026-W29",
        },
        {
            **one_open_agenda(),
            "agenda_id": "agenda-new",
            "week": "2026-W29",
            "updated_week": "2026-W29",
        },
    ]

    delta = select_weekly_delta(agendas, "2026-W29")

    assert [item["agenda_id"] for item in delta] == [
        "agenda-corrected",
        "agenda-new",
    ]


def test_w29_merges_only_4sa_spica_dram_and_keeps_dynamic_headings(taxonomy):
    calls = []
    saved = []
    agendas = [
        {
            **one_open_agenda(),
            "updated_week": "2026-W28",
            "content_hash": "w28",
        },
        w29_4sa_agenda(),
    ]
    previous = {
        "lotcd:4sa": {
            **complete_page_fixture("lotcd:4sa", "DRAM", "Spica", "4SA"),
            "current_body_markdown": "## 수율 하락\n\n원인 분석 중이다. [mail:mail-28]",
            "source_hash": "old-lot",
        },
        "tech:dram:spica": {
            **complete_page_fixture("tech:dram:spica", "DRAM", "Spica", None),
            "source_hash": "old-tech",
        },
        "domain:dram": {
            **complete_page_fixture("domain:dram", "DRAM", None, None),
            "source_hash": "old-domain",
        },
    }

    def merge(context):
        node_id = context["node"]["id"]
        calls.append(node_id)
        mail_ids = sorted(context["evidence_by_mail"])
        citations = "".join("[mail:" + mail_id + "]" for mail_id in mail_ids)
        return MergedWikiDocument(
            title=context["node"]["title"],
            current_body_markdown=(
                "## Chamber A 원인과 조건 원복\n\n"
                "원인 확인 후 조건 원복을 진행 중이다. " + citations
            ),
            used_claim_ids=sorted(context["required_claim_ids"]),
            used_issue_ids=sorted(context["required_issue_ids"]),
            weekly_delta="이번 주 원인과 조치가 갱신됐다. " + citations,
            confidence="high",
            review_items=[],
        )

    result = build_incremental_pages(
        taxonomy,
        agendas,
        previous,
        issue_result_for(agendas),
        as_of_week="2026-W29",
        merge=merge,
        on_page=saved.append,
    )

    assert calls == ["lotcd:4sa", "tech:dram:spica", "domain:dram"]
    assert [page["category_id"] for page in saved] == calls
    assert not result.failures
    assert "## 개요" not in saved[0]["current_body_markdown"]
    assert saved[0]["weekly_history"][0]["week"] == "2026-W29"


def test_parent_failure_keeps_saved_child_and_marks_ancestor_pending(taxonomy):
    saved = []

    def first_merge(context):
        if context["node"]["id"] == "tech:dram:spica":
            raise RuntimeError("provider timeout")
        return valid_dynamic_document(context)

    agenda = w29_4sa_agenda()
    issue_result = issue_result_for([agenda])
    first = build_incremental_pages(
        taxonomy,
        [agenda],
        previous_page_chain(),
        issue_result,
        as_of_week="2026-W29",
        merge=first_merge,
        on_page=saved.append,
    )

    assert [page["category_id"] for page in saved] == ["lotcd:4sa"]
    assert first.failures == {"tech:dram:spica": "provider timeout"}
    assert first.pending == {"domain:dram": "required child failed"}

    resumed_previous = previous_page_chain()
    resumed_previous["lotcd:4sa"] = saved[0]
    resumed_calls = []

    def resumed_merge(context):
        resumed_calls.append(context["node"]["id"])
        return valid_dynamic_document(context)

    second = build_incremental_pages(
        taxonomy,
        [agenda],
        resumed_previous,
        issue_result,
        as_of_week="2026-W29",
        merge=resumed_merge,
    )

    assert resumed_calls == ["tech:dram:spica", "domain:dram"]
    assert second.skipped == ["lotcd:4sa"]
    assert second.failures == {}
    assert second.pending == {}


def test_category_without_delta_gets_no_history_entry(taxonomy):
    def unexpected_merge(context):
        pytest.fail("merge must not run when no category is affected")

    result = build_incremental_pages(
        taxonomy,
        [],
        previous_page_chain(),
        IssueLedgerResult(
            issues={},
            agenda_to_issue={},
            changed_issue_ids=[],
            review_items=[],
        ),
        as_of_week="2026-W29",
        merge=unexpected_merge,
    )

    assert result.pages == []
    assert result.failures == {}
    assert result.pending == {}
    assert result.skipped == []


def test_lotcd_delta_affects_only_lotcd_and_ancestors(taxonomy):
    ids = affected_node_ids(
        taxonomy,
        [
            {
                **one_open_agenda(),
                "target_paths": [
                    {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
                ],
            }
        ],
    )

    assert ids == {"lotcd:4sa", "tech:dram:spica", "domain:dram"}


def test_affected_nodes_use_taxonomy_node_ids(taxonomy, monkeypatch):
    nodes = [
        CategoryNode("dram-node", "domain", "DRAM", None, None, "DRAM"),
        CategoryNode("spica-node", "tech", "DRAM", "Spica", None, "Spica"),
        CategoryNode("4sa-node", "lotcd", "DRAM", "Spica", "4SA", "4SA"),
    ]
    monkeypatch.setattr(wiki_builder_module, "category_nodes", lambda _: nodes)

    ids = affected_node_ids(taxonomy, [one_open_agenda()])

    assert ids == {"4sa-node", "spica-node", "dram-node"}


def test_no_delta_affects_no_pages(taxonomy):
    assert affected_node_ids(taxonomy, []) == set()


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


def test_quiet_resolved_issue_uses_timeline_evidence_without_prompt_expansion(
    taxonomy,
):
    agenda = {
        "agenda_id": "agenda-27-terminal",
        "mail_id": "mail-27-terminal",
        "week": "2026-W27",
        "state": "resolved",
        "review_status": "confirmed",
        "issue_id": "issue-quiet-resolved",
        "summary": "W27 해결",
        "subject": "W27 weekly",
        "topic": "yield",
        "source_doc_ids": ["chunk-27"],
        "target_paths": [
            {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
        ],
        "candidate_paths": [],
    }
    previous = {
        "lotcd:4sa": {
            "current_body_markdown": "",
            "weekly_history": [
                {
                    "week": "2026-W27",
                    "body_markdown": "W27 해결 [mail:mail-27-terminal]",
                    "source_mail_ids": ["mail-27-terminal"],
                }
            ],
            "citation_map": [
                {
                    "mail_id": "mail-27-terminal",
                    "agenda_ids": ["agenda-27-terminal"],
                    "used_in_sections": ["2026-W27"],
                    "category_paths": ["dram/spica/4sa"],
                }
            ],
        }
    }
    contexts = {}

    def analyze(context):
        contexts[context["node"]["id"]] = context
        return analysis_with_expected_issues(context)

    result = build_integrated_pages(
        taxonomy,
        [agenda],
        previous,
        as_of_week="2026-W29",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert contexts["lotcd:4sa"]["allowed_agendas"] == []
    assert contexts["lotcd:4sa"]["issue_timelines"][0]["events"][0][
        "agenda_id"
    ] == "agenda-27-terminal"
    assert not result.failures
    page = next(item for item in result.pages if item["category_id"] == "lotcd:4sa")
    assert page["resolved_issue_ids"] == ["issue-quiet-resolved"]


def test_legacy_tech_and_domain_bootstrap_descendant_history_without_prompt_leak(
    taxonomy,
):
    agenda = {
        "agenda_id": "agenda-27-lotcd",
        "mail_id": "mail-27-lotcd",
        "week": "2026-W27",
        "state": "open",
        "review_status": "confirmed",
        "issue_id": "issue-27-lotcd",
        "summary": "W27 LOTCD 상태",
        "subject": "W27 weekly",
        "topic": "yield",
        "source_doc_ids": ["chunk-27"],
        "target_paths": [
            {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
        ],
        "candidate_paths": [],
    }
    previous = {
        category_id: {
            "category_id": category_id,
            "page_kind": "latest",
            "as_of_week": "2026-W27",
            "body_markdown": f"legacy {category_id}",
        }
        for category_id in ("tech:dram:spica", "domain:dram")
    }
    contexts = {}

    def analyze(context):
        contexts[context["node"]["id"]] = context
        return analysis_with_expected_issues(context)

    result = build_integrated_pages(
        taxonomy,
        [agenda],
        previous,
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )
    pages = {page["category_id"]: page for page in result.pages}

    assert not result.failures
    for category_id in ("tech:dram:spica", "domain:dram"):
        assert contexts[category_id]["allowed_agendas"] == []
        assert contexts[category_id]["issue_timelines"] == []
        assert [
            item["week"] for item in pages[category_id]["weekly_history"]
        ] == ["2026-W28", "2026-W27"]
        assert pages[category_id]["weekly_history"][1][
            "source_mail_ids"
        ] == ["mail-27-lotcd"]
        citation = next(
            item
            for item in pages[category_id]["citation_map"]
            if item["mail_id"] == "mail-27-lotcd"
        )
        assert citation["agenda_ids"] == ["agenda-27-lotcd"]
        assert "W27 LOTCD 상태 [mail:mail-27-lotcd]" in pages[category_id][
            "body_markdown"
        ]


def test_render_current_body_preserves_dynamic_document_markdown():
    document = MergedWikiDocument(
        title="4SA",
        current_body_markdown="## 조건 원복 후 검증\n\n재측정 중이다.",
        weekly_delta="separate history entry",
        confidence="high",
    )

    rendered = render_current_body(document)

    assert rendered == "## 조건 원복 후 검증\n\n재측정 중이다."


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


def test_structured_invocation_retries_once_after_output_parser_error():
    valid = NarrativeDraft(
        overview="",
        current_status="",
        cause_and_impact="",
        actions_and_effects="",
        pending_and_decisions="",
        accumulated_knowledge="",
        weekly_update="",
        confidence="low",
    )

    class FakeRunnable:
        def __init__(self):
            self.calls = 0

        def invoke(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise OutputParserException("unterminated tool arguments")
            return valid

    runnable = FakeRunnable()

    assert invoke_structured(
        runnable, [{"role": "user", "content": "evidence"}]
    ) == valid
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


def test_analysis_uses_deterministic_issue_decisions_when_model_omits_them(
    taxonomy,
):
    contexts = []

    def analyze(context):
        contexts.append(context)
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        single_lotcd_taxonomy(taxonomy),
        [one_open_agenda()],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert not result.failures
    assert [item["node"]["id"] for item in contexts] == [
        "lotcd:4sa",
        "tech:dram:spica",
        "domain:dram",
    ]
    assert all("validation_feedback" not in item for item in contexts)


def test_analysis_context_exposes_expected_issue_statuses(taxonomy):
    contexts = {}

    def analyze(context):
        contexts[context["node"]["id"]] = context
        return analysis_with_expected_issues(context)

    result = build_integrated_pages(
        single_lotcd_taxonomy(taxonomy),
        [one_open_agenda()],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert not result.failures
    assert contexts["lotcd:4sa"]["expected_issue_statuses"] == {
        "issue-open": "ongoing"
    }


def test_analysis_semantic_retry_recovers_from_empty_structured_response(taxonomy):
    lotcd_contexts = []

    def analyze(context):
        if context["node"]["id"] == "lotcd:4sa":
            lotcd_contexts.append(context)
            if len(lotcd_contexts) == 1:
                return None
        return analysis_with_expected_issues(context)

    result = build_integrated_pages(
        single_lotcd_taxonomy(taxonomy),
        [one_open_agenda()],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert not result.failures
    assert len(lotcd_contexts) == 2
    assert "no structured response" in lotcd_contexts[1]["validation_feedback"]


def test_draft_semantic_retry_supplies_validator_feedback(taxonomy):
    contexts = []

    def make_draft(context, analysis):
        if context["node"]["id"] == "lotcd:4sa":
            contexts.append(context)
            if len(contexts) == 1:
                return empty_draft().model_copy(
                    update={"overview": "인용 없는 사실"}
                )
        return empty_draft()

    result = build_integrated_pages(
        single_lotcd_taxonomy(taxonomy),
        [one_open_agenda()],
        {},
        as_of_week="2026-W28",
        analyze=analysis_with_expected_issues,
        draft=make_draft,
    )

    assert not result.failures
    assert len(contexts) == 2
    assert "uncited factual unit" in contexts[1]["validation_feedback"]


def test_semantic_retry_stops_after_three_invalid_analyses(taxonomy):
    calls = []

    def analyze(context):
        if context["node"]["id"] == "lotcd:4sa":
            calls.append(context)
            return PageAnalysis(
                new_claims=[
                    SupportedClaim(
                        text="허용 범위 밖 주장",
                        mail_ids=["mail-unknown"],
                        agenda_ids=["agenda-unknown"],
                    )
                ],
                outline=["개요"],
            )
        return analysis_with_expected_issues(context)

    result = build_integrated_pages(
        single_lotcd_taxonomy(taxonomy),
        [one_open_agenda()],
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert len(calls) == 3
    assert result.pages == []
    assert "unknown agenda" in result.failures["lotcd:4sa"]
    assert result.failures["tech:dram:spica"] == "required child failed"
    assert result.failures["domain:dram"] == "required child failed"


def test_deterministic_issue_decisions_use_latest_timeline_evidence():
    decisions = wiki_builder_module.deterministic_issue_decisions(
        [
            {
                "issue_id": "issue-open",
                "title": "4SA 수율 하락",
                "event_type": "updated",
                "is_open": True,
                "events": [
                    {
                        "agenda_id": "agenda-27",
                        "mail_id": "mail-27",
                    },
                    {
                        "agenda_id": "agenda-28",
                        "mail_id": "mail-28",
                    },
                ],
            }
        ],
        [],
    )

    assert decisions == [
        IssueDecision(
            issue_id="issue-open",
            status="ongoing",
            summary="4SA 수율 하락",
            mail_ids=["mail-28"],
            agenda_ids=["agenda-28"],
        )
    ]


def test_deterministic_issue_decisions_merge_child_evidence():
    child = ChildDigest(
        canonical_id="dram/spica/4sa",
        as_of_week="2026-W28",
        summary="4SA 요약",
        claims=[],
        issues=[
            IssueDecision(
                issue_id="issue-open",
                status="ongoing",
                summary="4SA 수율 하락",
                mail_ids=["mail-28"],
                agenda_ids=["agenda-28"],
            )
        ],
        confidence="high",
    )

    assert wiki_builder_module.deterministic_issue_decisions([], [child]) == child.issues


def test_llm_extra_body_uses_optional_reasoning_effort(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_LLM_REASONING_EFFORT", raising=False)
    assert wiki_builder_module._llm_extra_body() is None

    monkeypatch.setenv("KNOWLEDGE_LLM_REASONING_EFFORT", "none")
    assert wiki_builder_module._llm_extra_body() == {
        "reasoning": {"effort": "none"}
    }


def test_llm_generators_skip_model_for_empty_leaf(monkeypatch):
    class FailingRunnable:
        def invoke(self, messages):
            raise AssertionError("empty leaf must not call the model")

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            pass

        def with_structured_output(self, schema, method):
            return FailingRunnable()

    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)
    monkeypatch.setattr(
        "integrated_wiki_builder.llm_connection",
        lambda: SimpleNamespace(
            model="z-ai/glm-4.7",
            api_key=SecretStr("test"),
            base_url="https://openrouter.ai/api/v1",
        ),
    )
    analyze, draft = build_llm_generators()
    context = {
        "allowed_agendas": [],
        "issue_timelines": [],
        "child_digests": [],
        "previous_current_body_markdown": "",
        "recent_history": [],
    }

    analysis = analyze(context)
    narrative = draft(context, analysis)

    assert analysis == PageAnalysis(outline=["개요"])
    assert narrative == empty_draft()


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
            "agenda_id": "agenda-reopened-terminal",
            "mail_id": "mail-reopened-terminal",
            "issue_id": "issue-reopened",
            "week": "2026-W27",
            "state": "resolved",
        },
        {
            "agenda_id": "agenda-open",
            "mail_id": "mail-open",
            "issue_id": "issue-reopened",
            "week": "2026-W28",
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


def test_issue_validation_rejects_terminal_only_ongoing_evidence():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="ongoing",
                summary="진행 중",
                mail_ids=["mail-terminal"],
                agenda_ids=["agenda-terminal"],
            )
        ],
    )

    with pytest.raises(NarrativeValidationError, match="non-terminal"):
        validate_issue_decisions(
            analysis,
            [
                {
                    "agenda_id": "agenda-terminal",
                    "mail_id": "mail-terminal",
                    "issue_id": "issue-1",
                    "week": "2026-W27",
                    "state": "resolved",
                }
            ],
        )


def test_issue_validation_accepts_ongoing_with_nonterminal_evidence():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="ongoing",
                summary="진행 중",
                mail_ids=["mail-open"],
                agenda_ids=["agenda-open"],
            )
        ],
    )

    validate_issue_decisions(
        analysis,
        [
            {
                "agenda_id": "agenda-open",
                "mail_id": "mail-open",
                "issue_id": "issue-1",
                "week": "2026-W28",
                "state": "open",
            }
        ],
    )


def test_issue_validation_accepts_terminal_then_later_nonterminal_reopened():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="reopened",
                summary="재발",
                mail_ids=["mail-reopened"],
                agenda_ids=["agenda-reopened"],
            )
        ],
    )

    validate_issue_decisions(
        analysis,
        [
            {
                "agenda_id": "agenda-terminal",
                "mail_id": "mail-terminal",
                "issue_id": "issue-1",
                "week": "2026-W27",
                "state": "resolved",
            },
            {
                "agenda_id": "agenda-reopened",
                "mail_id": "mail-reopened",
                "issue_id": "issue-1",
                "week": "2026-W28",
                "state": "open",
            },
        ],
    )


def test_issue_validation_accepts_compacted_deterministic_reopened_proof():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="reopened",
                summary="재발 후 모니터링",
                mail_ids=["mail-monitoring"],
                agenda_ids=["agenda-monitoring"],
            )
        ],
    )

    validate_issue_decisions(
        analysis,
        [
            {
                "agenda_id": "agenda-first",
                "mail_id": "mail-first",
                "issue_id": "issue-1",
                "week": "2026-W24",
                "state": "open",
            },
            {
                "agenda_id": "agenda-reopened",
                "mail_id": "mail-reopened",
                "issue_id": "issue-1",
                "week": "2026-W26",
                "state": "open",
            },
            {
                "agenda_id": "agenda-monitoring",
                "mail_id": "mail-monitoring",
                "issue_id": "issue-1",
                "week": "2026-W27",
                "state": "monitoring",
            },
        ],
        reopened_evidence_ids={"issue-1": {"agenda-reopened", "agenda-monitoring"}},
    )


def test_issue_validation_rejects_preterminal_citation_with_reopened_proof():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="reopened",
                summary="잘못된 재발 근거",
                mail_ids=["mail-first"],
                agenda_ids=["agenda-first"],
            )
        ],
    )

    with pytest.raises(NarrativeValidationError, match="later than terminal"):
        validate_issue_decisions(
            analysis,
            [
                {
                    "agenda_id": "agenda-first",
                    "mail_id": "mail-first",
                    "issue_id": "issue-1",
                    "week": "2026-W24",
                    "state": "open",
                },
                {
                    "agenda_id": "agenda-reopened",
                    "mail_id": "mail-reopened",
                    "issue_id": "issue-1",
                    "week": "2026-W26",
                    "state": "open",
                },
            ],
            reopened_evidence_ids={"issue-1": {"agenda-reopened"}},
        )


def test_issue_validation_rejects_nonterminal_before_terminal_as_reopened():
    analysis = PageAnalysis(
        outline=["개요"],
        issue_decisions=[
            IssueDecision(
                issue_id="issue-1",
                status="reopened",
                summary="재발",
                mail_ids=["mail-open"],
                agenda_ids=["agenda-open"],
            )
        ],
    )

    with pytest.raises(NarrativeValidationError, match="later than terminal"):
        validate_issue_decisions(
            analysis,
            [
                {
                    "agenda_id": "agenda-open",
                    "mail_id": "mail-open",
                    "issue_id": "issue-1",
                    "week": "2026-W27",
                    "state": "open",
                },
                {
                    "agenda_id": "agenda-terminal",
                    "mail_id": "mail-terminal",
                    "issue_id": "issue-1",
                    "week": "2026-W28",
                    "state": "resolved",
                },
            ],
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
        "category_id": "",
        "canonical_id": "dram/spica/4sa",
        "current_body_markdown": "",
        "weekly_delta": "",
        "used_claim_ids": [],
        "used_issue_ids": [],
        "citation_map": [],
        "source_hash": "",
        "as_of_week": "2026-W28",
        "summary": "요약 [mail:mail-1]",
        "claims": [claim.model_dump()],
        "issues": [decision.model_dump()],
        "reopened_evidence_ids": {},
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
    ] == []

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


def test_compact_issue_timeline_keeps_latest_distinct_transition_event():
    states = ["open", "resolved", "reopened", "monitoring"]
    agendas = [
        {
            "agenda_id": f"agenda-{week}",
            "mail_id": f"mail-{week}",
            "week": f"2026-W{week}",
            "state": state,
            "review_status": "confirmed",
            "issue_id": "issue-transition",
            "summary": f"state {state}",
        }
        for week, state in zip(range(24, 28), states, strict=True)
    ]

    timeline = _compact_issue_timelines(agendas, "2026-W27")[0]

    assert [event["agenda_id"] for event in timeline["events"]] == [
        "agenda-24",
        "agenda-26",
        "agenda-27",
    ]
    assert len(timeline["events"]) <= 3


def test_parent_deterministically_promotes_child_issue_decisions(taxonomy):
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
    contexts = {}

    def analyze(context):
        contexts[context["node"]["id"]] = context
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        agendas,
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert not result.failures
    tech_child = next(
        digest
        for digest in contexts["tech:dram:spica"]["child_digests"]
        if digest["canonical_id"] == "dram/spica/4sa"
    )
    assert tech_child["issues"] == [
        {
            "issue_id": "issue-child",
            "status": "ongoing",
            "summary": "child issue",
            "mail_ids": ["mail-child"],
            "agenda_ids": ["agenda-child"],
        }
    ]


def test_parent_accepts_validated_reopened_child_issue_without_raw_terminal(taxonomy):
    agendas = [
        {
            "agenda_id": "agenda-terminal",
            "mail_id": "mail-terminal",
            "week": "2026-W27",
            "state": "resolved",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "child resolved",
            "subject": "4SA W27",
            "topic": "yield",
            "source_doc_ids": ["chunk-terminal"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        },
        {
            "agenda_id": "agenda-reopened",
            "mail_id": "mail-reopened",
            "week": "2026-W28",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "child reopened",
            "subject": "4SA W28",
            "topic": "yield",
            "source_doc_ids": ["chunk-reopened"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        },
    ]
    contexts = {}

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

    assert contexts["tech:dram:spica"]["allowed_agendas"] == []
    assert not result.failures


def test_reopened_proof_propagates_deterministically_lotcd_to_domain(taxonomy):
    agendas = [
        {
            "agenda_id": "agenda-preterminal",
            "mail_id": "mail-preterminal",
            "week": "2026-W26",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "child initially open",
            "subject": "4SA W26",
            "topic": "yield",
            "source_doc_ids": ["chunk-preterminal"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        },
        {
            "agenda_id": "agenda-terminal",
            "mail_id": "mail-terminal",
            "week": "2026-W27",
            "state": "resolved",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "child resolved",
            "subject": "4SA W27",
            "topic": "yield",
            "source_doc_ids": ["chunk-terminal"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        },
        {
            "agenda_id": "agenda-reopened",
            "mail_id": "mail-reopened",
            "week": "2026-W28",
            "state": "open",
            "review_status": "confirmed",
            "issue_id": "issue-child",
            "summary": "child reopened",
            "subject": "4SA W28",
            "topic": "yield",
            "source_doc_ids": ["chunk-reopened"],
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "candidate_paths": [],
        },
    ]

    contexts = {}

    def analyze(context):
        contexts[context["node"]["id"]] = context
        return PageAnalysis(outline=["개요"])

    result = build_integrated_pages(
        taxonomy,
        agendas,
        {},
        as_of_week="2026-W28",
        analyze=analyze,
        draft=lambda context, analysis: empty_draft(),
    )

    assert not result.failures
    tech_child = next(
        digest
        for digest in contexts["tech:dram:spica"]["child_digests"]
        if digest["canonical_id"] == "dram/spica/4sa"
    )
    domain_child = next(
        digest
        for digest in contexts["domain:dram"]["child_digests"]
        if digest["canonical_id"] == "dram/spica"
    )
    assert tech_child["reopened_evidence_ids"] == {
        "issue-child": ["agenda-reopened"]
    }
    assert domain_child["reopened_evidence_ids"] == {
        "issue-child": ["agenda-reopened"]
    }


def test_child_digest_defaults_reopened_evidence_mapping_for_old_inputs():
    digest = ChildDigest.model_validate(
        {
            "canonical_id": "dram/spica/4sa",
            "as_of_week": "2026-W28",
            "summary": "summary",
            "claims": [],
            "issues": [],
            "confidence": "low",
        }
    )

    assert digest.reopened_evidence_ids == {}


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


def test_save_integrated_page_writes_latest_and_same_week_snapshot(
    monkeypatch,
):
    client = SourceMgetClient(found_ids=["chunk-28"])
    actions = []
    monkeypatch.setattr(
        wiki_builder_module.helpers,
        "bulk",
        lambda actual_client, batch: actions.extend(batch),
    )
    page = complete_page_fixture("lotcd:4sa", "DRAM", "Spica", "4SA")

    save_integrated_page(client, page)

    assert [item["_id"] for item in actions] == [
        "lotcd:4sa",
        "lotcd:4sa:2026-W28",
    ]
    assert actions[0]["_source"]["page_kind"] == "latest"
    assert actions[1]["_source"]["page_kind"] == "snapshot"
    assert client.indices.refreshed == [PAGE_INDEX]


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
        merge=valid_dynamic_document,
    )

    assert stats == {
        "affected_pages": 3,
        "saved_pages": 3,
        "skipped_pages": 0,
        "failed": 0,
        "pending": 0,
    }
    assert [index for index, _ in client.indices.created] == [
        "category_wiki_pages",
        "wiki_issue_ledger",
    ]
    assert [index for index, _ in client.searches] == [
        "mail_agendas",
        "wiki_issue_ledger",
        "category_wiki_pages",
    ]
    assert len(captured) == 7


def test_rebuild_then_weekly_merge_is_idempotent_and_cited(
    monkeypatch, taxonomy
):
    client = StatefulWikiOpenSearch(
        weekly_mail={
            "chunk-28": source_part(
                "mail-28", "2026-W28", "4SA 수율 1.2%p 하락"
            ),
            "chunk-29": source_part(
                "mail-29", "2026-W29", "chamber A 원인 확인 후 조건 원복"
            ),
        },
        mail_agendas={
            "agenda-28": agenda_document(
                "agenda-28",
                "mail-28",
                "chunk-28",
                "2026-W28",
                "investigating",
            ),
            "agenda-29": agenda_document(
                "agenda-29",
                "mail-29",
                "chunk-29",
                "2026-W29",
                "in_progress",
            ),
        },
    )
    calls = []
    monkeypatch.setattr(
        wiki_builder_module.helpers,
        "bulk",
        client.apply_bulk,
    )
    monkeypatch.setattr(
        issue_ledger_module.helpers,
        "bulk",
        client.apply_bulk,
    )

    first = run(
        weeks=["2026-W28"],
        allow_external_llm=True,
        allow_dummy_taxonomy=True,
        rebuild_all=True,
        client=client,
        merge=recording_merger(calls),
    )
    calls.clear()
    second = run(
        weeks=["2026-W29"],
        allow_external_llm=True,
        allow_dummy_taxonomy=True,
        client=client,
        merge=recording_merger(calls),
    )
    calls_after_second = list(calls)
    calls.clear()
    third = run(
        weeks=["2026-W29"],
        allow_external_llm=True,
        allow_dummy_taxonomy=True,
        client=client,
        merge=recording_merger(calls),
    )

    assert first["failed"] == 0
    assert second["saved_pages"] == 3
    assert calls_after_second == [
        "lotcd:4sa",
        "tech:dram:spica",
        "domain:dram",
    ]
    assert third["saved_pages"] == 0
    assert calls == []
    page = client.latest_page("lotcd:4sa")
    assert [entry["week"] for entry in page["weekly_history"]] == [
        "2026-W29",
        "2026-W28",
    ]
    source_docs_by_mail: dict[str, set[str]] = {}
    for citation in page["citation_map"]:
        source_docs_by_mail.setdefault(citation["mail_id"], set()).update(
            citation["source_doc_ids"]
        )
    assert source_docs_by_mail == {
        "mail-28": {"chunk-28"},
        "mail-29": {"chunk-29"},
    }
    assert page["generation_strategy"] == "incremental_merge"


@pytest.mark.parametrize(
    ("rebuild_all", "expected_agenda_ids"),
    [
        (False, ["agenda-29"]),
        (True, ["agenda-28", "agenda-29"]),
    ],
)
def test_run_resolves_weekly_delta_or_all_rebuild_evidence(
    taxonomy,
    monkeypatch,
    rebuild_all,
    expected_agenda_ids,
):
    agendas = [one_open_agenda(), w29_4sa_agenda()]
    client = SourceMgetClient(found_ids=[])
    client.indices = FakeIndices(existing=["mail_agendas"])
    captured = {}
    empty_ledger = IssueLedgerResult(
        issues={},
        agenda_to_issue={},
        changed_issue_ids=[],
        review_items=[],
    )
    monkeypatch.setattr(wiki_builder_module, "load_taxonomy", lambda path=None: taxonomy)
    monkeypatch.setattr(wiki_builder_module, "fetch_agendas", lambda actual: agendas)
    monkeypatch.setattr(
        wiki_builder_module,
        "fetch_issue_ledger",
        lambda actual: {},
        raising=False,
    )

    def resolve(selected, existing, **kwargs):
        captured["agenda_ids"] = [item["agenda_id"] for item in selected]
        return empty_ledger

    monkeypatch.setattr(
        wiki_builder_module,
        "resolve_issue_ledger",
        resolve,
        raising=False,
    )
    monkeypatch.setattr(
        wiki_builder_module,
        "save_issue_ledger",
        lambda actual, issues: 0,
        raising=False,
    )
    monkeypatch.setattr(wiki_builder_module, "fetch_previous_pages", lambda actual: {})
    monkeypatch.setattr(
        wiki_builder_module,
        "build_incremental_pages",
        lambda *args, **kwargs: BuildResult(),
    )

    stats = run(
        weeks=["2026-W29"],
        allow_dummy_taxonomy=True,
        client=client,
        merge=valid_dynamic_document,
        rebuild_all=rebuild_all,
    )

    assert captured["agenda_ids"] == expected_agenda_ids
    assert stats["affected_pages"] == (23 if rebuild_all else 3)


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
        "integrated_wiki_builder.build_page_merger",
        lambda: generator_calls.append(True),
    )
    monkeypatch.setattr(
        "integrated_wiki_builder.save_integrated_page",
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
