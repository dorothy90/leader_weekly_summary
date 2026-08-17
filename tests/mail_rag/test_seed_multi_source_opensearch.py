import importlib.util
from pathlib import Path
import subprocess
import sys


SCRIPT = Path("scripts/seed_multi_source_opensearch.py")


def _module():
    assert SCRIPT.is_file(), "OpenSearch seed script is missing"
    spec = importlib.util.spec_from_file_location("seed_multi_source", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_script_imports_app_when_launched_outside_repository(tmp_path):
    script = SCRIPT.resolve()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import runpy; "
                f"runpy.run_path({str(script)!r}, run_name='seed_import_test')"
            ),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_index_definition_is_strict_filterable_and_4096_dimension():
    body = _module().build_index_definition(4096)
    properties = body["mappings"]["properties"]

    assert body["settings"]["index"]["knn"] is True
    assert body["mappings"]["dynamic"] == "strict"
    assert properties["embedding"]["dimension"] == 4096
    assert properties["employee_id"] == {"type": "keyword"}
    assert properties["is_active"] == {"type": "boolean"}
    assert properties["is_cancelled"] == {"type": "boolean"}
    assert properties["received_at"] == {"type": "date"}
    assert properties["start_at_utc"] == {"type": "date"}


def test_fixture_document_is_flattened_for_production_queries():
    module = _module()
    calendar = {
        "document_id": "event-kim-20260817",
        "source_type": "calendar",
        "content_kind": "event",
        "source_id": "event-kim-20260817",
        "parent_event_id": "event-kim-20260817",
        "employee_id": "kim",
        "is_active": True,
        "is_cancelled": False,
        "title": "NAND 수율 점검 일정",
        "text": "NAND 수율 점검 회의",
        "occurred_at": "2026-08-17T01:00:00Z",
        "metadata": {
            "calendar_item_id": "event-kim-20260817",
            "start_at_utc": "2026-08-17T01:00:00Z",
            "end_at_utc": "2026-08-17T02:00:00Z",
            "timezone": "Asia/Seoul",
        },
    }

    document = module.build_index_document(calendar, [0.25, 0.5])

    assert document["subject"] == calendar["title"]
    assert document["calendar_item_id"] == calendar["document_id"]
    assert document["start_at_utc"] == "2026-08-17T01:00:00Z"
    assert document["end_at_utc"] == "2026-08-17T02:00:00Z"
    assert document["embedding"] == [0.25, 0.5]
    assert "metadata" not in document
    assert "occurred_at" not in document


def test_mail_fixture_time_becomes_received_at():
    module = _module()
    mail = {
        "document_id": "mail-kim-20260817",
        "source_type": "mail",
        "content_kind": "body",
        "source_id": "mail-kim-20260817",
        "employee_id": "kim",
        "is_active": True,
        "is_cancelled": False,
        "title": "업무 메일",
        "text": "업무 내용",
        "occurred_at": "2026-08-17T00:00:00Z",
        "metadata": {"chunk_index": 0},
    }

    document = module.build_index_document(mail, [0.1])

    assert document["received_at"] == "2026-08-17T00:00:00Z"
    assert document["chunk_index"] == 0


def test_alias_cutover_removes_old_indices_and_adds_target():
    actions = _module().build_alias_actions(
        "ews-calendar-active",
        "ews-calendar-active-dummy-202608-v1",
        {"ews-calendar-previous", "ews-calendar-active-dummy-202608-v1"},
    )

    assert actions == [
        {
            "remove": {
                "index": "ews-calendar-previous",
                "alias": "ews-calendar-active",
            }
        },
        {
            "add": {
                "index": "ews-calendar-active-dummy-202608-v1",
                "alias": "ews-calendar-active",
            }
        },
    ]
