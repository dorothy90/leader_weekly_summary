from hashlib import sha256
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


def hash_trace_value(value: str) -> str:
    """Return a stable opaque value suitable for correlation, never raw input."""
    return sha256(value.encode("utf-8")).hexdigest()


class TraceSink(Protocol):
    def emit(self, event: "TraceEvent") -> None: ...


class NoOpTraceSink:
    def emit(self, event: "TraceEvent") -> None:
        return None


_NOOP_TRACE_SINK = NoOpTraceSink()


def trace_sink(value: TraceSink | None) -> TraceSink:
    return value if value is not None else _NOOP_TRACE_SINK


def emit_trace(sink: TraceSink | None, event: "TraceEvent") -> None:
    """Observability is fail-open and receives only validated safe metadata."""
    try:
        trace_sink(sink).emit(event)
    except Exception:
        return None


class TraceEvent(BaseModel):
    """Allowlisted operational metadata with no user or mail payload fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    trace_id: str = Field(min_length=1, max_length=128)
    node_name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")
    duration_ms: int = Field(ge=0)
    status: Literal["ok", "error", "cancelled"]
    index_version_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    prompt_version_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    owner_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    query_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    document_hashes: list[Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]] = Field(
        default_factory=list, max_length=32
    )
    job_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evidence_count: int = Field(default=0, ge=0, le=32)
    retrieval_mode: Literal["hybrid", "bm25"] | None = None
    route: Literal["fast", "deep", "clarify", "general"] | None = None
    attempt: int = Field(default=0, ge=0, le=100)
    error_class: str | None = Field(
        default=None,
        max_length=128,
        pattern=r"^[A-Za-z_][A-Za-z0-9_.]*$",
    )
