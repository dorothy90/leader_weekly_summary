import ast
from pathlib import Path


LEGACY_OPENSEARCH_CLIENTS = (
    "opensearch.py",
    "wiki_export.py",
    "embed_vectordb.py",
    "wiki_builder.py",
    "wiki_builder_1stver.py",
    "rag_api_opensearch_v3.py",
)

REQUIRED_DIRECT_CONSTRAINTS = {
    "fastapi>=0.109,<1",
    "pydantic>=2.10,<3",
    "pydantic-settings==2.11.0",
    "langgraph>=0.2,<2",
    "opensearch-py>=2.4,<3",
    "openai>=1.58,<3",
    "motor>=3.3,<4",
    "httpx>=0.27,<1",
    "pytest>=8,<9",
    "pytest-asyncio>=0.24,<2",
}


def test_new_application_never_imports_legacy_rag_module():
    offenders = []
    for path in Path("app").rglob("*.py"):
        if "rag_api_opensearch_v3" in path.read_text(encoding="utf-8"):
            offenders.append(str(path))
    assert offenders == []


def test_new_application_does_not_depend_on_full_langchain_framework():
    import_offenders = []
    for root in (Path("app"), Path("tests")):
        paths = root.rglob("*.py")
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                imports_full_package = isinstance(node, ast.Import) and any(
                    alias.name == "langchain" for alias in node.names
                )
                imports_from_full_package = (
                    isinstance(node, ast.ImportFrom) and node.module == "langchain"
                )
                if imports_full_package or imports_from_full_package:
                    import_offenders.append(str(path))
    requirements = Path("requirements.txt").read_text(encoding="utf-8").splitlines()
    dependency_offenders = [
        line
        for line in requirements
        if line.startswith("langchain") and line[9:10] in "<>=!~"
    ]
    assert import_offenders == []
    assert dependency_offenders == []


def test_legacy_opensearch_clients_have_no_password_or_tls_bypass_defaults():
    offenders = []
    for name in LEGACY_OPENSEARCH_CLIENTS:
        source = Path(name).read_text(encoding="utf-8")
        if 'os.getenv("OPENSEARCH_PASSWORD", "")' not in source:
            offenders.append(f"{name}: password default")
        if 'os.getenv("OPENSEARCH_VERIFY_CERTS", "true")' not in source:
            offenders.append(f"{name}: certificate setting")
        verify_bypass = "verify_certs=" + "False"
        warning_bypass = "ssl_show_warn=" + "False"
        if verify_bypass in source or warning_bypass in source:
            offenders.append(f"{name}: certificate bypass")
        if 'RuntimeError("OPENSEARCH_PASSWORD is required")' not in source:
            offenders.append(f"{name}: missing-password guard")
    assert offenders == []


def test_new_application_dependencies_are_bounded():
    constraints = {
        line.strip()
        for line in Path("requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert REQUIRED_DIRECT_CONSTRAINTS <= constraints


def test_application_opensearch_builder_fails_closed_on_credentials_and_tls():
    source = Path("app/api/dependencies.py").read_text(encoding="utf-8")
    assert 'RuntimeError("OPENSEARCH_PASSWORD is required")' in source
    assert "ssl_show_warn=current.opensearch_verify_certs" in source
