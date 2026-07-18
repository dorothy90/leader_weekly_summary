from __future__ import annotations

from category_wiki_builder import (
    agenda_index_definition,
    build_page_documents,
    category_nodes,
    fetch_source_mails,
    merge_overlapping_chunks,
    normalize_agenda_document,
    page_index_definition,
)
from knowledge_models import TaxonomyDocument


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
