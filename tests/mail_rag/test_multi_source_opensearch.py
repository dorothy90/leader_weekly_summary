import asyncio
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from opensearchpy.exceptions import (
    ConnectionError as OpenSearchConnectionError,
    ConnectionTimeout as OpenSearchConnectionTimeout,
    TransportError as OpenSearchTransportError,
)

from app.config.settings import Settings
from app.domain.agentic import (
    IntentDecision,
    QueryAnalysis,
    SearchDocument,
    SourceRequest,
    ToolAction,
)
from app.domain.agentic_policy import TypedAgentPolicy
from app.domain.chat import BM25_FALLBACK_DISCLOSURE, ChatRequest
from app.domain.evidence import RetrievalFilters
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.graphs.multi_source import MultiSourceAgenticWorkflow
from app.persistence.conversations import (
    ConversationMemory,
    apply_agent_memory_update,
)
from app.retrieval.multi_source_opensearch import OpenSearchMultiSourceSearch
from app.retrieval.source_registry import SourceRegistry


class RecordingBackend:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    async def search(self, index, body):
        self.calls.append((index, deepcopy(body)))
        if self.responses:
            return self.responses.pop(0)
        return {"hits": {"hits": []}}


class FixedEmbedding:
    async def embed(self, text):
        return [0.1, 0.2]


def workflow(backend, *, settings=None, embeddings=None):
    return OpenSearchMultiSourceSearch(
        backend,
        embeddings or FixedEmbedding(),
        SourceRegistry.from_settings(settings or Settings()),
    )


def analysis(**updates):
    question_type = updates.pop("question_type", "multi_source")
    sources = {
        "domain_knowledge": ["domain_knowledge"],
        "mail_search": ["mail"],
        "calendar_search": ["calendar"],
        "multi_source": ["mail", "calendar", "domain_knowledge"],
    }.get(question_type, [])
    base = QueryAnalysis.from_intent(
        IntentDecision(
            intent="test",
            source_requests=[
                SourceRequest(source=source, query="test")
                for source in sources
            ],
        )
    )
    return QueryAnalysis.model_validate(
        {**base.model_dump(), **updates}
    )


class SequencedIntentAnalyzer:
    def __init__(self, *decisions):
        self.decisions = list(decisions)
        self.policy = TypedAgentPolicy()

    async def analyze(self, question, memory, timezone_name):
        decision = self.decisions.pop(0)
        return QueryAnalysis.from_intent(
            decision,
            timezone_name=timezone_name,
        )

    async def plan(self, question, analysis, observations, memory):
        del question
        return self.policy.next_action(analysis, observations, memory)

    async def judge(
        self,
        question,
        analysis,
        observations,
        documents,
        memory,
        iteration_count,
    ):
        del question
        decision = self.policy.judge(
            analysis,
            observations,
            documents,
            memory,
            iteration_count,
        )
        if (
            not decision.sufficient
            and decision.recommended_action is None
            and analysis.calendar_detail_required
        ):
            event = next(
                (
                    item
                    for item in documents
                    if item.source_type == "calendar"
                    and item.content_kind == "event"
                    and item.source_id
                ),
                None,
            )
            if event is not None:
                return decision.model_copy(
                    update={
                        "recommended_action": ToolAction(
                            tool="expand_calendar_event",
                            event_id=event.source_id,
                            reason="scripted test expansion",
                        )
                    }
                )
        return decision

    async def answer(self, question, analysis, documents, missing, memory):
        del question, analysis, memory
        return self.policy.answer(documents, missing)


def filters_from(body):
    return body["query"]["bool"]["filter"]


def optional_active_filter():
    return {
        "bool": {
            "should": [
                {"term": {"is_active": True}},
                {
                    "bool": {
                        "must_not": [{"exists": {"field": "is_active"}}]
                    }
                },
            ],
            "minimum_should_match": 1,
        }
    }


def hit(document_id, *, score=1, **source):
    return {"_id": document_id, "_score": score, "_source": source}


def test_mail_bm25_and_vector_queries_use_configured_alias_and_mandatory_filters():
    backend = RecordingBackend()
    configured = Settings(mail_index_alias="tenant-mail-read")

    asyncio.run(
        workflow(backend, settings=configured).execute(
            ToolAction(tool="search_mail", query="NAND", reason="mail"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [call[0] for call in backend.calls] == [
        "tenant-mail-read",
        "tenant-mail-read",
    ]
    assert "ews-mail-v1" not in repr(backend.calls)
    for _index, body in backend.calls:
        assert {"term": {"employee_id": "kim"}} in filters_from(body)
        assert {"term": {"is_active": True}} in filters_from(body)


def test_domain_queries_use_configured_index_and_optional_active_filter():
    backend = RecordingBackend()
    configured = Settings(domain_knowledge_index="domain-read")

    asyncio.run(
        workflow(backend, settings=configured).execute(
            ToolAction(tool="search_domain_knowledge", query="NAND", reason="domain"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [index for index, _body in backend.calls] == [
        "domain-read",
        "domain-read",
    ]
    for _index, body in backend.calls:
        assert filters_from(body) == [optional_active_filter()]


def test_domain_knowledge_supports_page_content_embedding_only_schema():
    response = {
        "hits": {
            "hits": [
                hit(
                    "domain-1",
                    page_content="Cell Leakage는 대기 누설 전류입니다.",
                )
            ]
        }
    }
    backend = RecordingBackend([response, response])

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="search_domain_knowledge",
                query="Cell Leakage 의미",
                reason="domain",
            ),
            PolicyContext.from_user_id("kim"),
            analysis(question_type="domain_knowledge"),
        )
    )

    assert result.total_hits == 1
    assert result.documents[0].text == "Cell Leakage는 대기 누설 전류입니다."
    assert "page_content^3" in backend.calls[0][1]["query"]["bool"]["must"][0][
        "multi_match"
    ]["fields"]
    assert all("page_content" in body["_source"] for _index, body in backend.calls)


def test_calendar_search_and_expansion_repeat_all_security_filters_and_alias():
    empty = {"hits": {"hits": []}}
    parent = {
        "hits": {
            "hits": [
                hit(
                    "event-storage-1",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    calendar_item_id="event-1",
                    content_kind="event",
                    text="meeting",
                )
            ]
        }
    }
    backend = RecordingBackend([empty, empty, parent, empty])
    configured = Settings(calendar_index_alias="tenant-calendar-read")
    service = workflow(backend, settings=configured)
    policy = PolicyContext.from_user_id("kim")

    asyncio.run(
        service.execute(
            ToolAction(tool="search_calendar", query="NAND", reason="meeting"),
            policy,
            analysis(),
        )
    )
    asyncio.run(
        service.execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="action",
            ),
            policy,
            analysis(),
        )
    )

    assert [index for index, _body in backend.calls] == [
        "tenant-calendar-read",
        "tenant-calendar-read",
        "tenant-calendar-read",
        "tenant-calendar-read",
    ]
    for _index, body in backend.calls:
        filters = filters_from(body)
        assert {"term": {"employee_id": "kim"}} in filters
        assert {"term": {"is_active": True}} in filters
        assert {"term": {"is_cancelled": False}} in filters


def test_calendar_optional_filters_and_date_range_are_backend_built():
    backend = RecordingBackend()
    current = analysis(
        start_at_utc=datetime(2026, 8, 2, 15, tzinfo=UTC),
        end_at_utc=datetime(2026, 8, 9, 15, tzinfo=UTC),
    )

    asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="search_calendar",
                query="NAND",
                reason="meeting",
                content_kinds=["event"],
                attachment_name="action.pdf",
                organizer_email="lead@example.com",
                attendee_emails=["kim@example.com"],
            ),
            PolicyContext.from_user_id("kim"),
            current,
        )
    )

    filters = filters_from(backend.calls[0][1])
    assert {"terms": {"content_kind": ["event"]}} in filters
    assert {"wildcard": {"attachment_name": "*action.pdf*"}} in filters
    assert {"term": {"organizer_email": "lead@example.com"}} in filters
    assert {"terms": {"attendee_emails": ["kim@example.com"]}} in filters
    assert {
        "range": {
            "start_at_utc": {
                "gte": "2026-08-02T15:00:00+00:00",
                "lt": "2026-08-09T15:00:00+00:00",
            }
        }
    } in filters


def test_mail_date_ranges_are_half_open_on_received_at():
    current = analysis(
        start_at_utc=datetime(2026, 8, 2, 15, tzinfo=UTC),
        end_at_utc=datetime(2026, 8, 9, 15, tzinfo=UTC),
    )

    backend = RecordingBackend()
    asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_mail", query="NAND", reason="range"),
            PolicyContext.from_user_id("kim"),
            current,
        )
    )
    assert {
        "range": {
            "received_at": {
                "gte": "2026-08-02T15:00:00+00:00",
                "lt": "2026-08-09T15:00:00+00:00",
            }
        }
    } in filters_from(backend.calls[0][1])


def test_domain_knowledge_does_not_inherit_mail_date_filters():
    backend = RecordingBackend()
    current = analysis(
        start_at_utc=datetime(2026, 8, 2, 15, tzinfo=UTC),
        end_at_utc=datetime(2026, 8, 9, 15, tzinfo=UTC),
    )

    asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="search_domain_knowledge",
                query="NAND",
                reason="timeless domain corpus",
            ),
            PolicyContext.from_user_id("kim"),
            current,
        )
    )

    for _index, body in backend.calls:
        assert filters_from(body) == [optional_active_filter()]


def test_mail_facets_are_added_to_backend_built_filters():
    backend = RecordingBackend()

    asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_mail", query="NAND", reason="facets"),
            PolicyContext.from_user_id("kim"),
            analysis(),
            request_filters=RetrievalFilters(
                teams=["YIELD팀"],
                weeks=["2026-08"],
                mail_type="weekly_report",
            ),
        )
    )

    for _index, body in backend.calls:
        filters = filters_from(body)
        assert {"terms": {"team": ["YIELD팀"]}} in filters
        assert {"terms": {"week": ["2026-08"]}} in filters
        assert {"term": {"mail_type": "weekly_report"}} in filters


def test_mail_hits_are_post_filtered_against_trusted_facets_and_date_range():
    base = {
        "employee_id": "kim",
        "is_active": True,
        "content_kind": "body",
        "text": "NAND yield report",
        "team": "YIELD팀",
        "week": "2026-08",
        "mail_type": "weekly_report",
        "received_at": "2026-08-05T01:00:00Z",
    }
    response = {
        "hits": {
            "hits": [
                hit("valid", **base),
                hit("wrong-team", **{**base, "team": "OTHER"}),
                hit("wrong-week", **{**base, "week": "2026-09"}),
                hit("wrong-type", **{**base, "mail_type": "daily_report"}),
                hit(
                    "out-of-range",
                    **{**base, "received_at": "2026-08-09T15:00:00Z"},
                ),
            ]
        }
    }
    backend = RecordingBackend([response, response])

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_mail", query="NAND", reason="facets"),
            PolicyContext.from_user_id("kim"),
            analysis(
                start_at_utc=datetime(2026, 8, 2, 15, tzinfo=UTC),
                end_at_utc=datetime(2026, 8, 9, 15, tzinfo=UTC),
            ),
            request_filters=RetrievalFilters(
                teams=["YIELD팀"],
                weeks=["2026-08"],
                mail_type="weekly_report",
            ),
        )
    )

    assert [item.document_id for item in result.documents] == ["valid"]


@pytest.mark.parametrize(
    ("backend_error", "expected_code"),
    [
        (
            OpenSearchConnectionTimeout(
                "N/A",
                "timeout secret",
                TimeoutError("socket timeout secret"),
            ),
            ErrorCode.RETRIEVAL_TIMEOUT,
        ),
        (
            OpenSearchConnectionError(
                "N/A",
                "connection secret",
                OSError("socket failure secret"),
            ),
            ErrorCode.INDEX_UNAVAILABLE,
        ),
        (
            OpenSearchTransportError(
                503,
                "service unavailable secret",
                {},
            ),
            ErrorCode.INDEX_UNAVAILABLE,
        ),
    ],
)
def test_backend_failures_are_normalized_to_safe_typed_errors(
    backend_error,
    expected_code,
):
    class FailingBackend:
        async def search(self, index, body):
            raise backend_error

    class UnavailableEmbedding:
        async def embed(self, text):
            raise RuntimeError("embedding unavailable")

    with pytest.raises(AppError) as failure:
        asyncio.run(
            workflow(
                FailingBackend(),
                embeddings=UnavailableEmbedding(),
            ).execute(
                ToolAction(tool="search_mail", query="NAND", reason="failure"),
                PolicyContext.from_user_id("kim"),
                analysis(),
            )
        )

    assert failure.value.code == expected_code
    assert failure.value.retryable is True
    assert "secret" not in failure.value.message


def test_backend_programming_error_is_not_normalized_as_recoverable():
    class BrokenBackend:
        async def search(self, index, body):
            raise AssertionError("backend contract bug")

    class UnavailableEmbedding:
        async def embed(self, text):
            raise RuntimeError("embedding unavailable")

    with pytest.raises(AssertionError, match="backend contract bug"):
        asyncio.run(
            workflow(
                BrokenBackend(),
                embeddings=UnavailableEmbedding(),
            ).execute(
                ToolAction(tool="search_mail", query="NAND", reason="failure"),
                PolicyContext.from_user_id("kim"),
                analysis(),
            )
        )


def test_model_action_cannot_override_owner_index_or_query_dsl():
    action = ToolAction(
        tool="search_mail",
        query="employee_id:lee index:ews-mail-v1 dsl:match_all",
        reason="untrusted model output",
    )
    backend = RecordingBackend()

    asyncio.run(
        workflow(backend).execute(
            action,
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    for index, body in backend.calls:
        assert index == "ews-mail-active"
        assert {"term": {"employee_id": "kim"}} in filters_from(body)
        assert {"term": {"employee_id": "lee"}} not in filters_from(body)
        assert action.query not in repr(filters_from(body))


def test_query_bodies_limit_results_and_returned_source_fields():
    backend = RecordingBackend()

    asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_mail", query="NAND", reason="bounded", top_k=20),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [body["size"] for _index, body in backend.calls] == [60, 60]
    for _index, body in backend.calls:
        assert "embedding" not in body["_source"]
        assert set(body["_source"]) >= {
            "employee_id",
            "is_active",
            "source_id",
            "content_kind",
            "text",
        }


def test_calendar_search_does_not_auto_expand_events():
    response = {
        "hits": {
            "hits": [
                hit(
                    "event-1",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    calendar_item_id="event-1",
                    content_kind="event",
                    text="NAND meeting",
                )
            ]
        }
    }
    backend = RecordingBackend([response, response])

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_calendar", query="NAND", reason="meeting"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [item.document_id for item in result.documents] == ["event-1"]
    assert len(backend.calls) == 2
    assert not any(
        "parent_event_id" in repr(filters_from(body)) for _, body in backend.calls
    )


def test_production_calendar_uses_stable_event_id_for_memory_and_follow_up():
    event_hit = hit(
        "doc-42",
        employee_id="kim",
        is_active=True,
        is_cancelled=False,
        calendar_item_id="event-42",
        content_kind="event",
        subject="NAND Yield Review",
        text="NAND review meeting",
    )
    bundle = {
        "hits": {
            "hits": [
                event_hit,
                hit(
                    "doc-attachment-42",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    parent_event_id="event-42",
                    content_kind="attachment",
                    attachment_name="action.pdf",
                    text="Action: inspect the FDC log",
                ),
            ]
        }
    }

    class ProductionShapedBackend(RecordingBackend):
        async def search(self, index, body):
            self.calls.append((index, deepcopy(body)))
            parent_gate = (
                {"term": {"calendar_item_id": "event-42"}}
                in filters_from(body)
                and {"term": {"content_kind": "event"}}
                in filters_from(body)
            )
            if parent_gate:
                return {"hits": {"hits": [deepcopy(event_hit)]}}
            relation = next(
                (
                    item
                    for item in filters_from(body)
                    if "bool" in item and "should" in item["bool"]
                ),
                None,
            )
            if relation is not None:
                return deepcopy(bundle)
            return {"hits": {"hits": [deepcopy(event_hit)]}}

    backend = ProductionShapedBackend()
    search = workflow(backend)
    agent = MultiSourceAgenticWorkflow(
        search,
        SequencedIntentAnalyzer(
            IntentDecision(
                intent="calendar_detail",
                source_requests=[
                    SourceRequest(source="calendar", query="NAND Yield Review")
                ],
                calendar_detail_required=True,
            ),
            IntentDecision(
                intent="calendar_follow_up",
                source_requests=[
                    SourceRequest(source="calendar", query="NAND Yield Review")
                ],
                event_reference="previous_event",
                calendar_detail_required=True,
            ),
        ),
    )
    policy = PolicyContext.from_user_id("kim")

    first = asyncio.run(
        agent.invoke(
            ChatRequest(
                user_id="kim",
                message="NAND Yield Review 회의에서 Action 뭐였어?",
            ),
            policy,
            ConversationMemory(),
        )
    )
    memory = apply_agent_memory_update(
        ConversationMemory(), first.agent_memory, policy
    )

    assert "doc-42" in {item.document_id for item in first.evidence}
    assert "event-42" not in {item.document_id for item in first.evidence}
    assert memory.previous_event_reference.event_id == "event-42"

    follow_up = asyncio.run(
        agent.invoke(
            ChatRequest(
                user_id="kim",
                message="그 회의에서 Action 뭐였어?",
            ),
            policy,
            memory,
        )
    )

    relation_calls = [
        body
        for _index, body in backend.calls
        if any(
            "bool" in item and "should" in item["bool"]
            for item in filters_from(body)
        )
    ]
    parent_gate_calls = [
        body
        for _index, body in backend.calls
        if {"term": {"calendar_item_id": "event-42"}}
        in filters_from(body)
        and {"term": {"content_kind": "event"}} in filters_from(body)
    ]
    assert len(relation_calls) == 2
    assert len(parent_gate_calls) == 2
    assert follow_up.agent_memory.previous_event_reference.event_id == "event-42"
    for body in [*parent_gate_calls, *relation_calls]:
        serialized = repr(filters_from(body))
        assert "event-42" in serialized
        assert "doc-42" not in serialized
        assert {"term": {"employee_id": "kim"}} in filters_from(body)
        assert {"term": {"is_active": True}} in filters_from(body)
        assert {"term": {"is_cancelled": False}} in filters_from(body)


def test_hybrid_searches_start_concurrently_after_embedding_finishes():
    class SequencedEmbedding:
        def __init__(self):
            self.finished = False

        async def embed(self, text):
            self.finished = True
            return [0.1, 0.2]

    class CoordinatedBackend:
        def __init__(self, embeddings):
            self.embeddings = embeddings
            self.started = 0
            self.both_started = asyncio.Event()

        async def search(self, index, body):
            assert self.embeddings.finished is True
            self.started += 1
            if self.started == 2:
                self.both_started.set()
            await asyncio.wait_for(self.both_started.wait(), timeout=0.2)
            return {"hits": {"hits": []}}

    embeddings = SequencedEmbedding()
    backend = CoordinatedBackend(embeddings)

    result = asyncio.run(
        workflow(backend, embeddings=embeddings).execute(
            ToolAction(tool="search_mail", query="NAND", reason="concurrent"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert result.retrieval_mode == "hybrid"
    assert backend.started == 2


def test_embedding_failure_runs_bm25_only_with_exact_disclosure():
    class BrokenEmbedding:
        async def embed(self, text):
            raise TimeoutError("offline secret-token")

    backend = RecordingBackend()
    result = asyncio.run(
        workflow(backend, embeddings=BrokenEmbedding()).execute(
            ToolAction(tool="search_mail", query="NAND", reason="mail"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert result.retrieval_mode == "bm25"
    assert result.disclosures == [BM25_FALLBACK_DISCLOSURE]
    assert len(backend.calls) == 1
    assert "knn" not in repr(backend.calls[0][1])
    assert "secret-token" not in result.model_dump_json()


def test_hybrid_rrf_order_and_scores_are_deterministic():
    bm25 = {
        "hits": {
            "hits": [
                hit("b", is_active=True, text="NAND B"),
                hit("a", is_active=True, text="NAND A"),
                hit("c", is_active=True, text="NAND C"),
            ]
        }
    }
    vector = {
        "hits": {
            "hits": [
                hit("b", is_active=True, text="NAND B"),
                hit("a", is_active=True, text="NAND A"),
                hit("d", is_active=True, text="NAND D"),
            ]
        }
    }
    backend = RecordingBackend([bm25, vector])

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_domain_knowledge", query="NAND", reason="domain"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [item.document_id for item in result.documents] == ["b", "a", "c", "d"]
    assert result.documents[0].score > result.documents[2].score
    assert result.documents[2].score == result.documents[3].score


def test_post_filter_drops_foreign_inactive_cancelled_and_malformed_hits():
    hits = {
        "hits": {
            "hits": [
                hit(
                    "foreign",
                    score=99,
                    employee_id="lee",
                    is_active=True,
                    is_cancelled=False,
                    content_kind="event",
                    text="NAND secret",
                ),
                hit(
                    "inactive",
                    score=98,
                    employee_id="kim",
                    is_active=False,
                    is_cancelled=False,
                    content_kind="event",
                    text="NAND old",
                ),
                hit(
                    "cancelled",
                    score=97,
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=True,
                    content_kind="event",
                    text="NAND cancelled",
                ),
                {"_id": "bad-source", "_score": 10, "_source": "not-a-map"},
                hit(
                    "bad-kind",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    content_kind="unsupported",
                    text="NAND malformed",
                ),
                hit(
                    "allowed",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    calendar_item_id="allowed",
                    content_kind="event",
                    subject="NAND Review",
                    text="NAND allowed",
                    private_path="/srv/private/event.json",
                ),
            ]
        }
    }
    backend = RecordingBackend([hits, hits])

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_calendar", query="NAND", reason="calendar"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [item.document_id for item in result.documents] == ["allowed"]
    assert result.documents[0].title == "NAND Review"
    assert "private_path" not in result.documents[0].metadata


def test_domain_post_filter_drops_inactive_but_allows_missing_lifecycle():
    response = {
        "hits": {
            "hits": [
                hit("inactive", is_active=False, text="old"),
                hit("missing", text="unknown"),
                hit("active", is_active=True, text="current"),
            ]
        }
    }
    backend = RecordingBackend([response, response])

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(tool="search_domain_knowledge", query="NAND", reason="domain"),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [item.document_id for item in result.documents] == [
        "missing",
        "active",
    ]


def test_calendar_expansion_uses_relation_clause_and_optional_filters():
    parent = {
        "hits": {
            "hits": [
                hit(
                    "event-storage-1",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    calendar_item_id="event-1",
                    content_kind="event",
                    text="meeting",
                )
            ]
        }
    }
    backend = RecordingBackend([parent, {"hits": {"hits": []}}])

    asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="action",
                content_kinds=["attachment"],
                attachment_name="action.pdf",
            ),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert len(backend.calls) == 2
    parent_index, parent_body = backend.calls[0]
    index, body = backend.calls[1]
    assert parent_index == index == "ews-calendar-active"
    parent_filters = filters_from(parent_body)
    filters = filters_from(body)
    assert {"term": {"calendar_item_id": "event-1"}} in parent_filters
    assert {"term": {"content_kind": "event"}} in parent_filters
    assert {"terms": {"content_kind": ["attachment"]}} not in parent_filters
    assert {"wildcard": {"attachment_name": "*action.pdf*"}} not in parent_filters
    assert {
        "bool": {
            "should": [
                {"term": {"calendar_item_id": "event-1"}},
                {"term": {"parent_event_id": "event-1"}},
            ],
            "minimum_should_match": 1,
        }
    } in filters
    assert {"terms": {"content_kind": ["attachment"]}} in filters
    assert {"wildcard": {"attachment_name": "*action.pdf*"}} in filters
    for query_filters in (parent_filters, filters):
        assert {"term": {"employee_id": "kim"}} in query_filters
        assert {"term": {"is_active": True}} in query_filters
        assert {"term": {"is_cancelled": False}} in query_filters
    assert body["size"] == 50


def test_calendar_expansion_returns_same_owner_parent_and_sibling_only():
    response = {
        "hits": {
            "hits": [
                hit(
                    "event-1",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    calendar_item_id="event-1",
                    parent_event_id="event-1",
                    content_kind="event",
                    subject="NAND Review",
                    text="meeting",
                ),
                hit(
                    "attachment-1",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    parent_event_id="event-1",
                    content_kind="attachment",
                    attachment_name="action.pdf",
                    text="FDC action",
                ),
                hit(
                    "foreign",
                    employee_id="lee",
                    is_active=True,
                    is_cancelled=False,
                    parent_event_id="event-1",
                    content_kind="attachment",
                    text="secret",
                ),
                hit(
                    "attachment-1",
                    score=0.5,
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    parent_event_id="event-1",
                    content_kind="attachment",
                    text="duplicate",
                ),
            ]
        }
    }
    backend = RecordingBackend([response, response])

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="action",
            ),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [item.document_id for item in result.documents] == [
        "event-1",
        "attachment-1",
    ]
    assert result.total_hits == 2


def test_calendar_expansion_skips_malformed_scores_and_unrelated_hits():
    response = {
        "hits": {
            "hits": [
                hit(
                    "bad-score",
                    score="not-a-number",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    parent_event_id="event-1",
                    content_kind="attachment",
                    text="malformed",
                ),
                hit(
                    "unrelated",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    parent_event_id="event-2",
                    content_kind="attachment",
                    text="other event",
                ),
            ]
        }
    }
    parent = {
        "hits": {
            "hits": [
                hit(
                    "event-storage-1",
                    employee_id="kim",
                    is_active=True,
                    is_cancelled=False,
                    calendar_item_id="event-1",
                    content_kind="event",
                    text="meeting",
                )
            ]
        }
    }
    backend = RecordingBackend([parent, response])

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="action",
            ),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert result.documents == []
    assert result.total_hits == 0


def _calendar_payload(content_kind="event", **updates):
    payload = {
        "employee_id": "kim",
        "is_active": True,
        "is_cancelled": False,
        "content_kind": content_kind,
        "text": "calendar evidence",
    }
    payload.update(updates)
    return payload


def normalize_calendar_hit(*, document_id="doc-42", content_kind="event", **source):
    action = ToolAction(
        tool="search_calendar",
        query="NAND",
        reason="normalize calendar",
    )
    return workflow(RecordingBackend())._normalize_hits(
        [
            {
                "_id": document_id,
                "_rrf_score": 1,
                "_source": _calendar_payload(content_kind, **source),
            }
        ],
        action,
        PolicyContext.from_user_id("kim"),
    )


VALID_EDGE_EVENT_ID = ("A" * 250) + "_.:@+-"


def test_calendar_event_normalization_preserves_storage_id_and_stable_relation_id():
    documents = normalize_calendar_hit(
        document_id="doc-edge",
        calendar_item_id=VALID_EDGE_EVENT_ID,
        source_id="untrusted-source-id",
    )

    assert len(documents) == 1
    assert documents[0].document_id == "doc-edge"
    assert documents[0].source_id == VALID_EDGE_EVENT_ID
    assert documents[0].parent_event_id == VALID_EDGE_EVENT_ID


@pytest.mark.parametrize(
    "calendar_item_id",
    [
        None,
        "",
        " event-42",
        "event-42 ",
        "event 42",
        "event/42",
        "event=42",
        "event#42",
        "x" * 257,
        42,
    ],
    ids=[
        "missing",
        "empty",
        "leading-space",
        "trailing-space",
        "internal-space",
        "slash",
        "equals",
        "symbol",
        "overlong",
        "non-string",
    ],
)
def test_calendar_event_normalization_rejects_missing_or_unsafe_relation_id(
    calendar_item_id,
):
    source = (
        {}
        if calendar_item_id is None
        else {"calendar_item_id": calendar_item_id}
    )

    assert normalize_calendar_hit(**source) == []


@pytest.mark.parametrize(
    "parent_event_id",
    [
        None,
        "",
        " event-42",
        "event-42 ",
        "event 42",
        "event/42",
        "event=42",
        "event#42",
        "x" * 257,
        42,
    ],
    ids=[
        "missing",
        "empty",
        "leading-space",
        "trailing-space",
        "internal-space",
        "slash",
        "equals",
        "symbol",
        "overlong",
        "non-string",
    ],
)
def test_calendar_attachment_normalization_requires_safe_parent_relation_id(
    parent_event_id,
):
    source = (
        {}
        if parent_event_id is None
        else {"parent_event_id": parent_event_id}
    )

    assert normalize_calendar_hit(content_kind="attachment", **source) == []


def test_calendar_attachment_preserves_storage_identity_and_stable_parent():
    documents = normalize_calendar_hit(
        document_id="doc-attachment-edge",
        content_kind="attachment",
        source_id="attachment-source-42",
        parent_event_id=VALID_EDGE_EVENT_ID,
    )

    assert len(documents) == 1
    assert documents[0].document_id == "doc-attachment-edge"
    assert documents[0].source_id == "attachment-source-42"
    assert documents[0].parent_event_id == VALID_EDGE_EVENT_ID


def _parent_gate_response(**updates):
    source = _calendar_payload(
        "event",
        calendar_item_id="event-1",
        **updates,
    )
    return {"hits": {"hits": [hit("event-storage-1", **source)]}}


def _allowed_child_response(*, parent_event_id="event-1"):
    source = _calendar_payload(
        "attachment",
        parent_event_id=parent_event_id,
        attachment_name="action.pdf",
    )
    return {"hits": {"hits": [hit("attachment-storage-1", **source)]}}


@pytest.mark.parametrize(
    "parent_response",
    [
        {"hits": {"hits": []}},
        _parent_gate_response(employee_id="lee"),
        _parent_gate_response(is_active=False),
        _parent_gate_response(is_cancelled=True),
    ],
    ids=["missing", "foreign", "inactive", "cancelled"],
)
def test_calendar_expansion_rejects_allowed_child_without_authorized_parent(
    parent_response,
):
    class ParentAwareBackend(RecordingBackend):
        async def search(self, index, body):
            self.calls.append((index, deepcopy(body)))
            parent_gate = {"term": {"content_kind": "event"}} in filters_from(body)
            return deepcopy(
                parent_response if parent_gate else _allowed_child_response()
            )

    backend = ParentAwareBackend()
    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="parent gate",
                content_kinds=["attachment"],
            ),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert result.documents == []
    assert result.total_hits == 0
    assert len(backend.calls) == 1


def test_calendar_expansion_applies_attachment_filter_after_parent_gate():
    class ParentAwareBackend(RecordingBackend):
        async def search(self, index, body):
            self.calls.append((index, deepcopy(body)))
            parent_gate = {"term": {"content_kind": "event"}} in filters_from(body)
            if parent_gate:
                return _parent_gate_response()
            return {
                "hits": {
                    "hits": [
                        *_parent_gate_response()["hits"]["hits"],
                        *_allowed_child_response()["hits"]["hits"],
                    ]
                }
            }

    backend = ParentAwareBackend()
    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="attachment only",
                content_kinds=["attachment"],
                attachment_name="action.pdf",
            ),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [item.document_id for item in result.documents] == [
        "attachment-storage-1"
    ]
    assert len(backend.calls) == 2
    assert {"terms": {"content_kind": ["attachment"]}} not in filters_from(
        backend.calls[0][1]
    )
    assert {"terms": {"content_kind": ["attachment"]}} in filters_from(
        backend.calls[1][1]
    )


def test_calendar_expansion_omits_unrelated_child_after_parent_gate():
    backend = RecordingBackend(
        [
            _parent_gate_response(),
            {
                "hits": {
                    "hits": [
                        *_allowed_child_response()["hits"]["hits"],
                        *_allowed_child_response(
                            parent_event_id="event-2"
                        )["hits"]["hits"],
                    ]
                }
            },
        ]
    )

    result = asyncio.run(
        workflow(backend).execute(
            ToolAction(
                tool="expand_calendar_event",
                event_id="event-1",
                reason="relation",
            ),
            PolicyContext.from_user_id("kim"),
            analysis(),
        )
    )

    assert [item.parent_event_id for item in result.documents] == ["event-1"]


def test_production_mail_reconstruction_sorts_and_deduplicates_chunks():
    parts = [
        SearchDocument(
            source_type="mail",
            document_id="p2",
            source_id="mail-1",
            content_kind="body",
            text="second",
            score=0.8,
            metadata={"chunk_index": 2},
        ),
        SearchDocument(
            source_type="mail",
            document_id="p1-copy",
            source_id="mail-1",
            content_kind="body",
            text="first",
            score=0.5,
            metadata={"chunk_index": 1},
        ),
        SearchDocument(
            source_type="mail",
            document_id="p1",
            source_id="mail-1",
            content_kind="body",
            text="first",
            score=1,
            metadata={"chunk_index": 1},
        ),
    ]

    result = OpenSearchMultiSourceSearch._reconstruct_mail(parts, 10)

    assert result[0].document_id == "p1"
    assert result[0].text == "first\n\nsecond"


def normalize_mail_hit(*, document_id="doc-1", **source):
    payload = {
        "employee_id": "kim",
        "is_active": True,
        "content_kind": "body",
        "text": "bounded text",
        **source,
    }
    action = ToolAction(tool="search_mail", query="NAND", reason="normalize")
    return workflow(RecordingBackend())._normalize_hits(
        [{"_id": document_id, "_rrf_score": 1, "_source": payload}],
        action,
        PolicyContext.from_user_id("kim"),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", ["private", "content"]),
        ("text", {"private": "content"}),
        ("subject", ["private", "title"]),
        ("title", {"private": "title"}),
        ("source_id", ["mail-1"]),
        ("source_id", {"id": "mail-1"}),
        ("parent_event_id", ["event-1"]),
        ("parent_event_id", {"id": "event-1"}),
    ],
)
def test_normalization_never_stringifies_structured_source_values(field, value):
    assert normalize_mail_hit(**{field: value}) == []


@pytest.mark.parametrize(
    "document_id",
    [123, ["doc-1"], {"id": "doc-1"}],
)
def test_normalization_requires_string_document_ids(document_id):
    assert normalize_mail_hit(document_id=document_id) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("document_id", "d" * 257),
        ("source_id", "s" * 257),
        ("parent_event_id", "p" * 257),
        ("subject", "t" * 501),
        ("text", "x" * 8001),
    ],
)
def test_normalization_rejects_overlong_structured_fields(field, value):
    if field == "document_id":
        documents = normalize_mail_hit(document_id=value)
    else:
        documents = normalize_mail_hit(**{field: value})
    assert documents == []


@pytest.mark.parametrize(
    "metadata",
    [
        {"timezone": "x" * 513},
        {"attendee_emails": ["user@example.com"] * 21},
        {"attendee_emails": ["x" * 321]},
        {"timezone": {"secret": "nested"}},
        {"attendee_emails": [["nested@example.com"]]},
    ],
)
def test_normalization_rejects_oversized_or_nested_metadata_values(metadata):
    assert normalize_mail_hit(**metadata) == []


def test_normalization_rejects_too_many_safe_metadata_keys():
    metadata = {
        "attachment_id": "a",
        "attachment_name": "b",
        "attendee_emails": ["kim@example.com"],
        "calendar_item_id": "c",
        "chunk_index": 1,
        "end_at_utc": "2026-08-09T15:00:00+00:00",
        "modified_at": "2026-08-09T15:00:00+00:00",
        "occurrence_id": "o",
        "organizer_email": "lead@example.com",
        "received_at": "2026-08-09T15:00:00+00:00",
        "sent_at": "2026-08-09T15:00:00+00:00",
        "series_master_id": "s",
        "start_at_utc": "2026-08-02T15:00:00+00:00",
    }

    assert normalize_mail_hit(**metadata) == []


def test_normalization_rejects_metadata_over_aggregate_serialized_limit():
    metadata = {
        "attachment_id": "a" * 500,
        "attachment_name": "b" * 500,
        "calendar_item_id": "c" * 500,
        "end_at_utc": "d" * 500,
        "modified_at": "e" * 500,
        "occurrence_id": "f" * 500,
        "organizer_email": "g" * 500,
        "received_at": "h" * 500,
        "sent_at": "i" * 500,
    }

    assert normalize_mail_hit(**metadata) == []


def test_normalization_preserves_valid_bounded_metadata_without_aliasing_lists():
    attendees = ["kim@example.com", "lead@example.com"]

    documents = normalize_mail_hit(
        source_id="mail-1",
        title="NAND Review",
        received_at="2026-08-09T15:00:00+00:00",
        timezone="Asia/Seoul",
        attendee_emails=attendees,
        attachment_name="action.pdf",
        chunk_index=3,
        modified_at=None,
    )
    attendees.append("late-mutation@example.com")

    assert len(documents) == 1
    assert documents[0].document_id == "doc-1"
    assert documents[0].source_id == "mail-1"
    assert documents[0].title == "NAND Review"
    assert documents[0].text == "bounded text"
    assert documents[0].metadata == {
        "attachment_name": "action.pdf",
        "attendee_emails": ["kim@example.com", "lead@example.com"],
        "chunk_index": 3,
        "modified_at": None,
        "received_at": "2026-08-09T15:00:00+00:00",
        "timezone": "Asia/Seoul",
    }
