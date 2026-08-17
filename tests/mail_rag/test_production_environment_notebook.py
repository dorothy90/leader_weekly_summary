import ast
import json
from pathlib import Path


NOTEBOOK = Path("MultiSource_Production_Environment_Test.ipynb")


def _notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cells(notebook: dict) -> list[dict]:
    return [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]


def test_production_notebook_is_valid_output_free_python():
    notebook = _notebook()
    assert notebook["nbformat"] == 4
    assert notebook["nbformat_minor"] >= 5
    assert notebook["metadata"]["kernelspec"]["language"] == "python"

    cells = _code_cells(notebook)
    assert cells
    for index, cell in enumerate(cells):
        source = "".join(cell["source"])
        compile(
            source,
            f"{NOTEBOOK}#cell-{index}",
            "exec",
            flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT,
        )
        assert cell["execution_count"] is None
        assert cell["outputs"] == []


def test_production_notebook_uses_the_real_route_free_application_path():
    source = "\n".join(
        "".join(cell["source"]) for cell in _code_cells(_notebook())
    )
    for required in (
        "Settings.from_env()",
        "build_container(settings)",
        "create_app(container)",
        "httpx.ASGITransport",
        'client.get("/ready")',
        'client.post("/v1/chat"',
        '"conversation_id": conversation_id',
        '"OPENROUTER_API_KEY configured"',
    ):
        assert required in source

    assert '"response_mode":' not in source
    assert '"mode":' not in source
    assert '"routing":' not in source
    assert 'obsolete_fields = {"response_mode", "mode", "routing"}' in source
    assert "settings.multi_source_demo is False" in source
