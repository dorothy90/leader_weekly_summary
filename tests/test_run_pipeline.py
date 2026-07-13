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


def configure_pipeline(
    monkeypatch,
    tmp_path,
    stats,
    *,
    complete_downstream=False,
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
    if complete_downstream:
        markdown = tmp_path / "2026-W28_전체요약.md"
        monkeypatch.setattr(
            run_pipeline.wiki_export,
            "run_export",
            lambda **kwargs: markdown.write_text("# summary", encoding="utf-8"),
            raising=False,
        )
        monkeypatch.setattr(
            run_pipeline.generate_outlook_report,
            "convert",
            lambda source, target: target.write_text("html", encoding="utf-8"),
            raising=False,
        )
        monkeypatch.setattr(
            run_pipeline.send_report,
            "send_report",
            lambda *args: None,
            raising=False,
        )


def test_pipeline_accepts_three_page_incremental_update(monkeypatch, tmp_path):
    configure_pipeline(
        monkeypatch,
        tmp_path,
        {
            "affected_pages": 3,
            "saved_pages": 3,
            "skipped_pages": 0,
            "failed": 0,
            "pending": 0,
        },
        complete_downstream=True,
    )

    assert run_pipeline.main() == 0


@pytest.mark.parametrize(
    "stats",
    [
        {
            "affected_pages": 3,
            "saved_pages": 2,
            "skipped_pages": 0,
            "failed": 1,
            "pending": 0,
        },
        {
            "affected_pages": 3,
            "saved_pages": 1,
            "skipped_pages": 0,
            "failed": 1,
            "pending": 1,
        },
    ],
)
def test_pipeline_rejects_failed_or_pending_incremental_update(
    stats,
    monkeypatch,
    tmp_path,
):
    configure_pipeline(monkeypatch, tmp_path, stats)

    with pytest.raises(RuntimeError, match="통합 Wiki 생성 실패"):
        run_pipeline.main()
