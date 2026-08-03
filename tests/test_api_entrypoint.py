import subprocess
import sys
import tomllib
from pathlib import Path


def test_runtime_dependencies_include_api_settings():
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]

    assert "pydantic-settings==2.7.1" in dependencies
    assert "motor==3.7.1" in dependencies


def test_uvicorn_entrypoint_exposes_fastapi_app():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from fastapi import FastAPI; "
                "from app.api.main import app; "
                "assert isinstance(app, FastAPI); "
                "assert '/health' in {route.path for route in app.routes}"
            ),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
