import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.api.dependencies import ServiceContainer
from app.api.main import create_app
from app.domain.evidence import SearchTask
from app.domain.policy import PolicyContext
from app.graphs.deep_research import DeepResearchWorkflow
from app.observability.tracing import TraceEvent, hash_trace_value
from app.retrieval.service import RetrievalService
from app.workers.research import ResearchWorker


class RecordingTraceSink:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def _assert_safe_events(sink, *raw_values):
    payload = json.dumps(
        [event.model_dump(mode="json") for event in sink.events],
        ensure_ascii=False,
    )
    for raw in raw_values:
        assert raw not in payload
    assert all(isinstance(event, TraceEvent) for event in sink.events)


def _version_hashes():
    return {
        "index_version_hash": hash_trace_value("mail-v2"),
        "prompt_version_hash": hash_trace_value("fast-v1"),
        "model_hash": hash_trace_value("gpt-oss-120b"),
    }


def test_trace_contains_only_opaque_operational_metadata():
    event = TraceEvent(
        trace_id="trace-1",
        node_name="retrieve",
        duration_ms=12,
        status="ok",
        **_version_hashes(),
        owner_hash=hash_trace_value("kim"),
        query_hash=hash_trace_value("secret mail question"),
        document_hashes=[hash_trace_value("kim-mail-001")],
        evidence_count=1,
        retrieval_mode="hybrid",
    )

    dumped = event.model_dump()

    assert dumped["owner_hash"] != "kim"
    assert dumped["query_hash"] != "secret mail question"
    assert dumped["document_hashes"] != ["kim-mail-001"]
    assert set(dumped).isdisjoint(
        {
            "user_id",
            "query",
            "content",
            "mail_text",
            "evidence",
            "raw_path",
            "credentials",
            "chain_of_thought",
        }
    )


@pytest.mark.parametrize(
    "unsafe_field",
    [
        "user_id",
        "query",
        "content",
        "mail_text",
        "evidence",
        "raw_path",
        "credentials",
        "chain_of_thought",
        "error_message",
        "index_version",
        "prompt_version",
        "model",
    ],
)
def test_trace_rejects_raw_or_secret_fields(unsafe_field):
    payload = {
        "trace_id": "trace-1",
        "node_name": "retrieve",
        "duration_ms": 12,
        "status": "error",
        **_version_hashes(),
        unsafe_field: "sensitive",
    }

    with pytest.raises(ValidationError):
        TraceEvent.model_validate(payload)


def test_trace_accepts_error_class_but_not_exception_text():
    event = TraceEvent(
        trace_id="trace-1",
        node_name="retrieve",
        duration_ms=9,
        status="error",
        **_version_hashes(),
        error_class="TimeoutError",
        attempt=1,
    )

    assert event.error_class == "TimeoutError"


def test_trace_rejects_raw_document_identifiers_in_hash_field():
    with pytest.raises(ValidationError):
        TraceEvent(
            trace_id="trace-1",
            node_name="retrieve",
            duration_ms=9,
            status="ok",
            **_version_hashes(),
            document_hashes=["kim-mail-001"],
        )


def test_api_emits_content_safe_trace_event():
    sink = RecordingTraceSink()
    app = create_app(
        ServiceContainer(
            agentic=None,
            conversations=None,
            jobs=None,
            traces=sink,
        )
    )

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            return await client.get("/health")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert [event.node_name for event in sink.events] == ["api.request"]
    _assert_safe_events(sink, "/health")


def test_retrieval_emits_only_hashes_counts_mode_and_error_class():
    class Search:
        async def search(self, index, body):
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "raw-document-id",
                            "_score": 1,
                            "_source": {
                                "user_id": "kim",
                                "text": "private evidence excerpt",
                            },
                        }
                    ]
                }
            }

    class BrokenEmbedding:
        async def embed(self, text):
            raise TimeoutError("raw secret exception")

    sink = RecordingTraceSink()
    service = RetrievalService(
        Search(),
        BrokenEmbedding(),
        child_index="weekly_mail",
        trace_sink=sink,
    )
    result = asyncio.run(
        service.search(
            SearchTask(query="private query"),
            PolicyContext.from_user_id("kim"),
        )
    )

    assert result.mode == "bm25"
    event = sink.events[-1]
    assert event.node_name == "retrieval.search"
    assert event.retrieval_mode == "bm25"
    assert event.error_class == "TimeoutError"
    _assert_safe_events(
        sink,
        "kim",
        "private query",
        "raw secret exception",
        "raw-document-id",
        "private evidence excerpt",
        "weekly_mail",
    )


def test_deep_workflow_emits_safe_terminal_event():
    class DeepGraph:
        async def ainvoke(self, state):
            return {
                "report": "확인 가능한 조사 근거가 없어 보고서를 제공할 수 없습니다.",
                "evidence": [],
                "branch_results": [],
                "rounds": 1,
                "citation_valid": False,
            }

    deep_sink = RecordingTraceSink()
    deep = DeepResearchWorkflow(None, None, trace_sink=deep_sink)
    deep.graph = DeepGraph()
    asyncio.run(
        deep.invoke(
            "private deep question",
            PolicyContext.from_user_id("kim"),
        )
    )

    assert deep_sink.events[-1].node_name == "deep_research.invoke"
    assert deep_sink.events[-1].route == "deep"
    _assert_safe_events(deep_sink, "kim", "private deep question")


def test_worker_emits_error_class_without_exception_or_job_payload():
    job = SimpleNamespace(
        job_id="raw-job-id",
        user_id="kim",
        question="private worker question",
        filters=None,
        status="running",
        lease_token="raw-lease-token",
    )

    class Jobs:
        async def claim(self, lease_seconds):
            return job

        async def get(self, job_id, policy):
            return job

        async def fail(self, job_id, policy, lease_token, error_code):
            return None

    class Workflow:
        async def invoke(self, question, policy, filters, **kwargs):
            raise RuntimeError("raw worker exception")

    sink = RecordingTraceSink()
    handled = asyncio.run(
        ResearchWorker(Jobs(), Workflow(), trace_sink=sink).run_once()
    )

    assert handled is True
    event = sink.events[-1]
    assert event.node_name == "research_worker.run_once"
    assert event.status == "error"
    assert event.error_class == "RuntimeError"
    _assert_safe_events(
        sink,
        "kim",
        "raw-job-id",
        "raw-lease-token",
        "private worker question",
        "raw worker exception",
    )
