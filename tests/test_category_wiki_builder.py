from __future__ import annotations

from datetime import UTC, datetime

import category_wiki_builder as category_builder_module

from category_wiki_builder import (
    apply_agenda_version,
    agenda_index_definition,
    build_page_documents,
    category_nodes,
    fetch_source_mails,
    merge_overlapping_chunks,
    normalize_agenda_document,
    page_index_definition,
    replace_mail_agendas,
)
from knowledge_models import TaxonomyDocument


def test_apply_agenda_version_marks_new_agenda():
    current = {
        "agenda_id": "agenda-29",
        "mail_id": "2026-W29:Spica:mail-1",
        "week": "2026-W29",
        "summary": "4SA chamber A 원복",
        "source_quote": "조건을 원복했습니다.",
        "state": "in_progress",
        "topic": "action",
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "source_doc_ids": ["chunk-29"],
    }
    versioned = apply_agenda_version(
        current,
        None,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert versioned["created_at"] == "2026-07-14T00:00:00+00:00"
    assert versioned["updated_at"] == "2026-07-14T00:00:00+00:00"
    assert versioned["updated_week"] == "2026-W29"
    assert len(versioned["content_hash"]) == 64


def test_apply_agenda_version_preserves_unchanged_metadata():
    previous = {
        "agenda_id": "agenda-29",
        "mail_id": "2026-W29:Spica:mail-1",
        "week": "2026-W29",
        "summary": "4SA chamber A 원복",
        "source_quote": "조건을 원복했습니다.",
        "state": "in_progress",
        "topic": "action",
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "source_doc_ids": ["chunk-29"],
        "created_at": "2026-07-13T00:00:00+00:00",
        "updated_at": "2026-07-13T00:00:00+00:00",
        "updated_week": "2026-W29",
    }
    first = apply_agenda_version(
        previous,
        None,
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )
    unchanged = apply_agenda_version(
        previous,
        first,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert unchanged["created_at"] == first["created_at"]
    assert unchanged["updated_at"] == first["updated_at"]
    assert unchanged["updated_week"] == first["updated_week"]
    assert unchanged["content_hash"] == first["content_hash"]


def test_apply_agenda_version_migrates_unversioned_agenda():
    previous = {
        "agenda_id": "agenda-28",
        "mail_id": "2026-W28:Spica:mail-1",
        "week": "2026-W28",
        "summary": "4SA 원인 분석",
        "source_quote": "원인 분석 중입니다.",
        "state": "investigating",
        "topic": "yield",
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "source_doc_ids": ["chunk-28"],
        "created_at": "2026-07-07T00:00:00+00:00",
    }

    versioned = apply_agenda_version(
        previous,
        previous,
        now=datetime(2026, 7, 14, tzinfo=UTC),
        observed_week="2026-W29",
    )

    assert versioned["created_at"] == previous["created_at"]
    assert versioned["updated_at"] == "2026-07-14T00:00:00+00:00"
    assert versioned["updated_week"] == "2026-W29"
    assert len(versioned["content_hash"]) == 64


def test_apply_agenda_version_marks_correction_in_requested_week():
    previous = apply_agenda_version(
        {
            "agenda_id": "agenda-28",
            "mail_id": "2026-W28:Spica:mail-1",
            "week": "2026-W28",
            "summary": "4SA 원인 분석",
            "source_quote": "원인 분석 중입니다.",
            "state": "investigating",
            "topic": "yield",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "source_doc_ids": ["chunk-28"],
        },
        None,
        now=datetime(2026, 7, 7, tzinfo=UTC),
    )
    corrected = {
        **previous,
        "summary": "4SA chamber A 원인 확인",
        "state": "confirmed",
        "updated_week": "2026-W29",
    }

    versioned = apply_agenda_version(
        corrected,
        previous,
        now=datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert versioned["created_at"] == previous["created_at"]
    assert versioned["updated_at"] == "2026-07-14T00:00:00+00:00"
    assert versioned["updated_week"] == "2026-W29"
    assert versioned["content_hash"] != previous["content_hash"]


def test_apply_agenda_version_preserves_old_path_when_reclassified():
    previous = {
        "agenda_id": "agenda-28",
        "mail_id": "mail-28",
        "week": "2026-W28",
        "summary": "분류 정정",
        "source_quote": "분류 정정",
        "state": "open",
        "topic": "yield",
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "source_doc_ids": ["chunk-28"],
    }
    current = {
        **previous,
        "target_paths": [{"domain": "NAND", "tech": "Heraion", "lotcd": "4H1"}],
    }

    versioned = apply_agenda_version(
        current,
        previous,
        now=datetime(2026, 7, 14, tzinfo=UTC),
        observed_week="2026-W29",
    )

    assert versioned["previous_target_paths"] == previous["target_paths"]
    assert versioned["is_deleted"] is False


def test_replace_mail_agendas_retains_removed_agenda_as_tombstone(monkeypatch):
    previous = {
        "agenda_id": "agenda-removed",
        "mail_id": "mail-28",
        "raw_mail_id": "raw-28",
        "week": "2026-W28",
        "updated_week": "2026-W28",
        "summary": "기존 수율 이슈",
        "source_quote": "기존 수율 이슈",
        "state": "open",
        "topic": "yield",
        "review_status": "confirmed",
        "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
        "candidate_paths": [],
        "source_doc_ids": ["chunk-28"],
        "created_at": "2026-07-07T00:00:00+00:00",
    }

    class Client:
        def search(self, *, index, body):
            return {
                "hits": {
                    "hits": [
                        {"_id": "agenda-removed", "_source": previous, "sort": ["agenda-removed"]}
                    ]
                }
            }

        def delete_by_query(self, **kwargs):
            return None

    actions = []
    monkeypatch.setattr(
        category_builder_module.helpers,
        "bulk",
        lambda client, batch: actions.extend(batch),
    )

    replace_mail_agendas(Client(), "mail-28", [], observed_week="2026-W29")

    tombstone = actions[0]["_source"]
    assert tombstone["agenda_id"] == "agenda-removed"
    assert tombstone["is_deleted"] is True
    assert tombstone["updated_week"] == "2026-W29"
    assert tombstone["target_paths"] == previous["target_paths"]
    assert tombstone["previous_target_paths"] == previous["target_paths"]
    assert tombstone["source_doc_ids"] == ["chunk-28"]


def test_agenda_mapping_supports_deletion_and_reclassification_metadata():
    properties = agenda_index_definition()["mappings"]["properties"]

    assert properties["is_deleted"] == {"type": "boolean"}
    assert properties["previous_target_paths"]["type"] == "nested"


def taxonomy() -> TaxonomyDocument:
    return TaxonomyDocument.model_validate_json(
        open("fixtures/knowledge/taxonomy.json", encoding="utf-8").read()
    )


def agenda(
    agenda_id: str,
    week: str,
    state: str,
    summary: str,
    *,
    issue_id: str = "issue:4sa-yield",
    review_status: str = "confirmed",
) -> dict:
    path = {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
    return {
        "agenda_id": agenda_id,
        "mail_id": f"{week}:Spica:mail-1",
        "source_doc_ids": [f"source-{agenda_id}"],
        "week": week,
        "subject": "Spica 주간 수율",
        "sender_team": "Spica수율",
        "summary": summary,
        "source_quote": summary,
        "scope": "lotcd",
        "topic": "yield",
        "state": state,
        "issue_id": issue_id,
        "confidence": 0.99,
        "review_status": review_status,
        "target_paths": [path] if review_status == "confirmed" else [],
        "candidate_paths": [path] if review_status != "confirmed" else [],
    }


def test_new_indices_have_no_vector_or_embedding_fields():
    agenda_properties = agenda_index_definition()["mappings"]["properties"]
    page_properties = page_index_definition()["mappings"]["properties"]

    assert "embedding" not in agenda_properties
    assert "embedding" not in page_properties
    assert agenda_properties["week"]["type"] == "keyword"
    assert page_properties["body_markdown"]["type"] == "text"


def test_overlap_chunks_are_reassembled_without_duplicate_text():
    assert merge_overlapping_chunks(["ABCDE", "DEFGH", "GHIJK"]) == "ABCDEFGHIJK"


def test_legacy_agenda_is_normalized_without_embedding_or_sqlite_lookup():
    normalized = normalize_agenda_document(
        {
            "agenda_id": "legacy-1",
            "received_at": "2026-07-08T09:00:00+09:00",
            "topic": "yield",
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
        }
    )

    assert normalized["week"] == "2026-28"
    assert normalized["issue_id"].startswith("issue:")
    assert normalized["source_doc_ids"] == []


def test_fetch_source_mails_reads_text_metadata_but_not_embedding():
    class FakeClient:
        def __init__(self):
            self.body = None

        def search(self, *, index, body):
            self.body = body
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "chunk-1",
                            "_source": {
                                "text": "4SA 수율 하락. 원인",
                                "week": "2026-27",
                                "team": "Spica수율",
                                "mail_id": "mail-1",
                                "subject": "Spica 주간 수율",
                                "part_index": 0,
                            },
                        },
                        {
                            "_id": "chunk-2",
                            "_source": {
                                "text": "원인 분석 중입니다.",
                                "week": "2026-27",
                                "team": "Spica수율",
                                "mail_id": "mail-1",
                                "subject": "Spica 주간 수율",
                                "part_index": 1,
                            },
                        },
                    ]
                }
            }

    client = FakeClient()
    mails = fetch_source_mails(client, ["2026-27"])

    assert len(mails) == 1
    assert mails[0].id == "2026-27:Spica수율:mail-1"
    assert mails[0].body == "4SA 수율 하락. 원인 분석 중입니다."
    assert "embedding" not in client.body["_source"]


def test_every_taxonomy_node_gets_one_latest_page():
    nodes = category_nodes(taxonomy())
    pages = build_page_documents(taxonomy(), [], as_of_week="2026-28")

    assert len(nodes) == 23
    assert len(pages) == 23
    assert {page["category_id"] for page in pages} == {node.id for node in nodes}


def test_pending_issue_is_carried_and_resolved_event_updates_same_page():
    pending = agenda(
        "agenda-w27",
        "2026-27",
        "pending",
        "4SA 조건 변경 결과 확인 대기",
    )
    pages_w27 = build_page_documents(taxonomy(), [pending], as_of_week="2026-27")
    page_w27 = next(page for page in pages_w27 if page["category_id"] == "lotcd:4sa")

    assert page_w27["open_issue_ids"] == ["issue:4sa-yield"]
    assert "진행 중 이슈: 1건" in page_w27["body_markdown"]

    resolved = agenda(
        "agenda-w28",
        "2026-28",
        "resolved",
        "4SA 조건 변경 후 정상 범위 회복",
    )
    pages_w28 = build_page_documents(
        taxonomy(), [pending, resolved], as_of_week="2026-28"
    )
    page_w28 = next(page for page in pages_w28 if page["category_id"] == "lotcd:4sa")

    assert page_w28["open_issue_ids"] == []
    assert page_w28["resolved_issue_ids"] == ["issue:4sa-yield"]
    assert "이번 주 해결 이슈: 1건" in page_w28["body_markdown"]
    assert "2026-27: 4SA 조건 변경 결과 확인 대기" in page_w28["body_markdown"]
    assert "해결 주차: 2026-28" in page_w28["body_markdown"]


def test_classification_pending_is_shown_as_review_not_open_issue():
    review = agenda(
        "agenda-review",
        "2026-28",
        "pending",
        "4SA 후보 분류 검토",
        issue_id="issue:review",
        review_status="pending",
    )
    pages = build_page_documents(taxonomy(), [review], as_of_week="2026-28")
    page = next(page for page in pages if page["category_id"] == "lotcd:4sa")

    assert page["open_issue_ids"] == []
    assert page["review_agenda_ids"] == ["agenda-review"]
    assert "## 분류 검토 필요" in page["body_markdown"]
