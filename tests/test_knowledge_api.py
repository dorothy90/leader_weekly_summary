from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastapi import FastAPI

import knowledge_api
from knowledge_api import get_store, router
from knowledge_models import CategoryPath
from knowledge_store import SQLiteKnowledgeStore


def request(method: str, path: str, **kwargs) -> httpx.Response:
    async def run() -> httpx.Response:
        app = FastAPI()
        app.include_router(router)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.request(method, path, **kwargs)

    return asyncio.run(run())


def test_fixture_documents_are_valid_and_complete():
    store = get_store()

    assert len(store.taxonomy.domains) == 2
    assert len(store.mails) == 12
    assert len(store.agendas) == 29


def test_category_wiki_route_returns_generated_opensearch_page(monkeypatch):
    captured = []

    def fake_get_page(category_id):
        captured.append(category_id)
        return {
            "category_id": category_id,
            "page_kind": "latest",
            "level": "lotcd",
            "domain": "DRAM",
            "tech": "Spica",
            "lotcd": "4SA",
            "title": "4SA",
            "product": "LPDDR5 24G",
            "fab_id": "4",
            "as_of_week": "2026-28",
            "body_markdown": "# 4SA",
            "agenda_count": 1,
            "open_issue_ids": ["issue:4sa-yield"],
            "resolved_issue_ids": [],
            "review_agenda_ids": [],
            "source_agenda_ids": ["agenda-w27"],
            "source_doc_ids": ["chunk-1"],
            "source_hash": "hash",
            "taxonomy_version": 1,
            "generated_at": "2026-07-12T00:00:00Z",
        }

    monkeypatch.setattr(knowledge_api, "_get_category_wiki_page", fake_get_page)
    response = request("GET", "/api/knowledge/wiki/pages/DRAM/Spica/4SA")

    assert response.status_code == 200
    assert response.json()["body_markdown"] == "# 4SA"
    assert captured == ["lotcd:4sa"]


def test_agenda_detail_falls_back_to_opensearch_for_generated_wiki_source(monkeypatch):
    expected = {
        "agenda": {
            "id": "os-agenda-1",
            "mail_id": "2026-28:Spica:mail-1",
            "source_quote": "4SA 수율 하락",
            "summary": "4SA 수율 하락",
            "scope": "lotcd",
            "target_paths": [{"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}],
            "candidate_paths": [],
            "topic": "yield",
            "state": "open",
            "confidence": 0.99,
            "review_required": False,
            "subject": "Spica 주간 수율",
            "sender_team": "Spica수율",
            "received_at": "2026-07-06T00:00:00Z",
            "review_status": "confirmed",
        },
        "mail": {
            "id": "2026-28:Spica:mail-1",
            "subject": "Spica 주간 수율",
            "sender_team": "Spica수율",
            "sender": "unknown",
            "received_at": "2026-07-06T00:00:00Z",
            "body": "4SA 수율 하락",
            "reply_to": None,
        },
    }
    monkeypatch.setattr(knowledge_api, "_get_opensearch_agenda", lambda _agenda_id: expected)

    detail = request("GET", "/api/knowledge/agendas/os-agenda-1")
    revisions = request("GET", "/api/knowledge/agendas/os-agenda-1/revisions")

    assert detail.status_code == 200
    assert detail.json()["mail"]["body"] == "4SA 수율 하락"
    assert revisions.status_code == 200
    assert revisions.json() == {"items": []}


def test_descendant_query_returns_unique_agendas():
    response = request(
        "GET",
        "/api/knowledge/agendas",
        params={"domain": "DRAM", "tech": "Spica", "scope_mode": "descendants"},
    )

    assert response.status_code == 200
    data = response.json()
    ids = [item["id"] for item in data["items"]]
    assert len(ids) == len(set(ids))
    assert "agenda_006" in ids
    assert "agenda_007" in ids


def test_category_counts_separate_direct_and_descendants():
    response = request("GET", "/api/knowledge/counts")

    assert response.status_code == 200
    spica = next(
        item
        for item in response.json()["items"]
        if item["path"] == {"domain": "DRAM", "tech": "Spica", "lotcd": None}
    )
    assert spica["direct"] == 2
    assert spica["descendants"] > spica["direct"]


def test_facets_cover_filter_values():
    response = request("GET", "/api/knowledge/facets")

    assert response.status_code == 200
    data = response.json()
    assert "yield" in data["topics"]
    assert "Spica수율" in data["sender_teams"]
    assert data["date_min"] == "2026-07-06"
    assert data["date_max"] == "2026-07-11"


def test_agenda_filters_sender_date_and_review_status():
    response = request(
        "GET",
        "/api/knowledge/agendas",
        params={
            "sender_team": "Spica수율",
            "date_from": "2026-07-11",
            "review_status": "confirmed",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert {item["id"] for item in data["items"]} == {"agenda_028", "agenda_029"}


def test_agenda_filter_rejects_inverted_date_range():
    response = request(
        "GET",
        "/api/knowledge/agendas",
        params={"date_from": "2026-07-11", "date_to": "2026-07-01"},
    )

    assert response.status_code == 422


def test_opensearch_backend_uses_ranked_ids_and_keeps_pending_sqlite_search(
    monkeypatch,
):
    monkeypatch.setenv("KNOWLEDGE_SEARCH_BACKEND", "opensearch")
    monkeypatch.setattr(
        knowledge_api,
        "_search_opensearch_agenda_ids",
        lambda query: ["agenda_004"] if query == "equipment condition" else [],
    )

    ranked = request(
        "GET", "/api/knowledge/agendas", params={"q": "equipment condition"}
    )
    pending = request(
        "GET", "/api/knowledge/agendas", params={"q": "4ZZ"}
    )

    assert [item["id"] for item in ranked.json()["items"]] == ["agenda_004"]
    assert [item["id"] for item in pending.json()["items"]] == ["agenda_027"]


def test_direct_tech_query_excludes_lotcd_agendas():
    response = request(
        "GET",
        "/api/knowledge/agendas",
        params={"domain": "DRAM", "tech": "Spica", "scope_mode": "direct"},
    )

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert ids == {"agenda_002", "agenda_003"}


def test_lotcd_query_returns_direct_and_multi_target_agendas():
    response = request(
        "GET",
        "/api/knowledge/agendas",
        params={
            "domain": "DRAM",
            "tech": "Spica",
            "lotcd": "4SA",
            "scope_mode": "descendants",
        },
    )

    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["items"]}
    assert {"agenda_004", "agenda_006", "agenda_007", "agenda_028"} <= ids


def test_review_queue_contains_unknown_lotcd():
    response = request("GET", "/api/knowledge/review-queue")

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == "agenda_027"


def test_invalid_hierarchy_returns_not_found():
    response = request(
        "GET",
        "/api/knowledge/agendas",
        params={"domain": "DRAM", "tech": "Spica", "lotcd": "4H1"},
    )

    assert response.status_code == 404


def test_agenda_detail_contains_mail_source():
    response = request("GET", "/api/knowledge/agendas/agenda_028")

    assert response.status_code == 200
    data = response.json()
    assert data["agenda"]["source_quote"] in data["mail"]["body"]
    assert data["mail"]["id"] == "dummy_mail_012"


def test_classification_update_persists_and_records_revision(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)

    response = request(
        "PATCH",
        "/api/knowledge/agendas/agenda_027/classification",
        json={
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
            "review_status": "confirmed",
        },
    )

    assert response.status_code == 200
    assert response.json()["agenda"]["scope"] == "lotcd"
    assert response.json()["agenda"]["review_status"] == "confirmed"
    assert store.agenda_view(
        next(item for item in store.agendas if item.id == "agenda_027")
    ).target_paths == [CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")]

    revisions = request(
        "GET", "/api/knowledge/agendas/agenda_027/revisions"
    ).json()["items"]
    assert len(revisions) == 1
    assert revisions[0]["changed_by"] == "demo-user"
    assert revisions[0]["before"]["target_paths"] == []
    assert revisions[0]["after"]["target_paths"][0]["lotcd"] == "4SA"
    assert store.pending_search_sync() == ["dummy_mail_011"]


def test_confirmed_classification_can_be_replaced_with_multiple_paths(
    tmp_path, monkeypatch
):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)
    paths = [
        {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"},
        {"domain": "DRAM", "tech": "Spica", "lotcd": "6SA"},
    ]

    response = request(
        "PATCH",
        "/api/knowledge/agendas/agenda_001/classification",
        json={"target_paths": paths, "review_status": "confirmed"},
    )

    assert response.status_code == 200
    assert response.json()["agenda"]["target_paths"] == paths
    assert response.json()["agenda"]["scope"] == "multi_lotcd"
    revisions = request(
        "GET", "/api/knowledge/agendas/agenda_001/revisions"
    ).json()["items"]
    assert revisions[0]["before"]["target_paths"] != paths
    assert revisions[0]["after"]["target_paths"] == paths


def test_classification_update_rejects_mismatched_hierarchy(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)

    response = request(
        "PATCH",
        "/api/knowledge/agendas/agenda_027/classification",
        json={
            "target_paths": [
                {"domain": "NAND", "tech": "Petra", "lotcd": "4SA"}
            ],
            "review_status": "confirmed",
        },
    )

    assert response.status_code == 422


def test_classification_can_be_held_without_canonical_target(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)

    held = request(
        "PATCH",
        "/api/knowledge/agendas/agenda_027/classification",
        json={"target_paths": [], "review_status": "on_hold"},
    )
    queue = request("GET", "/api/knowledge/review-queue")
    held_filter = request(
        "GET",
        "/api/knowledge/agendas",
        params={"review_status": "on_hold"},
    )
    invalid_confirm = request(
        "PATCH",
        "/api/knowledge/agendas/agenda_027/classification",
        json={"target_paths": [], "review_status": "confirmed"},
    )

    assert held.status_code == 200
    assert held.json()["agenda"]["scope"] == "unknown"
    assert held.json()["agenda"]["review_status"] == "on_hold"
    assert queue.json()["total"] == 0
    assert held_filter.json()["items"][0]["id"] == "agenda_027"
    assert invalid_confirm.status_code == 422


def test_alias_create_update_and_revision(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)
    paths = [
        {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"},
        {"domain": "DRAM", "tech": "Spica", "lotcd": "6SA"},
    ]

    created = request(
        "POST",
        "/api/knowledge/aliases",
        json={"value": "SP 24G Demo", "target_paths": paths},
    )
    assert created.status_code == 201
    alias_id = created.json()["id"]
    assert len(created.json()["target_paths"]) == 2

    updated = request(
        "PATCH",
        f"/api/knowledge/aliases/{alias_id}",
        json={
            "value": "SP 24G Demo Updated",
            "target_paths": [paths[0]],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["value"] == "SP 24G Demo Updated"
    assert len(updated.json()["target_paths"]) == 1

    revisions = request(
        "GET", f"/api/knowledge/aliases/{alias_id}/revisions"
    ).json()["items"]
    assert len(revisions) == 2
    assert revisions[0]["before"]["value"] == "SP 24G Demo"


def test_alias_conflict_returns_409(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)

    response = request(
        "POST",
        "/api/knowledge/aliases",
        json={
            "value": "sp lpddr5 24g",
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
        },
    )

    assert response.status_code == 409


def test_alias_soft_delete_keeps_revision_and_allows_recreate(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)
    payload = {
        "value": "Temporary Alias",
        "target_paths": [
            {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
        ],
    }
    created = request("POST", "/api/knowledge/aliases", json=payload)
    alias_id = created.json()["id"]

    deleted = request("DELETE", f"/api/knowledge/aliases/{alias_id}")
    aliases_after_delete = request("GET", "/api/knowledge/aliases").json()["items"]
    recreated = request("POST", "/api/knowledge/aliases", json=payload)

    assert deleted.status_code == 200
    assert all(item["id"] != alias_id for item in aliases_after_delete)
    assert recreated.status_code == 201
    revisions = store.mapping_revisions(alias_id)
    assert revisions[0].after["deleted"] is True


def test_header_auth_requires_identity_and_editor_role(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)
    monkeypatch.setenv("KNOWLEDGE_AUTH_MODE", "header")

    unauthenticated = request("GET", "/api/knowledge/taxonomy")
    reader = request(
        "GET",
        "/api/knowledge/taxonomy",
        headers={"X-User-Id": "reader-1", "X-User-Roles": "knowledge-reader"},
    )
    reader_session = request(
        "GET",
        "/api/knowledge/session",
        headers={"X-User-Id": "reader-1", "X-User-Roles": "knowledge-reader"},
    )
    forbidden = request(
        "POST",
        "/api/knowledge/aliases",
        headers={"X-User-Id": "reader-1", "X-User-Roles": "knowledge-reader"},
        json={
            "value": "Authenticated Alias",
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
        },
    )
    created = request(
        "POST",
        "/api/knowledge/aliases",
        headers={"X-User-Id": "editor-1", "X-User-Roles": "knowledge-editor"},
        json={
            "value": "Authenticated Alias",
            "target_paths": [
                {"domain": "DRAM", "tech": "Spica", "lotcd": "4SA"}
            ],
        },
    )

    assert unauthenticated.status_code == 401
    assert reader.status_code == 200
    assert reader_session.json() == {
        "user_id": "reader-1",
        "roles": ["knowledge-reader"],
        "can_edit": False,
    }
    assert forbidden.status_code == 403
    assert created.status_code == 201
    revisions = store.mapping_revisions(created.json()["id"])
    assert revisions[0].changed_by == "editor-1"


def test_external_taxonomy_initializes_without_dummy_mail(tmp_path, monkeypatch):
    source = (
        Path(__file__).resolve().parents[1]
        / "fixtures"
        / "knowledge"
        / "taxonomy.json"
    )
    taxonomy = json.loads(source.read_text(encoding="utf-8"))
    taxonomy["is_dummy"] = False
    taxonomy["notice"] = "test production taxonomy"
    taxonomy_path = tmp_path / "taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(taxonomy, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("KNOWLEDGE_TAXONOMY_PATH", str(taxonomy_path))

    store = SQLiteKnowledgeStore(tmp_path / "production.db")

    assert store.taxonomy.is_dummy is False
    assert store.taxonomy.notice == "test production taxonomy"
    assert store.mails == {}
    assert store.agendas == []


def test_tech_and_lotcd_crud_with_linked_delete_guard(tmp_path, monkeypatch):
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)

    tech_created = request(
        "POST",
        "/api/knowledge/techs",
        json={
            "domain": "DRAM",
            "id": "orion",
            "name": "Orion",
            "aliases": ["OR"],
        },
    )
    lotcd_created = request(
        "POST",
        "/api/knowledge/lotcds",
        json={
            "domain": "DRAM",
            "tech": "Orion",
            "code": "4OR",
            "fab_id": "4",
            "product_code": "OR",
            "product": "DDR5 32G",
            "aliases": ["OR DDR5 32G"],
        },
    )
    lotcd_updated = request(
        "PATCH",
        "/api/knowledge/lotcds/4OR",
        json={
            "fab_id": "4",
            "product_code": "OR",
            "product": "DDR5 32G Rev.B",
            "aliases": ["OR 32G Rev.B"],
        },
    )
    linked_delete = request("DELETE", "/api/knowledge/lotcds/4SA")
    child_tech_delete = request("DELETE", "/api/knowledge/techs/orion")
    tech_updated = request(
        "PATCH",
        "/api/knowledge/techs/orion",
        json={"name": "Orion Next", "aliases": ["OR Next"]},
    )
    unlinked_delete = request("DELETE", "/api/knowledge/lotcds/4OR")
    tech_deleted = request("DELETE", "/api/knowledge/techs/orion")

    assert tech_created.status_code == 201
    assert lotcd_created.status_code == 201
    assert lotcd_updated.status_code == 200
    updated_orion = next(
        tech
        for domain in lotcd_updated.json()["domains"]
        for tech in domain["techs"]
        if tech["name"] == "Orion"
    )
    assert updated_orion["lotcds"][0]["product"] == "DDR5 32G Rev.B"
    assert updated_orion["lotcds"][0]["aliases"] == ["OR 32G Rev.B"]
    assert linked_delete.status_code == 409
    assert child_tech_delete.status_code == 409
    assert tech_updated.status_code == 200
    assert any(
        tech["name"] == "Orion Next" and tech["aliases"] == ["OR Next"]
        for domain in tech_updated.json()["domains"]
        for tech in domain["techs"]
    )
    assert unlinked_delete.status_code == 200
    assert tech_deleted.status_code == 200
    assert all(
        tech["id"] != "orion"
        for domain in tech_deleted.json()["domains"]
        for tech in domain["techs"]
    )
