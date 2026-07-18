import asyncio
import shutil
from pathlib import Path

import httpx
from fastapi import FastAPI

import knowledge_api
from classification_store import JsonClassificationStore

ROOT = Path(__file__).resolve().parents[1]

def request(app, path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get(path)
    return asyncio.run(run())

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
