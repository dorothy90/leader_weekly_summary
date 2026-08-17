import ast
import json
from pathlib import Path


NOTEBOOK = Path("MultiSource_Production_Environment_Test.ipynb")


def _notebook() -> dict:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _code_cells(notebook: dict) -> list[dict]:
    return [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]


def _code_source() -> str:
    return "\n".join("".join(cell["source"]) for cell in _code_cells(_notebook()))


def _notebook_function(name: str):
    module = ast.parse(_code_source())
    imports = [
        node
        for node in module.body
        if isinstance(node, ast.ImportFrom) and node.module == "urllib.parse"
    ]
    definitions = [
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(definitions) == 1

    namespace = {}
    selected = ast.Module(body=[*imports, *definitions], type_ignores=[])
    exec(compile(selected, str(NOTEBOOK), "exec"), namespace)
    return namespace[name]


def test_production_notebook_is_valid_output_free_python():
    notebook = _notebook()
    assert notebook["nbformat"] == 4
    assert notebook["nbformat_minor"] >= 5
    assert notebook["metadata"]["kernelspec"]["language"] == "python"
    assert "widgets" not in notebook["metadata"]

    for cell in notebook["cells"]:
        assert "widgets" not in cell["metadata"]

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
    source = _code_source()
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


def test_production_notebook_redacts_credentials_from_displayed_endpoint():
    safe_endpoint_description = _notebook_function("safe_endpoint_description")
    credential_bearing_url = (
        "https://svc-user:svc-password@example.test/v1"
        "?api_key=token-value#fragment"
    )

    displayed_endpoint = safe_endpoint_description(credential_bearing_url)

    assert displayed_endpoint == "https://example.test"
    for sensitive_value in (
        "svc-user",
        "svc-password",
        "/v1",
        "api_key",
        "token-value",
        "fragment",
    ):
        assert sensitive_value not in displayed_endpoint

    source = _code_source()
    assert (
        '"base_url": safe_endpoint_description(settings.openrouter_base_url)'
        in source
    )
    assert '"base_url": settings.openrouter_base_url' not in source
