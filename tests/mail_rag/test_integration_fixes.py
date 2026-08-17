import asyncio
import httpx
import subprocess
import sys

from app.api.dependencies import ServiceContainer
from app.api.main import create_app


def test_real_runtime_dependencies_import_and_container_constructs_without_network():
    code = """
import motor, pymongo
assert tuple(map(int, motor.version.split('.')[:2])) >= (3, 7)
assert tuple(map(int, pymongo.version.split('.')[:2])) == (4, 15)
from app.api.dependencies import build_container
from app.config.settings import Settings
from pydantic import SecretStr
container = build_container(Settings(
    mongo_uri='mongodb://127.0.0.1:1',
    openrouter_api_key=SecretStr('test-openrouter-key'),
))
assert container.agentic is not None and container.jobs is not None
"""
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_ready_checks_dependencies_but_health_is_liveness():
    class Ready:
        async def check(self):
            return {"mongo": "ready", "opensearch": "ready", "aliases": "ready"}

    container = ServiceContainer(
        agentic=None,
        conversations=None,
        jobs=None,
        readiness=Ready(),
    )

    async def exercise():
        transport = httpx.ASGITransport(app=create_app(container))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            health = await client.get("/health")
            ready = await client.get("/ready")
        return health, ready

    health, ready = asyncio.run(exercise())
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
