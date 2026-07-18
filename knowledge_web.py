"""Serve the built Classification Workbench from FastAPI."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles


DEFAULT_WEB_DIST = Path(__file__).resolve().parent / "web" / "dist"


def mount_knowledge_web(app: FastAPI, dist_path: Path | None = None) -> bool:
    configured_path = dist_path or Path(
        os.getenv("KNOWLEDGE_WEB_DIST", str(DEFAULT_WEB_DIST))
    )
    index_path = configured_path / "index.html"
    assets_path = configured_path / "assets"
    if not index_path.exists() or not assets_path.is_dir():
        return False

    app.mount(
        "/assets",
        StaticFiles(directory=str(assets_path)),
        name="knowledge-assets",
    )

    def serve_index() -> FileResponse:
        return FileResponse(index_path)

    app.add_api_route(
        "/",
        serve_index,
        methods=["GET"],
        include_in_schema=False,
        name="knowledge-root",
    )
    app.add_api_route(
        "/classification",
        serve_index,
        methods=["GET"],
        include_in_schema=False,
        name="knowledge-classification",
    )
    return True
