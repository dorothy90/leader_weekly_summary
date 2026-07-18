from __future__ import annotations

from datetime import UTC, datetime

import agenda_opensearch
from agenda_extract import ExtractedAgenda, MailExtractionResult
from agenda_opensearch import (
    build_documents,
    ensure_index,
    index_definition,
    search_agenda_ids,
)
from knowledge_models import CategoryPath, ClassificationDecision, Mail
from knowledge_store import SQLiteKnowledgeStore


def test_index_definition_uses_explicit_search_and_filter_mappings():
    definition = index_definition()
    mappings = definition["mappings"]

    assert mappings["dynamic"] == "strict"
    assert mappings["properties"]["text"]["type"] == "text"
    assert mappings["properties"]["lotcds"]["type"] == "keyword"
    assert mappings["properties"]["decision_status"]["type"] == "keyword"
    assert mappings["properties"]["target_paths"]["type"] == "nested"


def test_ensure_index_adds_decision_status_mapping_to_existing_index():
    class FakeIndices:
        def __init__(self):
            self.mapping = None

        def exists(self, *, index):
            return True

        def put_mapping(self, *, index, body):
            self.mapping = (index, body)

    class FakeClient:
        indices = FakeIndices()

    client = FakeClient()

    ensure_index(client, "agendas")

    assert client.indices.mapping == (
        "agendas",
        {"properties": {"decision_status": {"type": "keyword"}}},
    )


def test_build_documents_only_returns_confirmed_agendas(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")

    confirmed = build_documents(store, "dummy_mail_002")
    pending = build_documents(store, "dummy_mail_011")

    assert len(confirmed) == 3
    assert pending == []
    assert confirmed[0]["review_status"] == "confirmed"
    assert confirmed[0]["mail_id"] == "dummy_mail_002"


def test_build_documents_only_returns_reviewed_single_lotcd_traces(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    run = store.start_classification_run(
        week="2026-01",
        prompt_version="agenda-v2",
        classifier_version="lotcd-v1",
    )
    mail = Mail(
        id="mixed-mail",
        subject="mixed classifications",
        sender_team="DRAM",
        sender="sender@example.com",
        received_at=datetime(2026, 1, 5, tzinfo=UTC),
        body="mixed",
    )
    lotcd_4sa = CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")
    lotcd_6sa = CategoryPath(domain="DRAM", tech="Spica", lotcd="6SA")

    def agenda(
        agenda_id: str,
        status: str,
        target_paths: list[CategoryPath],
    ) -> ExtractedAgenda:
        decision_target = target_paths[0] if target_paths else None
        return ExtractedAgenda(
            id=agenda_id,
            mail_id=mail.id,
            source_quote=agenda_id,
            source_start=0,
            source_end=len(agenda_id),
            classification_context=agenda_id,
            summary=agenda_id,
            scope="lotcd" if target_paths else "unknown",
            target_paths=target_paths,
            candidate_paths=[],
            topic="yield",
            state="investigating",
            item_kind="aggregate" if status == "aggregate" else "lotcd_specific",
            decision=ClassificationDecision(
                status=status,
                target_path=decision_target,
                matches=[],
                diagnostics=[],
                confidence=1.0 if decision_target else 0.0,
            ),
            confidence=1.0 if decision_target else 0.0,
            # The persisted trace is authoritative even when a legacy review flag
            # was previously confirmed.
            review_required=False,
        )

    result = MailExtractionResult(
        mail_id=mail.id,
        agendas=[
            agenda("confirmed", "confirmed", [lotcd_4sa]),
            agenda("corrected", "manually_corrected", [lotcd_6sa]),
            agenda("aggregate", "aggregate", []),
            agenda("unresolved", "unclassified", []),
            agenda("excluded", "excluded", []),
            agenda("multi-target", "confirmed", [lotcd_4sa, lotcd_6sa]),
        ],
    )
    store.save_classified_extraction(run.id, mail, result)
    store.finish_classification_run(run.id)

    documents = build_documents(store, mail.id)

    assert {document["agenda_id"] for document in documents} == {
        "confirmed",
        "corrected",
    }
    assert {document["decision_status"] for document in documents} == {
        "confirmed",
        "manually_corrected",
    }
    assert all(len(document["target_paths"]) == 1 for document in documents)


def test_sync_pending_clears_success_and_retains_failure(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    store.update_classification(
        agenda_id="agenda_027",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        review_status="confirmed",
        changed_by="tester",
    )
    monkeypatch.setattr(agenda_opensearch, "sync_mail", lambda *args: 1)

    success = agenda_opensearch.sync_pending(None, store)

    assert success == {
        "synced_mails": 1,
        "indexed_agendas": 1,
        "failed_mails": [],
    }
    assert store.pending_search_sync() == []

    store.update_classification(
        agenda_id="agenda_027",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        review_status="confirmed",
        changed_by="tester",
    )

    def fail(*_args):
        raise RuntimeError("OpenSearch unavailable")

    monkeypatch.setattr(agenda_opensearch, "sync_mail", fail)
    failed = agenda_opensearch.sync_pending(None, store)

    assert failed["failed_mails"] == ["dummy_mail_011"]
    assert store.pending_search_sync() == ["dummy_mail_011"]


def test_search_agenda_ids_uses_weighted_text_fields():
    class FakeClient:
        def __init__(self):
            self.body = None

        def search(self, *, index, body):
            self.body = body
            return {
                "hits": {
                    "hits": [
                        {"_source": {"agenda_id": "agenda_004"}},
                        {"_source": {"agenda_id": "agenda_028"}},
                    ]
                }
            }

    client = FakeClient()

    ids = search_agenda_ids(client, "수율 하락")

    assert ids == ["agenda_004", "agenda_028"]
    assert client.body["query"]["multi_match"]["fields"][0] == "summary^3"
    assert client.body["query"]["multi_match"]["operator"] == "and"

    search_agenda_ids(client, "4SA")
    assert client.body["query"] == {"term": {"lotcds": "4SA"}}
