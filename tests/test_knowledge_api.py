import asyncio
import shutil
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

import knowledge_api
from classification_store import JsonClassificationStore
from knowledge_models import (
    CategoryPath,
    ClassificationDecision,
    ClassificationItem,
    TopicRevision,
    WikiTopic,
)
from wiki_store import JsonWikiStore

ROOT = Path(__file__).resolve().parents[1]

def request(app, path, *, headers=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path, headers=headers)
    return asyncio.run(run())

def post(app, path, *, headers=None, json=None):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(path, headers=headers, json=json)
    return asyncio.run(run())


@pytest.fixture
def api_app(tmp_path, monkeypatch):
    item = ClassificationItem(
        agenda_id="A-001",
        mail_id="M-001",
        summary="4SA yield declined.",
        source_quote="4SA yield declined.",
        classification_context="4SA yield declined.",
        item_kind="lotcd_specific",
        decision=ClassificationDecision(
            status="confirmed",
            target_path=CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA"),
            confidence=1,
        ),
        revision_count=0,
        team="Yield",
        subject="Weekly yield",
        received_at=datetime(2026, 7, 20, tzinfo=UTC),
    )

    class ClassificationStore:
        def classification_item(self, agenda_id):
            if agenda_id != item.agenda_id:
                raise KeyError(agenda_id)
            return "2026-W30", item

        def approved_week(self, week):
            raise ValueError(f"Week {week} is not approved")

    wiki_store = JsonWikiStore(tmp_path / "wiki_data")
    topic = WikiTopic(
        topic_id="T-001",
        title="4SA yield decline",
        topic_kind="issue",
        primary_area="yield_defect",
        state="investigating",
        importance="high",
        first_seen_week="2026-W30",
        last_updated_week="2026-W30",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        teams=["Yield"],
        source_agenda_ids=["A-001"],
        current_revision_id="REV-001",
    )
    revision = TopicRevision(
        revision_id="REV-001",
        topic_id="T-001",
        week="2026-W30",
        body_markdown="4SA yield declined. [agenda:A-001]",
        sections=[],
        claims=[],
        source_agenda_ids=["A-001"],
        created_at=datetime(2026, 7, 20, tzinfo=UTC),
        model="test-model",
    )
    wiki_store.publish_topic(topic, revision)
    monkeypatch.setattr(knowledge_api, "get_store", lambda: ClassificationStore())
    monkeypatch.setattr(knowledge_api, "get_wiki_store", lambda: wiki_store, raising=False)
    app = FastAPI()
    app.include_router(knowledge_api.router)
    return app

def test_classification_api_reads_json_week(tmp_path, monkeypatch):
    rules = tmp_path / "rules.json"
    shutil.copy(ROOT / "config/classification_rules.json", rules)
    store = JsonClassificationStore(tmp_path / "data", rules)
    run = store.start_classification_run("2026-28", "prompt", "classifier")
    store.finish_classification_run(run.id)
    monkeypatch.setattr(knowledge_api, "get_store", lambda: store)
    app = FastAPI(); app.include_router(knowledge_api.router)
    response = request(app, "/api/knowledge/classification/weeks")
    assert response.status_code == 200
    assert response.json()[0]["week"] == "2026-28"

def test_removed_legacy_api_is_not_exposed():
    app = FastAPI(); app.include_router(knowledge_api.router)
    assert request(app, "/api/knowledge/agendas").status_code == 404


def test_topic_and_lotcd_routes_read_same_topic(api_app):
    topic = request(api_app, "/api/knowledge/wiki/topics/T-001").json()
    lotcd = request(api_app, "/api/knowledge/wiki/lotcd/DRAM/Spica/4SA").json()
    assert topic["topic"]["topic_id"] in lotcd["topic_ids"]


def test_build_requires_editor_and_approved_week(api_app, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_AUTH_MODE", "header")
    viewer = post(
        api_app,
        "/api/knowledge/wiki/builds/2026-W30",
        headers={"X-User-Id": "viewer", "X-User-Roles": "knowledge-viewer"},
    )
    editor = post(
        api_app,
        "/api/knowledge/wiki/builds/2026-W30",
        headers={"X-User-Id": "editor", "X-User-Roles": "knowledge-editor"},
    )
    assert viewer.status_code == 403
    assert editor.status_code == 409
