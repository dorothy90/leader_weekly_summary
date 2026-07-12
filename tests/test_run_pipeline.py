from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import integrated_wiki_builder

module_names = (
    "fetch_mail",
    "process_attachment",
    "process_vision",
    "embed_vectordb",
    "wiki_export",
    "generate_outlook_report",
    "send_report",
)
missing = object()
original_modules = {name: sys.modules.get(name, missing) for name in module_names}
for module_name in module_names:
    sys.modules[module_name] = SimpleNamespace()

try:
    import run_pipeline
finally:
    for module_name, original in original_modules.items():
        if original is missing:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = original


@pytest.mark.parametrize(
    "stats",
    [
        {"pages": 22, "failed": 1, "expected_pages": 23},
        {"pages": 22, "failed": 0, "expected_pages": 23},
    ],
)
def test_pipeline_rejects_failed_or_incomplete_integrated_wiki(
    stats, monkeypatch, tmp_path
):
    monkeypatch.setenv("ENABLE_CATEGORY_WIKI", "true")
    monkeypatch.setenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", "true")
    monkeypatch.setattr(run_pipeline, "wait_for_opensearch", lambda: None)
    monkeypatch.setattr(
        run_pipeline.fetch_mail,
        "get_week_string",
        lambda today: "2026-W28",
        raising=False,
    )
    monkeypatch.setattr(run_pipeline.fetch_mail, "main", lambda: None, raising=False)
    monkeypatch.setattr(
        run_pipeline.process_attachment,
        "process_all",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        run_pipeline.process_vision,
        "process_all",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        run_pipeline.embed_vectordb,
        "process_all",
        lambda **kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        run_pipeline.embed_vectordb,
        "get_opensearch_client",
        lambda: object(),
        raising=False,
    )
    monkeypatch.setattr(integrated_wiki_builder, "run", lambda **kwargs: stats)
    monkeypatch.setattr(run_pipeline, "OVERVIEW_DIR", tmp_path)

    with pytest.raises(RuntimeError, match="통합 Wiki 생성 실패"):
        run_pipeline.main()
