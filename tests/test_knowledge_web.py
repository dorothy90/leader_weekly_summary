from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from knowledge_web import mount_knowledge_web


def get(app: FastAPI, path: str) -> httpx.Response:
    async def run() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get(path)

    return asyncio.run(run())


def test_mount_knowledge_web_serves_spa_routes_and_assets(tmp_path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<main>Knowledge Web</main>", encoding="utf-8")
    (assets / "app.js").write_text("export {}", encoding="utf-8")
    app = FastAPI()

    mounted = mount_knowledge_web(app, dist)

    assert mounted is True
    assert "Knowledge Web" in get(app, "/classification").text
    assert get(app, "/assets/app.js").status_code == 200


def test_topic_wiki_deep_links_serve_spa(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<main>Knowledge Web</main>", encoding="utf-8")
    app = FastAPI()

    assert mount_knowledge_web(app, dist)
    for path in (
        "/wiki/topics/T-001",
        "/wiki/lotcd/DRAM/Spica/4SA",
        "/wiki/teams/Yield",
        "/wiki/weeks/2026-W30",
    ):
        assert get(app, path).status_code == 200


def test_mount_knowledge_web_skips_missing_build(tmp_path):
    app = FastAPI()

    mounted = mount_knowledge_web(app, tmp_path / "missing")

    assert mounted is False
    assert get(app, "/classification").status_code == 404
