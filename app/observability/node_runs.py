import asyncio
import inspect
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from time import perf_counter
from typing import Iterator, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_NODE_RUNS = 64


class NodeRunMetrics(BaseModel):
    """Allowlisted counts and flags; payload text is intentionally impossible."""

    model_config = ConfigDict(extra="forbid")

    history_messages: int = Field(default=0, ge=0, le=20)
    task_count: int = Field(default=0, ge=0, le=24)
    search_count: int = Field(default=0, ge=0, le=24)
    candidate_count: int = Field(default=0, ge=0, le=10_000)
    evidence_count: int = Field(default=0, ge=0, le=32)
    rewrite_count: int = Field(default=0, ge=0, le=10)
    revision_count: int = Field(default=0, ge=0, le=10)
    retrieval_mode: Literal["hybrid", "bm25", "not_used", "not_started"] | None = None
    fallback_used: bool = False


class NodeRun(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    sequence: int = Field(ge=1, le=10_000)
    node_name: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    status: Literal["ok", "error", "cancelled"]
    started_ms: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    attempt: int = Field(default=1, ge=1, le=100)
    input: NodeRunMetrics = Field(default_factory=NodeRunMetrics)
    output: NodeRunMetrics = Field(default_factory=NodeRunMetrics)
    error_class: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$",
    )


@dataclass
class NodeRunHandle:
    recorder: "NodeRunRecorder | None"
    sequence: int = 0
    node_name: str = "noop"
    started_at: float = 0
    input_metrics: dict = field(default_factory=dict)
    output_metrics: dict = field(default_factory=dict)
    attempt: int = 1

    def update(
        self,
        *,
        input_metrics: dict | None = None,
        output_metrics: dict | None = None,
    ) -> None:
        if input_metrics:
            self.input_metrics.update(input_metrics)
        if output_metrics:
            self.output_metrics.update(output_metrics)


class NodeRunRecorder:
    def __init__(self):
        self.started_at = perf_counter()
        self._next_sequence = 1
        self._runs: list[NodeRun] = []

    def start(
        self,
        node_name: str,
        *,
        input_metrics: dict | None = None,
        attempt: int = 1,
    ) -> NodeRunHandle:
        sequence = self._next_sequence
        self._next_sequence += 1
        return NodeRunHandle(
            recorder=self,
            sequence=sequence,
            node_name=node_name,
            started_at=perf_counter(),
            input_metrics=dict(input_metrics or {}),
            attempt=attempt,
        )

    def finish(
        self,
        handle: NodeRunHandle,
        *,
        status: Literal["ok", "error", "cancelled"],
        error_class: str | None = None,
    ) -> None:
        if handle.sequence > MAX_NODE_RUNS:
            return
        self._runs.append(
            NodeRun(
                sequence=handle.sequence,
                node_name=handle.node_name,
                status=status,
                started_ms=max(0, int((handle.started_at - self.started_at) * 1000)),
                duration_ms=max(0, int((perf_counter() - handle.started_at) * 1000)),
                attempt=handle.attempt,
                input=NodeRunMetrics.model_validate(handle.input_metrics),
                output=NodeRunMetrics.model_validate(handle.output_metrics),
                error_class=error_class,
            )
        )

    def snapshot(self) -> list[NodeRun]:
        return sorted(self._runs, key=lambda item: item.sequence)[:MAX_NODE_RUNS]


_CURRENT_RECORDER: ContextVar[NodeRunRecorder | None] = ContextVar(
    "node_run_recorder", default=None
)


def current_node_recorder() -> NodeRunRecorder | None:
    return _CURRENT_RECORDER.get()


def metrics_from_state(state: object) -> dict:
    if not isinstance(state, dict):
        return {}
    metrics: dict = {}
    conversation = state.get("conversation")
    messages = getattr(conversation, "messages", None)
    if isinstance(messages, list):
        metrics["history_messages"] = min(20, len(messages))
    tasks = state.get("tasks")
    queries = state.get("queries")
    if isinstance(tasks, list):
        metrics["task_count"] = min(24, len(tasks))
    elif isinstance(queries, list):
        metrics["task_count"] = min(24, len(queries))
    if isinstance(state.get("searches"), int):
        metrics["search_count"] = min(24, max(0, state["searches"]))
    evidence = state.get("evidence")
    if isinstance(evidence, list):
        metrics["evidence_count"] = min(32, len(evidence))
    elif isinstance(state.get("branch_results"), list):
        count = sum(
            len(getattr(branch, "evidence", []) or [])
            for branch in state["branch_results"]
        )
        metrics["evidence_count"] = min(32, count)
    if isinstance(state.get("rewrites"), int):
        metrics["rewrite_count"] = min(10, max(0, state["rewrites"]))
    if isinstance(state.get("revisions"), int):
        metrics["revision_count"] = min(10, max(0, state["revisions"]))
    mode = state.get("retrieval_mode")
    if mode in {"hybrid", "bm25", "not_used", "not_started"}:
        metrics["retrieval_mode"] = mode
        metrics["fallback_used"] = mode == "bm25"
    disclosures = state.get("disclosures")
    if isinstance(disclosures, list) and disclosures:
        metrics["fallback_used"] = metrics.get("fallback_used", False) or any(
            "BM25" in str(item) for item in disclosures
        )
    return metrics


def instrument_node(node_name: str, function):
    @wraps(function)
    async def wrapped(state):
        async with record_node(
            node_name,
            input_metrics=metrics_from_state(state),
        ) as run:
            result = function(state)
            if inspect.isawaitable(result):
                result = await result
            merged = {**state, **result} if isinstance(result, dict) else state
            run.update(output_metrics=metrics_from_state(merged))
            return result

    return wrapped


@contextmanager
def use_node_recorder(recorder: NodeRunRecorder) -> Iterator[NodeRunRecorder]:
    token = _CURRENT_RECORDER.set(recorder)
    try:
        yield recorder
    finally:
        _CURRENT_RECORDER.reset(token)


@contextmanager
def ensure_node_recorder() -> Iterator[NodeRunRecorder]:
    existing = current_node_recorder()
    if existing is not None:
        yield existing
        return
    recorder = NodeRunRecorder()
    with use_node_recorder(recorder):
        yield recorder


@asynccontextmanager
async def record_node(
    node_name: str,
    *,
    input_metrics: dict | None = None,
    attempt: int = 1,
):
    recorder = current_node_recorder()
    handle = (
        recorder.start(
            node_name,
            input_metrics=input_metrics,
            attempt=attempt,
        )
        if recorder is not None
        else NodeRunHandle(None)
    )
    try:
        yield handle
    except asyncio.CancelledError:
        if recorder is not None:
            recorder.finish(
                handle,
                status="cancelled",
                error_class="CancelledError",
            )
        raise
    except Exception as error:
        if recorder is not None:
            recorder.finish(
                handle,
                status="error",
                error_class=type(error).__name__,
            )
        raise
    else:
        if recorder is not None:
            recorder.finish(handle, status="ok")
