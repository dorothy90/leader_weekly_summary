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
    TopicRelation,
    TopicSection,
    TopicRevision,
    WikiTopic,
    WikiReview,
)
from wiki_projections import build_week_view
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
    app = FastAPI()
    app.state.wiki_store = wiki_store
    monkeypatch.setattr(knowledge_api, "get_store", lambda: ClassificationStore())
    monkeypatch.setattr(knowledge_api, "get_wiki_store", lambda: wiki_store, raising=False)
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


def test_week_route_preserves_snapshot_action_rows(api_app):
    store = api_app.state.wiki_store
    current = store.topic("T-001")
    action_revision = store.topic_revision("T-001", "REV-001").model_copy(
        update={
            "sections": [
                TopicSection(
                    key="actions_and_decisions",
                    title="Actions and decisions",
                    body="Adjust condition.",
                )
            ]
        }
    )
    store.publish_topic(current, action_revision)
    snapshot = build_week_view(store, "2026-W30", "RUN-001")
    store.save_week(snapshot)

    response = request(api_app, "/api/knowledge/wiki/weeks/2026-W30")

    assert response.status_code == 200
    assert response.json()["actions_and_decisions"] == [
        snapshot.actions_and_decisions[0].model_dump(mode="json")
    ]


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


def test_relation_review_api_accepts_and_rejects(api_app):
    store = api_app.state.wiki_store
    for suffix, action, expected_state in (
        ("accept", "accept", "accepted"),
        ("reject", "reject", "rejected"),
    ):
        relation_id = f"REL-{suffix}"
        review_id = f"R-{relation_id}"
        store.save_relation(
            TopicRelation(
                relation_id=relation_id,
                source_topic_id="T-001",
                target_topic_id="T-002",
                kind="supports",
                agenda_ids=["A-001"],
                confidence=0.8,
                review_state="pending",
            )
        )
        store.save_review(
            WikiReview(
                review_id=review_id,
                kind="relation",
                relation_id=relation_id,
                relation_kind="supports",
                relation_agenda_ids=["A-001"],
            )
        )

        response = post(
            api_app,
            f"/api/knowledge/wiki/reviews/{review_id}/resolve",
            json={"action": action},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "resolved"
        assert store.relation(relation_id).review_state == expected_state


def test_relation_review_api_maps_invalid_action_and_missing_relation(api_app):
    store = api_app.state.wiki_store
    store.save_review(
        WikiReview(
            review_id="R-REL-missing",
            kind="relation",
            relation_id="REL-missing",
            relation_kind="supports",
            relation_agenda_ids=["A-001"],
        )
    )

    invalid = post(
        api_app,
        "/api/knowledge/wiki/reviews/R-REL-missing/resolve",
        json={"action": "hold"},
    )
    missing = post(
        api_app,
        "/api/knowledge/wiki/reviews/R-REL-missing/resolve",
        json={"action": "accept"},
    )

    assert invalid.status_code == 409
    assert missing.status_code == 404
