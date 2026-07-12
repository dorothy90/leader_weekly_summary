from __future__ import annotations

import agenda_opensearch
from agenda_opensearch import build_documents, index_definition, search_agenda_ids
from knowledge_models import CategoryPath
from knowledge_store import SQLiteKnowledgeStore


def test_index_definition_uses_explicit_search_and_filter_mappings():
    definition = index_definition()
    mappings = definition["mappings"]

    assert mappings["dynamic"] == "strict"
    assert mappings["properties"]["text"]["type"] == "text"
    assert mappings["properties"]["lotcds"]["type"] == "keyword"
    assert mappings["properties"]["target_paths"]["type"] == "nested"


def test_build_documents_only_returns_confirmed_agendas(tmp_path):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")

    confirmed = build_documents(store, "dummy_mail_002")
    pending = build_documents(store, "dummy_mail_011")

    assert len(confirmed) == 3
    assert pending == []
    assert confirmed[0]["review_status"] == "confirmed"
    assert confirmed[0]["mail_id"] == "dummy_mail_002"


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
