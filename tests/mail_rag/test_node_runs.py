import asyncio
import importlib

import pytest
from pydantic import ValidationError


def _diagnostics_module():
    try:
        return importlib.import_module("app.observability.node_runs")
    except ModuleNotFoundError:
        pytest.fail("node run diagnostics module is not implemented")


def test_node_run_contract_rejects_raw_payload_fields():
    module = _diagnostics_module()

    with pytest.raises(ValidationError):
        module.NodeRun.model_validate(
            {
                "sequence": 1,
                "node_name": "fast.plan",
                "status": "ok",
                "started_ms": 0,
                "duration_ms": 1,
                "query": "private mail question",
            }
        )


def test_recorder_orders_runs_by_start_sequence_and_captures_safe_counts():
    module = _diagnostics_module()

    async def exercise():
        recorder = module.NodeRunRecorder()
        with module.use_node_recorder(recorder):
            async with module.record_node(
                "fast.plan",
                input_metrics={"history_messages": 2},
            ) as slow:
                async with module.record_node("retrieval.embedding") as fast:
                    fast.update(output_metrics={"candidate_count": 1})
                slow.update(output_metrics={"task_count": 1})
        return recorder.snapshot()

    runs = asyncio.run(exercise())

    assert [item.sequence for item in runs] == [1, 2]
    assert [item.node_name for item in runs] == ["fast.plan", "retrieval.embedding"]
    assert runs[0].input.history_messages == 2
    assert runs[0].output.task_count == 1
    assert runs[1].output.candidate_count == 1


def test_recorder_captures_error_class_without_exception_message():
    module = _diagnostics_module()
    recorder = module.NodeRunRecorder()

    async def exercise():
        with module.use_node_recorder(recorder):
            with pytest.raises(RuntimeError):
                async with module.record_node("fast.generate"):
                    raise RuntimeError("private mail content")

    asyncio.run(exercise())
    payload = recorder.snapshot()[0].model_dump(mode="json")

    assert payload["status"] == "error"
    assert payload["error_class"] == "RuntimeError"
    assert "private mail content" not in str(payload)


def test_recorder_marks_cancelled_nodes():
    module = _diagnostics_module()
    recorder = module.NodeRunRecorder()

    async def exercise():
        with module.use_node_recorder(recorder):
            with pytest.raises(asyncio.CancelledError):
                async with module.record_node("deep.research"):
                    raise asyncio.CancelledError

    asyncio.run(exercise())

    assert recorder.snapshot()[0].status == "cancelled"


def test_recorder_is_bounded_to_64_runs():
    module = _diagnostics_module()
    recorder = module.NodeRunRecorder()

    async def exercise():
        with module.use_node_recorder(recorder):
            for _ in range(70):
                async with module.record_node("fast.validate"):
                    pass

    asyncio.run(exercise())

    assert len(recorder.snapshot()) == 64
    assert recorder.snapshot()[0].sequence == 1
    assert recorder.snapshot()[-1].sequence == 64


def test_execution_metadata_accepts_bounded_node_runs():
    module = _diagnostics_module()
    from app.domain.chat import ExecutionMetadata

    execution = ExecutionMetadata(
        status="succeeded",
        node_runs=[
            module.NodeRun(
                sequence=1,
                node_name="router.route",
                status="ok",
                started_ms=0,
                duration_ms=3,
            )
        ],
    )

    assert execution.node_runs[0].node_name == "router.route"
