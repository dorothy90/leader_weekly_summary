"""Standalone Knowledge Explorer API for local frontend development."""

from fastapi import FastAPI

from knowledge_api import router
from knowledge_web import mount_knowledge_web


app = FastAPI(title="Mail Knowledge Preview API", version="0.1.0")
app.include_router(router)
mount_knowledge_web(app)
