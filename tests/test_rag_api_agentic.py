import asyncio
import json
import sys
import uuid
import pytest
from pydantic import ValidationError
from types import ModuleType
from types import SimpleNamespace


motor_asyncio = ModuleType("motor.motor_asyncio")
motor_asyncio.AsyncIOMotorClient = object
sys.modules["motor.motor_asyncio"] = motor_asyncio

import rag_api_opensearch_v3 as rag_api
import streamlit_chat_v2 as streamlit_chat


class RecordingGraph:
    def __init__(self):
        self.graph_input = None

    def invoke(self, graph_input, config=None):
        self.graph_input = graph_input
        return {
            **graph_input,
            "answer": "제한된 테스트 답변",
            "route": "search",
            "messages": [],
        }


class FakeConversationHistoryCollection:
    def __init__(self, doc=None):
        self.doc = doc
        self.update_filter = None
        self.update_payload = None
        self.upsert = None

    async def find_one(self, query):
        return self.doc

    async def update_one(self, query, payload, upsert=False):
        self.update_filter = query
        self.update_payload = payload
        self.upsert = upsert


def _agent_result(**overrides):
    result = {
        "answer": "테스트 답변",
        "tool_calls": [],
        "tool_results": [],
        "reranked_results": [],
        "trace": [],
        "standalone_question": "YIELD팀 수율 이슈",
        "search_query": "YIELD팀 수율 이슈",
        "route": "search",
        "follow_up_type": "new_topic",
        "contextualized_teams": ["YIELD팀"],
        "week": ["2026-28"],
        "mail_type": "weekly_report",
    }
    result.update(overrides)
    return result


class RecordingSearchClient:
    def __init__(self):
        self.wiki_calls = []
        self.mail_calls = []
        self.technical_calls = []

    def search_wiki(self, query, **kwargs):
        self.wiki_calls.append((query, kwargs))
        return []

    def search(self, query, **kwargs):
        self.mail_calls.append((query, kwargs))
        return []

    def search_secondary(self, query, **kwargs):
        self.technical_calls.append((query, kwargs))
        return []


class EvidenceSearchClient(RecordingSearchClient):
    def search_wiki(self, query, **kwargs):
        self.wiki_calls.append((query, kwargs))
        return [
            {
                "score": 3.0,
                "text": "YIELD팀 수율 개선 요약",
                "title": "수율 Wiki",
                "team": "YIELD팀",
                "week": "2026-28",
                "summary_type": "weekly",
                "topic": "수율",
            }
        ]

    def search(self, query, **kwargs):
        self.mail_calls.append((query, kwargs))
        return [
            {
                "score": 8.0,
                "text": "YIELD팀 defect 원인과 개선",
                "team": "YIELD팀",
                "week": "2026-28",
                "mail_id": "mail-1",
                "html_path": "mail/mail-1.html",
                "part_index": 0,
                "total_parts": 1,
            },
            {
                "score": 7.0,
                "text": "YIELD팀 defect 원인과 개선",
                "team": "YIELD팀",
                "week": "2026-28",
                "mail_id": "mail-1",
                "html_path": "mail/mail-1.html",
                "part_index": 1,
                "total_parts": 2,
            },
        ]

    def search_secondary(self, query, **kwargs):
        self.technical_calls.append((query, kwargs))
        return [{"score": 2.0, "text": "defect 저감 기술 배경"}]


class RaisingLLM:
    def invoke(self, *args, **kwargs):
        raise TimeoutError("router timed out")


class StaticLLM:
    def __init__(self, content):
        self.content = content

    def invoke(self, *args, **kwargs):
        return SimpleNamespace(content=self.content)


class AgenticLLM:
    def invoke(self, messages, *args, **kwargs):
        content = messages[-1].content if hasattr(messages[-1], "content") else messages[-1]["content"]
        if "출력 형식 (반드시 이 형식으로 네 줄 출력)" in content:
            return SimpleNamespace(
                content=(
                    "route: search\n"
                    "mail_type: all\n"
                    "week: none\n"
                    "search_query: 수율 이슈"
                )
            )
        if "retrieval grade" in content.lower():
            return SimpleNamespace(content="not-json")
        return SimpleNamespace(content="확보된 근거만으로 제한적으로 답변합니다. [S1]")


class InsufficientComparisonClient(RecordingSearchClient):
    def search(self, query, **kwargs):
        self.mail_calls.append((query, kwargs))
        return [
            {
                "score": 5.0,
                "text": "PROCESS팀 수율 이슈",
                "team": "PROCESS팀",
                "week": "2026-28",
                "mail_id": f"mail-{len(self.mail_calls)}",
                "html_path": "",
                "part_index": 0,
                "total_parts": 1,
            }
        ]


class PartiallyFailingClient(EvidenceSearchClient):
    def search_wiki(self, query, **kwargs):
        self.wiki_calls.append((query, kwargs))
        raise RuntimeError("wiki unavailable")


class FilterIgnoringClient(RecordingSearchClient):
    def search(self, query, **kwargs):
        self.mail_calls.append((query, kwargs))
        return [
            {
                "score": 5.0,
                "text": "PROCESS팀 문서",
                "team": "PROCESS팀",
                "week": "2026-27",
                "mail_id": "wrong-team",
                "part_index": 0,
                "total_parts": 1,
            }
        ]


class RevisionLLM:
    def invoke(self, messages, *args, **kwargs):
        content = messages[-1].content if hasattr(messages[-1], "content") else messages[-1]["content"]
        if "출력 형식 (반드시 이 형식으로 네 줄 출력)" in content:
            return SimpleNamespace(
                content=(
                    "route: search\n"
                    "mail_type: all\n"
                    "week: none\n"
                    "search_query: YIELD팀 defect 이슈"
                )
            )
        if "Create a retrieval plan" in content:
            return SimpleNamespace(content="not-json")
        if "retrieval grade" in content.lower():
            return SimpleNamespace(content="not-json")
        if "Evaluate this RAG answer" in content:
            return SimpleNamespace(content="not-json")
        if "Revise this RAG answer" in content:
            return SimpleNamespace(
                content="YIELD팀 2026-28 defect 원인과 개선 활동이 확인됩니다 [S1]"
            )
        return SimpleNamespace(content="YIELD팀 defect는 확인됐습니다 [S9]")


class ScenarioLLM:
    def invoke(self, messages, *args, **kwargs):
        content = messages[-1].content if hasattr(messages[-1], "content") else messages[-1]["content"]
        if "출력 형식 (반드시 이 형식으로 네 줄 출력)" in content:
            question = content.rsplit("현재 질문:", 1)[-1]
            if "안녕하세요" in question:
                route = "general"
            elif "제출 현황" in question:
                route = "statistics"
            else:
                route = "search"
            return SimpleNamespace(
                content=f"route: {route}\nmail_type: all\nweek: none\nsearch_query: none"
            )
        if "Create a retrieval plan" in content or "retrieval grade" in content.lower():
            return SimpleNamespace(content="not-json")
        if "Evaluate this RAG answer" in content:
            return SimpleNamespace(content="not-json")
        if "안녕하세요" in content:
            return SimpleNamespace(content="안녕하세요. 무엇을 도와드릴까요?")
        if "제출 현황" in content:
            return SimpleNamespace(content="이번 주 제출 현황은 10개 팀입니다.")
        return SimpleNamespace(content="근거가 부족합니다.")


class CapturingOpenSearchBackend:
    def __init__(self):
        self.body = None

    def search(self, *, index, body):
        self.body = body
        return {"hits": {"hits": []}}


class RecordingStatisticsTool:
    name = "get_mail_type_summary"

    def __init__(self):
        self.args = None

    def invoke(self, args):
        self.args = args
        return "10개 팀"


class StatisticsLLM:
    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages):
        return SimpleNamespace(
            tool_calls=[
                {"name": "get_mail_type_summary", "args": {"week": "2026-29"}}
            ]
        )


def test_week_filter_is_present_in_actual_opensearch_query_body():
    client = rag_api.OpenSearchClient.__new__(rag_api.OpenSearchClient)
    client.client = CapturingOpenSearchBackend()
    client.index_name = "weekly_mail"
    client._get_embedding = lambda _query: [0.1, 0.2]

    client.search(
        "수율 이슈",
        team="YIELD팀",
        week=["2026-27", "2026-28"],
    )

    filters = client.client.body["query"]["bool"]["filter"]
    assert {"term": {"team": "YIELD팀"}} in filters
    assert {"terms": {"week": ["2026-27", "2026-28"]}} in filters


def test_explicit_api_week_overrides_statistics_tool_inference(monkeypatch):
    tool = RecordingStatisticsTool()
    monkeypatch.setattr(rag_api, "STATISTICS_TOOLS", [tool])
    monkeypatch.setattr(rag_api, "get_llm", lambda: StatisticsLLM())

    result = rag_api.statistics_node(
        {
            "question": "제출 현황을 알려줘",
            "team": None,
            "week": ["2026-28"],
            "messages": [],
            "trace": [],
        }
    )

    assert tool.args == {"week": "2026-28"}
    assert result["trace"][-1]["filters"]["weeks"] == ["2026-28"]


def test_statistics_result_is_restricted_to_explicit_team():
    direct = rag_api._filter_statistics_result_for_team(
        "count_weekly_reports_by_team",
        '{"PROCESS팀": 4, "YIELD팀": 2}',
        "YIELD팀",
    )
    summary = rag_api._filter_statistics_result_for_team(
        "get_mail_type_summary",
        '{"week":"2026-28","weekly_report":{"by_team":{"PROCESS팀":4,"YIELD팀":2},"total":6}}',
        "YIELD팀",
    )

    assert json.loads(direct) == {"YIELD팀": 2}
    assert json.loads(summary)["weekly_report"] == {
        "by_team": {"YIELD팀": 2},
        "total": 2,
    }


def test_chat_with_agent_puts_explicit_team_and_week_in_initial_state(monkeypatch):
    graph = RecordingGraph()
    monkeypatch.setattr(rag_api, "get_naive_rag_graph", lambda: graph)

    async def no_history(_conversation_id):
        return []

    monkeypatch.setattr(rag_api, "get_history", no_history)

    asyncio.run(
        rag_api.chat_with_agent(
            "주요 이슈를 알려줘",
            team="YIELD팀",
            week="2026-28",
            conversation_id="test-conversation",
        )
    )

    assert graph.graph_input["team"] == "YIELD팀"
    assert graph.graph_input["week"] == ["2026-28"]
    assert graph.graph_input["trace"] == []


def test_chat_v2_generates_uuid_and_uses_same_id_for_save(monkeypatch):
    saved = {}
    monkeypatch.setattr(rag_api, "os_client", object())

    async def fake_chat(*_args, **_kwargs):
        return _agent_result()

    async def no_full_log(**_kwargs):
        return None

    async def no_legacy_save(**_kwargs):
        return None

    async def capture_save(**kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(rag_api, "chat_with_agent", fake_chat)
    monkeypatch.setattr(rag_api, "save_full_log", no_full_log)
    monkeypatch.setattr(rag_api, "save_history", no_legacy_save)
    monkeypatch.setattr(
        rag_api, "save_conversation_turn", capture_save, raising=False
    )

    response = asyncio.run(
        rag_api.chat_v2(rag_api.ChatV2Request(user_id="user-1", message="질문"))
    )

    assert uuid.UUID(response.conversation_id).version == 4
    assert saved["conversation_id"] == response.conversation_id
    assert saved["user_id"] == "user-1"


def test_chat_v2_preserves_supplied_conversation_id(monkeypatch):
    saved = {}
    monkeypatch.setattr(rag_api, "os_client", object())

    async def fake_chat(*_args, **_kwargs):
        return _agent_result()

    async def no_full_log(**_kwargs):
        return None

    async def no_legacy_save(**_kwargs):
        return None

    async def capture_save(**kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(rag_api, "chat_with_agent", fake_chat)
    monkeypatch.setattr(rag_api, "save_full_log", no_full_log)
    monkeypatch.setattr(rag_api, "save_history", no_legacy_save)
    monkeypatch.setattr(
        rag_api, "save_conversation_turn", capture_save, raising=False
    )

    response = asyncio.run(
        rag_api.chat_v2(
            rag_api.ChatV2Request(
                user_id="user-1", message="질문", conversation_id="provided-id"
            )
        )
    )

    assert response.conversation_id == "provided-id"
    assert saved["conversation_id"] == "provided-id"


def test_chat_v2_persists_only_contexts_used_by_final_answer(monkeypatch):
    saved = {}
    monkeypatch.setattr(rag_api, "os_client", object())
    unused = {
        "document_id": "wiki:unused",
        "citation_id": "S1",
        "source_type": "wiki",
        "title": "미사용",
        "content": "후보 문서",
    }
    cited = {
        "document_id": "wiki:cited",
        "citation_id": "S2",
        "source_type": "wiki",
        "title": "사용",
        "content": "인용 문서",
    }

    async def fake_chat(*_args, **_kwargs):
        return _agent_result(
            answer="확인된 답변 [S2]",
            reranked_results=[unused, cited],
            conversation_memory=rag_api.ConversationMemory().model_dump(),
        )

    async def no_full_log(**_kwargs):
        return None

    async def capture_save(**kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(rag_api, "chat_with_agent", fake_chat)
    monkeypatch.setattr(rag_api, "save_full_log", no_full_log)
    monkeypatch.setattr(rag_api, "save_conversation_turn", capture_save)

    asyncio.run(
        rag_api.chat_v2(
            rag_api.ChatV2Request(
                user_id="user-1", message="근거 질문", conversation_id="evidence-id"
            )
        )
    )

    assert [item.document_id for item in saved["memory"].cited_evidence] == [
        "wiki:cited"
    ]


def test_chat_v2_normalizes_cited_week_before_persisting_memory(monkeypatch):
    saved = {}
    monkeypatch.setattr(rag_api, "os_client", object())
    cited = {
        "document_id": "mail:week:0",
        "citation_id": "S1",
        "source_type": "mail",
        "title": "주차 근거",
        "team": "YIELD팀",
        "week": "2026-7",
        "content": "수율 근거",
        "metadata": {"mail_id": "week-mail"},
    }

    async def fake_chat(*_args, **_kwargs):
        return _agent_result(
            answer="확인된 답변 [S1]",
            reranked_results=[cited],
            conversation_memory=rag_api.ConversationMemory().model_dump(),
        )

    async def no_full_log(**_kwargs):
        return None

    async def capture_save(**kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(rag_api, "chat_with_agent", fake_chat)
    monkeypatch.setattr(rag_api, "save_full_log", no_full_log)
    monkeypatch.setattr(rag_api, "save_conversation_turn", capture_save)

    response = asyncio.run(
        rag_api.chat_v2(
            rag_api.ChatV2Request(
                user_id="user-1", message="주차 질문", conversation_id="week-id"
            )
        )
    )

    assert response.conversation_id == "week-id"
    assert saved["memory"].cited_evidence[0].week == "2026-07"


def test_invalid_cited_week_is_omitted_from_conversation_memory():
    memory = rag_api.build_next_conversation_memory(
        _agent_result(
            answer="답변 [S1]",
            used_contexts=[
                {
                    "document_id": "mail:invalid-week:0",
                    "citation_id": "S1",
                    "source_type": "mail",
                    "week": "not-a-week",
                    "content": "근거",
                }
            ],
        ),
        rag_api.ConversationMemory(),
    )

    assert memory.cited_evidence[0].week is None


def test_chat_v2_memory_build_failure_is_nonfatal_and_saves_previous_memory(
    monkeypatch,
):
    saved = {}
    previous = rag_api.ConversationMemory(active_topic="이전 주제")
    monkeypatch.setattr(rag_api, "os_client", object())

    async def fake_chat(*_args, **_kwargs):
        return _agent_result(conversation_memory=previous.model_dump())

    async def no_full_log(**_kwargs):
        return None

    def fail_memory_build(*_args, **_kwargs):
        raise ValueError("invalid cited metadata")

    async def capture_save(**kwargs):
        saved.update(kwargs)

    monkeypatch.setattr(rag_api, "chat_with_agent", fake_chat)
    monkeypatch.setattr(rag_api, "save_full_log", no_full_log)
    monkeypatch.setattr(rag_api, "build_next_conversation_memory", fail_memory_build)
    monkeypatch.setattr(rag_api, "save_conversation_turn", capture_save)

    response = asyncio.run(
        rag_api.chat_v2(rag_api.ChatV2Request(user_id="user-1", message="질문"))
    )

    assert response.answer == "테스트 답변"
    assert saved["memory"] == previous


def test_legacy_conversation_document_loads_empty_memory(monkeypatch):
    collection = FakeConversationHistoryCollection(
        {"conversation_id": "legacy", "messages": [{"role": "user", "content": "안녕"}]}
    )
    monkeypatch.setattr(
        rag_api,
        "mongo_db",
        SimpleNamespace(conversation_history=collection),
    )

    messages, memory, available = asyncio.run(
        rag_api.load_conversation_state("legacy")
    )

    assert messages == [{"role": "user", "content": "안녕"}]
    assert memory == rag_api.ConversationMemory()
    assert available is True


def test_chat_with_agent_injects_stored_conversation_memory(monkeypatch):
    graph = RecordingGraph()
    stored_memory = rag_api.ConversationMemory(
        active_topic="수율 이슈",
        teams=["YIELD팀"],
        weeks=["2026-28"],
        route="search",
    )

    async def load_once(_conversation_id):
        return [], stored_memory, True

    monkeypatch.setattr(rag_api, "get_naive_rag_graph", lambda: graph)
    monkeypatch.setattr(
        rag_api, "load_conversation_state", load_once, raising=False
    )

    asyncio.run(
        rag_api.chat_with_agent("더 자세히", conversation_id="conversation-1")
    )

    assert graph.graph_input["conversation_memory"] == stored_memory.model_dump()


def test_save_conversation_turn_sets_bounded_memory_and_user(monkeypatch):
    collection = FakeConversationHistoryCollection()
    monkeypatch.setattr(
        rag_api,
        "mongo_db",
        SimpleNamespace(conversation_history=collection),
    )
    memory = rag_api.ConversationMemory(
        active_topic="수율",
        teams=["YIELD팀"],
        weeks=["2026-28"],
    )

    asyncio.run(
        rag_api.save_conversation_turn(
            conversation_id="conversation-1",
            user_id="user-1",
            user_message="질문",
            assistant_answer="답변",
            memory=memory,
        )
    )

    assert collection.update_filter == {"conversation_id": "conversation-1"}
    assert collection.upsert is True
    assert collection.update_payload["$set"]["user_id"] == "user-1"
    assert collection.update_payload["$set"]["memory"] == memory.model_dump(mode="json")
    assert collection.update_payload["$set"]["schema_version"] == 1
    assert collection.update_payload["$push"]["messages"]["$slice"] == -20


def test_api_filters_are_persisted_for_the_next_request(monkeypatch):
    first_memory = rag_api.build_next_conversation_memory(
        _agent_result(
            contextualized_teams=["PROCESS팀"],
            week=["2026-27"],
        ),
        rag_api.ConversationMemory(),
    )
    graph = RecordingGraph()

    async def load_saved(_conversation_id):
        return [], first_memory, True

    monkeypatch.setattr(rag_api, "get_naive_rag_graph", lambda: graph)
    monkeypatch.setattr(
        rag_api, "load_conversation_state", load_saved, raising=False
    )

    asyncio.run(
        rag_api.chat_with_agent("더 자세히", conversation_id="conversation-1")
    )

    assert graph.graph_input["conversation_memory"]["teams"] == ["PROCESS팀"]
    assert graph.graph_input["conversation_memory"]["weeks"] == ["2026-27"]


def test_clarification_preserves_previous_conversation_memory():
    previous = rag_api.ConversationMemory(
        active_topic="수율 이슈",
        standalone_question="YIELD팀 수율 이슈",
        search_query="YIELD팀 수율",
        route="search",
        follow_up_type="refine",
        teams=["YIELD팀"],
        weeks=["2026-28"],
        mail_type="weekly_report",
        rolling_summary="이전 요약",
        cited_evidence=[
            {
                "document_id": "mail-1",
                "source_type": "mail",
                "title": "근거",
                "team": "YIELD팀",
                "week": "2026-28",
                "snippet": "수율 근거",
            }
        ],
    )

    memory = rag_api.build_next_conversation_memory(
        {
            "answer": "어느 팀인지 알려주세요.",
            "route": "clarify",
            "follow_up_type": "clarify",
            "standalone_question": "그 팀은?",
            "search_query": "그 팀은?",
            "contextualized_teams": [],
            "week": None,
            "mail_type": None,
        },
        previous,
    )

    assert memory.model_copy(update={"follow_up_type": "refine"}) == previous
    assert memory.follow_up_type == "clarify"


def test_follow_up_rolling_summary_retains_previous_and_current_and_is_bounded():
    previous = rag_api.ConversationMemory(rolling_summary="P" * 1900)

    memory = rag_api.build_next_conversation_memory(
        _agent_result(
            follow_up_type="refine",
            standalone_question="현재 독립 질문",
            answer="A" * 500,
        ),
        previous,
    )

    assert len(memory.rolling_summary) <= 2000
    assert "P" in memory.rolling_summary
    assert "현재 독립 질문" in memory.rolling_summary
    assert "A" * 100 in memory.rolling_summary


def test_new_topic_rolling_summary_resets_to_current_turn():
    previous = rag_api.ConversationMemory(rolling_summary="이전 주제 요약")

    memory = rag_api.build_next_conversation_memory(
        _agent_result(
            follow_up_type="new_topic",
            standalone_question="새 질문",
            answer="새 답변",
        ),
        previous,
    )

    assert memory.rolling_summary == "새 질문\n새 답변"
    assert "이전 주제" not in memory.rolling_summary


def test_conversation_memory_persists_only_documents_cited_in_answer():
    cited = {
        "document_id": "mail:used:0",
        "citation_id": "S2",
        "source_type": "mail",
        "title": "사용 근거",
        "team": "YIELD팀",
        "week": "2026-28",
        "content": "실제로 인용된 내용",
    }
    unused = {
        "document_id": "mail:unused:0",
        "citation_id": "S1",
        "source_type": "mail",
        "title": "미사용 후보",
        "content": "검색됐지만 인용되지 않은 내용",
    }

    memory = rag_api.build_next_conversation_memory(
        _agent_result(
            answer="답변 [S2]",
            reranked_results=[unused, cited],
            used_contexts=[cited],
        ),
        rag_api.ConversationMemory(),
    )

    assert [item.document_id for item in memory.cited_evidence] == ["mail:used:0"]
    assert memory.cited_evidence[0].snippet == "실제로 인용된 내용"


def test_follow_up_without_new_citations_retains_previous_evidence():
    previous = rag_api.ConversationMemory(
        cited_evidence=[
            {
                "document_id": "mail:prior:0",
                "source_type": "mail",
                "snippet": "이전 인용 근거",
            }
        ]
    )

    memory = rag_api.build_next_conversation_memory(
        _agent_result(
            follow_up_type="continue",
            answer="새 인용이 없는 후속 답변",
            used_contexts=[],
        ),
        previous,
    )

    assert memory.cited_evidence == previous.cited_evidence


def test_evidence_follow_up_restores_bounded_documents_with_fresh_citations():
    memory = rag_api.ConversationMemory(
        cited_evidence=[
            {
                "document_id": "mail:used:0",
                "source_type": "mail",
                "title": "이전 근거",
                "team": "YIELD팀",
                "week": "2026-28",
                "snippet": "이전에 검증된 수율 근거",
                "citation_id": "S9",
            }
        ]
    )

    restored = rag_api.restore_prior_evidence_node(
        {
            "question": "그 수치의 근거를 보여줘",
            "follow_up_type": "evidence",
            "conversation_memory": memory.model_dump(),
            "trace": [],
        }
    )

    assert restored["reranked_results"][0]["document_id"] == "mail:used:0"
    assert restored["reranked_results"][0]["citation_id"] == "S1"
    assert "[S1] 이전 근거" in restored["context"]
    assert "이전에 검증된 수율 근거" in restored["context"]
    assert rag_api.validate_citations("근거 [S1]", restored["reranked_results"])[0]
    assert not rag_api.validate_citations("근거 [S9]", restored["reranked_results"])[0]


def test_cited_mail_source_metadata_roundtrips_through_memory_restore():
    document = {
        "document_id": "mail:mail-7:2",
        "citation_id": "S1",
        "source_type": "mail",
        "title": "메일 근거",
        "team": "YIELD팀",
        "week": "2026-28",
        "content": "수율 근거",
        "metadata": {
            "mail_id": "mail-7",
            "html_path": "mail/mail-7.html",
            "part_index": 2,
            "total_parts": 3,
        },
    }
    memory = rag_api.build_next_conversation_memory(
        _agent_result(answer="답변 [S1]", used_contexts=[document]),
        rag_api.ConversationMemory(),
    )

    restored = rag_api.restore_prior_evidence_node(
        {
            "question": "그 근거를 보여줘",
            "follow_up_type": "evidence",
            "conversation_memory": memory.model_dump(),
            "trace": [],
        }
    )

    snapshot = memory.cited_evidence[0]
    restored_document = restored["reranked_results"][0]
    assert snapshot.mail_id == "mail-7"
    assert snapshot.html_path == "mail/mail-7.html"
    assert snapshot.part_index == 2
    assert snapshot.total_parts == 3
    assert restored_document["metadata"]["mail_id"] == "mail-7"
    assert restored_document["metadata"]["html_path"] == "mail/mail-7.html"
    assert restored_document["original_score"] == 0.0
    assert restored_document["rerank_score"] == 0.0
    assert restored["route"] == "search"


def test_restored_mail_without_mail_id_does_not_create_external_reference():
    restored = rag_api.restore_prior_evidence_node(
        {
            "question": "근거",
            "follow_up_type": "evidence",
            "conversation_memory": rag_api.ConversationMemory(
                cited_evidence=[
                    {
                        "document_id": "mail:legacy:0",
                        "source_type": "mail",
                        "snippet": "레거시 근거",
                    }
                ]
            ).model_dump(),
            "trace": [],
        }
    )

    assert rag_api.extract_references(restored["reranked_results"]) == []


def test_extract_references_coalesces_none_score_to_zero():
    references = rag_api.extract_references(
        [
            {
                "document_id": "mail:mail-8:0",
                "source_type": "mail",
                "team": "YIELD팀",
                "week": "2026-28",
                "rerank_score": None,
                "original_score": None,
                "metadata": {"mail_id": "mail-8"},
            }
        ]
    )

    assert references[0].score == 0.0


def test_chat_v2_returns_reference_for_restored_mail_snapshot(monkeypatch):
    monkeypatch.setattr(rag_api, "os_client", object())
    memory = rag_api.ConversationMemory(
        cited_evidence=[
            {
                "document_id": "mail:mail-9:0",
                "source_type": "mail",
                "title": "복원 메일",
                "team": "YIELD팀",
                "week": "2026-28",
                "snippet": "복원된 근거",
                "mail_id": "mail-9",
                "html_path": "mail/mail-9.html",
                "part_index": 0,
                "total_parts": 1,
            }
        ]
    )
    restored = rag_api.restore_prior_evidence_node(
        {
            "question": "그 근거를 보여줘",
            "follow_up_type": "evidence",
            "conversation_memory": memory.model_dump(),
            "trace": [],
        }
    )

    async def fake_chat(*_args, **_kwargs):
        return _agent_result(
            answer="복원 근거입니다 [S1]",
            follow_up_type="evidence",
            conversation_memory=memory.model_dump(),
            reranked_results=restored["reranked_results"],
            route=restored["route"],
        )

    async def no_write(**_kwargs):
        return None

    monkeypatch.setattr(rag_api, "chat_with_agent", fake_chat)
    monkeypatch.setattr(rag_api, "save_full_log", no_write)
    monkeypatch.setattr(rag_api, "save_conversation_turn", no_write)

    response = asyncio.run(
        rag_api.chat_v2(
            rag_api.ChatV2Request(
                user_id="user-1",
                message="그 근거를 보여줘",
                conversation_id="restored-mail",
            )
        )
    )

    assert len(response.references) == 1
    assert response.references[0].mail_id == "mail-9"
    assert response.references[0].score == 0.0


def test_evidence_follow_up_routes_to_restore_only_when_snapshots_exist():
    with_evidence = {
        "follow_up_type": "evidence",
        "conversation_memory": rag_api.ConversationMemory(
            cited_evidence=[
                {
                    "document_id": "mail:used:0",
                    "source_type": "mail",
                    "snippet": "근거",
                }
            ]
        ).model_dump(),
    }
    without_evidence = {
        "follow_up_type": "evidence",
        "conversation_memory": rag_api.ConversationMemory().model_dump(),
    }

    assert rag_api.route_after_contextualization(with_evidence) == "restore_prior_evidence"
    assert rag_api.route_after_contextualization(without_evidence) == "router"


def test_graph_evidence_path_bypasses_retrieval_planning(monkeypatch):
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    graph = rag_api.get_naive_rag_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert "restore_prior_evidence" in graph.nodes
    assert ("contextualize_turn", "restore_prior_evidence") in edges
    assert ("restore_prior_evidence", "llm_answer") in edges
    assert ("restore_prior_evidence", "plan_retrieval") not in edges


def test_init_mongodb_degrades_when_client_creation_fails(monkeypatch):
    def fail_client(*_args, **_kwargs):
        raise OSError("mongo unavailable")

    monkeypatch.setattr(rag_api, "AsyncIOMotorClient", fail_client)
    monkeypatch.setattr(rag_api, "mongo_client", None)
    monkeypatch.setattr(rag_api, "mongo_db", object())

    asyncio.run(rag_api.init_mongodb())

    assert rag_api.mongo_client is None
    assert rag_api.mongo_db is None


def test_init_mongodb_requests_ttl_update_for_existing_index(monkeypatch):
    class FakeCollection:
        async def create_index(self, *_args, **_kwargs):
            return "updated_at_1"

        async def index_information(self):
            return {
                "updated_at_1": {
                    "key": [("updated_at", 1)],
                    "expireAfterSeconds": 60,
                }
            }

    class FakeDatabase:
        def __init__(self):
            self.conversation_logs = FakeCollection()
            self.conversation_history = FakeCollection()
            self.commands = []

        async def command(self, command):
            self.commands.append(command)

    class FakeClient:
        def __init__(self):
            self.database = FakeDatabase()

        def __getitem__(self, _name):
            return self.database

    client = FakeClient()
    monkeypatch.setattr(rag_api, "AsyncIOMotorClient", lambda *_a, **_k: client)
    monkeypatch.setattr(rag_api, "mongo_client", None)
    monkeypatch.setattr(rag_api, "mongo_db", None)

    asyncio.run(rag_api.init_mongodb())

    assert client.database.commands == [
        {
            "collMod": "conversation_history",
            "index": {
                "name": "updated_at_1",
                "expireAfterSeconds": rag_api.HISTORY_TTL_SECONDS,
            },
        }
    ]


@pytest.mark.parametrize("failed_write", ["full_log", "conversation_turn"])
def test_chat_v2_write_failure_is_nonfatal_and_other_write_is_attempted(
    monkeypatch, failed_write
):
    attempts = []
    monkeypatch.setattr(rag_api, "os_client", object())

    async def fake_chat(*_args, **_kwargs):
        return _agent_result()

    async def full_log(**_kwargs):
        attempts.append("full_log")
        if failed_write == "full_log":
            raise OSError("log write failed")

    async def conversation_turn(**_kwargs):
        attempts.append("conversation_turn")
        if failed_write == "conversation_turn":
            raise OSError("turn write failed")

    monkeypatch.setattr(rag_api, "chat_with_agent", fake_chat)
    monkeypatch.setattr(rag_api, "save_full_log", full_log)
    monkeypatch.setattr(rag_api, "save_conversation_turn", conversation_turn)

    response = asyncio.run(
        rag_api.chat_v2(rag_api.ChatV2Request(user_id="user-1", message="질문"))
    )

    assert response.answer == "테스트 답변"
    assert attempts == ["full_log", "conversation_turn"]


def test_streamlit_uses_server_returned_conversation_id(monkeypatch):
    fake_streamlit = SimpleNamespace(
        session_state=SimpleNamespace(conversation_id="local-id")
    )
    monkeypatch.setattr(streamlit_chat, "st", fake_streamlit)

    streamlit_chat.retain_server_conversation_id(
        {"conversation_id": "server-id", "answer": "답변"}
    )

    assert fake_streamlit.session_state.conversation_id == "server-id"


def test_contextualized_standalone_question_reaches_retrieval_planner(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: StaticLLM("not-json"))

    result = rag_api.plan_retrieval_node(
        {
            "question": "그 팀은?",
            "standalone_question": "YIELD팀 수율 이슈를 알려줘",
            "search_query": "YIELD팀 수율 이슈",
            "contextualized_teams": ["YIELD팀"],
            "team": "YIELD팀",
            "week": ["2026-28"],
            "messages": [],
            "trace": [],
        }
    )

    assert result["sub_questions"] == ["YIELD팀 수율 이슈를 알려줘"]
    assert "그 팀" not in result["sub_questions"][0]
    assert result["retrieval_plan"]["filters"]["allowed_teams"] == ["YIELD팀"]


def test_contextualize_turn_explicit_api_filters_override_memory(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())
    memory = rag_api.ConversationMemory(
        active_topic="수율 이슈",
        teams=["PROCESS팀"],
        weeks=["2026-27"],
        mail_type="daily_report",
    )

    result = rag_api.contextualize_turn_node(
        {
            "question": "YIELD팀 2026-29 주간보고도 비교해줘",
            "team": "QA팀",
            "week": ["2026-30"],
            "mail_type": "weekly_report",
            "conversation_memory": memory.model_dump(),
            "messages": [],
            "trace": [],
        }
    )

    assert result["team"] == "QA팀"
    assert result["week"] == ["2026-30"]
    assert result["mail_type"] == "weekly_report"


def test_contextualize_turn_inherits_filters_for_resolvable_follow_up(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())
    memory = rag_api.ConversationMemory(
        active_topic="수율 이슈",
        standalone_question="YIELD팀 수율 이슈",
        search_query="YIELD팀 수율 이슈",
        route="search",
        teams=["YIELD팀"],
        weeks=["2026-28"],
        mail_type="weekly_report",
    )

    result = rag_api.contextualize_turn_node(
        {
            "question": "더 자세히 알려줘",
            "team": None,
            "week": None,
            "mail_type": None,
            "conversation_memory": memory.model_dump(),
            "messages": [],
            "trace": [],
        }
    )

    assert result["follow_up_type"] == "refine"
    assert result["standalone_question"] == "YIELD팀 수율 이슈"
    assert result["team"] == "YIELD팀"
    assert result["week"] == ["2026-28"]
    assert result["mail_type"] == "weekly_report"


def test_compare_follow_up_unions_prior_and_explicit_teams(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())
    memory = rag_api.ConversationMemory(
        active_topic="수율 이슈",
        standalone_question="PROCESS팀 수율 이슈",
        search_query="PROCESS팀 수율 이슈",
        route="search",
        teams=["PROCESS팀"],
        weeks=["2026-28"],
    )

    result = rag_api.contextualize_turn_node(
        {
            "question": "YIELD팀과 비교해줘",
            "team": None,
            "week": None,
            "mail_type": None,
            "conversation_memory": memory.model_dump(),
            "messages": [],
            "trace": [],
        }
    )

    assert result["follow_up_type"] == "compare"
    assert result["contextualized_teams"] == ["PROCESS팀", "YIELD팀"]
    assert result["team"] is None
    assert "PROCESS팀" in result["standalone_question"]
    assert "YIELD팀" in result["standalone_question"]


def test_compare_follow_up_planner_builds_tasks_for_both_teams(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())
    memory = rag_api.ConversationMemory(
        standalone_question="PROCESS팀 수율 이슈",
        search_query="PROCESS팀 수율 이슈",
        route="search",
        teams=["PROCESS팀"],
        weeks=["2026-28"],
    )
    contextualized = rag_api.contextualize_turn_node(
        {
            "question": "YIELD팀과 비교해줘",
            "team": None,
            "week": None,
            "mail_type": None,
            "conversation_memory": memory.model_dump(),
            "messages": [],
            "trace": [],
        }
    )
    monkeypatch.setattr(rag_api, "get_llm", lambda: StaticLLM("not-json"))

    planned = rag_api.plan_retrieval_node(
        {
            "question": "YIELD팀과 비교해줘",
            **contextualized,
        }
    )
    tasks = rag_api.build_search_tasks(
        rag_api.RetrievalPlan.model_validate(planned["retrieval_plan"])
    )

    assert planned["retrieval_plan"]["filters"]["allowed_teams"] == [
        "PROCESS팀",
        "YIELD팀",
    ]
    assert {task.team for task in tasks} == {"PROCESS팀", "YIELD팀"}


def test_contextualize_turn_new_topic_clears_memory_filters(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())
    memory = rag_api.ConversationMemory(
        active_topic="수율 이슈",
        teams=["YIELD팀"],
        weeks=["2026-28"],
        mail_type="weekly_report",
    )

    result = rag_api.contextualize_turn_node(
        {
            "question": "ALD 공정 원리를 알려줘",
            "team": None,
            "week": None,
            "mail_type": None,
            "conversation_memory": memory.model_dump(),
            "messages": [],
            "trace": [],
        }
    )

    assert result["follow_up_type"] == "new_topic"
    assert result["team"] is None
    assert result["week"] is None
    assert result["mail_type"] is None


def test_contextualize_turn_unresolved_pronoun_requests_clarification(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())

    result = rag_api.contextualize_turn_node(
        {
            "question": "그 팀은?",
            "team": None,
            "week": None,
            "mail_type": None,
            "conversation_memory": {},
            "messages": [],
            "trace": [],
        }
    )

    assert result["follow_up_type"] == "clarify"
    assert result["route"] == "clarify"
    assert "팀" in result["answer"]


@pytest.mark.parametrize("invalid_week", ["2026-00", "2026-54"])
def test_normalize_weeks_rejects_out_of_range_week(invalid_week):
    assert rag_api.normalize_weeks(invalid_week) is None


def test_contextualize_turn_revalidates_resolved_updates(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())

    with pytest.raises(ValidationError):
        rag_api.contextualize_turn_node(
            {
                "question": "YIELD팀 수율 이슈",
                "team": None,
                "week": None,
                "mail_type": "invalid_mail_type",
                "conversation_memory": {},
                "messages": [],
                "trace": [],
            }
        )


def test_greeting_history_does_not_resolve_team_pronoun(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())

    result = rag_api.contextualize_turn_node(
        {
            "question": "그 팀은?",
            "team": None,
            "week": None,
            "mail_type": None,
            "conversation_memory": {},
            "messages": [
                rag_api.HumanMessage(content="안녕하세요"),
                rag_api.AIMessage(content="안녕하세요. 무엇을 도와드릴까요?"),
            ],
            "trace": [],
        }
    )

    assert result["follow_up_type"] == "clarify"
    assert result["route"] == "clarify"


def test_graph_starts_with_contextualizer_and_can_finalize_clarification(monkeypatch):
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    graph = rag_api.get_naive_rag_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert "contextualize_turn" in graph.nodes
    assert ("__start__", "contextualize_turn") in edges
    assert ("contextualize_turn", "router") in edges
    assert ("contextualize_turn", "finalize") in edges
    assert ("__start__", "router") not in edges


def test_retrieve_document_passes_team_and_week_to_filterable_sources(monkeypatch):
    client = RecordingSearchClient()
    monkeypatch.setattr(rag_api, "os_client", client)

    result = rag_api.retrieve_document(
        {
            "question": "수율 이슈",
            "search_query": "수율 이슈",
            "team": "YIELD팀",
            "week": ["2026-27", "2026-28"],
            "messages": [],
            "trace": [],
        }
    )

    assert client.wiki_calls[0][1]["team"] == "YIELD팀"
    assert client.wiki_calls[0][1]["week"] == ["2026-27", "2026-28"]
    assert client.mail_calls[0][1]["team"] == "YIELD팀"
    assert client.mail_calls[0][1]["week"] == ["2026-27", "2026-28"]
    assert result["retrieval_error"] == "no_relevant_documents"
    assert result["trace"][-1]["filters"] == {
        "team": "YIELD팀",
        "weeks": ["2026-27", "2026-28"],
    }


def test_retrieve_document_uses_normalized_deduplicated_reranked_context(monkeypatch):
    client = EvidenceSearchClient()
    monkeypatch.setattr(rag_api, "os_client", client)

    result = rag_api.retrieve_document(
        {
            "question": "YIELD팀 수율 defect 개선",
            "search_query": "YIELD팀 수율 defect 개선",
            "team": "YIELD팀",
            "week": ["2026-28"],
            "messages": [],
            "trace": [],
        }
    )

    assert len(result["retrieval_results"]) == 3
    assert len(result["reranked_results"]) == 3
    assert result["reranked_results"][0]["citation_id"] == "S1"
    assert "[S1]" in result["context"]
    assert result["trace"][-1]["duplicates_removed"] == 1
    assert result["trace"][-1]["selected_document_ids"] == [
        document["document_id"] for document in result["reranked_results"]
    ]
    assert result["trace"][-1]["ranking_before"]
    assert result["trace"][-1]["ranking_after"] == [
        {
            "document_id": document["document_id"],
            "rerank_score": document["rerank_score"],
        }
        for document in result["reranked_results"]
    ]


def test_router_failure_uses_conservative_rule_fallback(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())

    search_result = rag_api.router_node(
        {"question": "YIELD팀 수율 이슈", "messages": [], "trace": []}
    )
    statistics_result = rag_api.router_node(
        {"question": "이번 주 미제출 팀은 몇 개야?", "messages": [], "trace": []}
    )
    general_result = rag_api.router_node(
        {"question": "안녕하세요", "messages": [], "trace": []}
    )

    assert search_result["route"] == "search"
    assert statistics_result["route"] == "statistics"
    assert general_result["route"] == "general"
    assert search_result["trace"][-1]["fallback_reason"] == "router timed out"


def test_explicit_api_week_filter_wins_over_router_inference(monkeypatch):
    monkeypatch.setattr(
        rag_api,
        "get_llm",
        lambda: StaticLLM(
            "route: search\n"
            "mail_type: all\n"
            "week: 2026-29\n"
            "search_query: 수율 이슈"
        ),
    )

    result = rag_api.router_node(
        {
            "question": "다음 주 수율 이슈",
            "team": "YIELD팀",
            "week": ["2026-28"],
            "messages": [],
            "trace": [],
        }
    )

    assert result["week"] == ["2026-28"]


def test_malformed_router_output_defaults_to_search(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: StaticLLM("route: unknown"))

    result = rag_api.router_node(
        {"question": "설명해줘", "messages": [], "trace": []}
    )

    assert result["route"] == "search"
    assert result["trace"][-1]["fallback_reason"] == "invalid_router_output"


def test_successful_llm_cannot_misroute_domain_or_statistics_as_general(monkeypatch):
    monkeypatch.setattr(
        rag_api,
        "get_llm",
        lambda: StaticLLM(
            "route: general\nmail_type: all\nweek: none\nsearch_query: none"
        ),
    )

    domain = rag_api.router_node(
        {"question": "ALD 공정 원인을 알려줘", "messages": [], "trace": []}
    )
    statistics = rag_api.router_node(
        {"question": "이번 주 제출 현황을 알려줘", "messages": [], "trace": []}
    )

    assert domain["route"] == "search"
    assert statistics["route"] == "statistics"
    assert domain["trace"][-1]["fallback_reason"] == "rule_route_guard"


@pytest.mark.parametrize("week", ["not-a-week", "2026-00", "2026-54"])
def test_chat_request_rejects_invalid_explicit_week(week):
    with pytest.raises(ValidationError):
        rag_api.ChatV2Request(user_id="u1", message="이슈", week=week)


def test_missing_search_client_is_reported_as_retrieval_failure(monkeypatch):
    monkeypatch.setattr(rag_api, "os_client", None)

    result = rag_api.retrieve_document(
        {
            "question": "수율 이슈",
            "search_query": "수율 이슈",
            "team": "YIELD팀",
            "week": ["2026-28"],
            "messages": [],
            "trace": [],
        }
    )

    assert result["retrieval_error"] == "search_unavailable"
    assert result["trace"][-1]["error"] == "search_unavailable"


def test_search_failure_returns_grounded_limitation_without_calling_llm(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: RaisingLLM())

    result = rag_api.llm_answer_node(
        {
            "question": "존재하지 않는 장비의 defect 수치는?",
            "route": "search",
            "context": "",
            "retrieval_error": "search_failed",
            "messages": [],
            "trace": [],
        }
    )

    assert "검색" in result["answer"]
    assert "근거" in result["answer"]
    assert result["trace"][-1]["event"] == "answer_limited"


def test_graph_retries_insufficient_retrieval_once_and_terminates(monkeypatch):
    client = InsufficientComparisonClient()
    monkeypatch.setattr(rag_api, "os_client", client)
    monkeypatch.setattr(rag_api, "get_llm", lambda: AgenticLLM())
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    graph = rag_api.get_naive_rag_graph()
    result = graph.invoke(
        {
            "question": "PROCESS팀과 YIELD팀 수율 이슈를 비교해줘",
            "search_query": "",
            "context": "",
            "messages": [],
            "route": "",
            "team": None,
            "week": ["2026-28"],
            "mail_type": None,
            "retrieval_error": None,
            "retrieval_results": [],
            "reranked_results": [],
            "search_attempts": 0,
            "rewrite_count": 0,
            "executed_search_keys": [],
            "trace": [],
        }
    )

    assert result["search_attempts"] == 2
    assert result["rewrite_count"] == 1
    assert result["retrieval_grade"] == "insufficient"
    assert any("YIELD팀" in item for item in result["missing_information"])
    assert len(client.mail_calls) == 3
    assert [call[0] for call in client.mail_calls[:2]] == [
        "PROCESS팀 수율 이슈",
        "YIELD팀 수율 이슈",
    ]
    assert client.mail_calls[2][0] == "수율 이슈 YIELD팀"
    assert "제한적으로" in result["answer"]


def test_plan_retrieval_node_uses_bounded_fallback_plan(monkeypatch):
    monkeypatch.setattr(rag_api, "get_llm", lambda: StaticLLM("not-json"))

    result = rag_api.plan_retrieval_node(
        {
            "question": "PROCESS팀과 YIELD팀 수율 이슈를 비교해줘",
            "team": None,
            "week": ["2026-27", "2026-28"],
            "messages": [],
            "trace": [],
        }
    )

    assert result["retrieval_plan"]["intent"] == "comparison"
    assert result["selected_sources"] == ["mail", "wiki"]
    assert result["sub_questions"] == [
        "PROCESS팀 수율 이슈",
        "YIELD팀 수율 이슈",
    ]
    assert result["trace"][-1]["planner_mode"] == "rule_fallback"


def test_llm_generic_comparison_plan_is_constrained_to_question_teams(monkeypatch):
    monkeypatch.setattr(
        rag_api,
        "get_llm",
        lambda: StaticLLM(
            '{"intent":"comparison","sources":["mail","wiki"],'
            '"sub_questions":["두 팀 이슈 비교"],"filters":{},'
            '"search_strategy":"hybrid","vector_weight":0.7,'
            '"keyword_weight":0.3}'
        ),
    )

    result = rag_api.plan_retrieval_node(
        {
            "question": "PROCESS팀과 YIELD팀의 이슈를 비교해줘",
            "team": None,
            "week": ["2026-28"],
            "trace": [],
        }
    )
    tasks = rag_api.build_search_tasks(
        rag_api.RetrievalPlan.model_validate(result["retrieval_plan"])
    )

    assert result["retrieval_plan"]["filters"]["allowed_teams"] == [
        "PROCESS팀",
        "YIELD팀",
    ]
    assert tasks
    assert {task.team for task in tasks} == {"PROCESS팀", "YIELD팀"}


def test_execute_searches_calls_only_selected_source_and_preserves_task_metadata(monkeypatch):
    client = EvidenceSearchClient()
    monkeypatch.setattr(rag_api, "os_client", client)

    result = rag_api.execute_searches_node(
        {
            "question": "ALD 공정 원리가 뭐야",
            "search_query": "ALD 공정 원리",
            "team": None,
            "week": None,
            "mail_type": None,
            "retrieval_plan": {
                "intent": "single_search",
                "sources": ["technical_document"],
                "sub_questions": ["ALD 공정 원리가 뭐야"],
                "filters": {"team": None, "weeks": None},
                "search_strategy": "semantic",
                "vector_weight": 0.8,
                "keyword_weight": 0.2,
            },
            "retrieval_results": [],
            "reranked_results": [],
            "search_attempts": 0,
            "executed_search_keys": [],
            "trace": [],
        }
    )

    assert client.mail_calls == []
    assert client.wiki_calls == []
    assert len(client.technical_calls) == 1
    assert client.technical_calls[0][1]["vector_weight"] == 0.8
    assert result["retrieval_results"][0]["source_type"] == "technical_document"
    assert result["retrieval_results"][0]["metadata"]["sub_question"] == "ALD 공정 원리가 뭐야"


def test_execute_searches_keeps_partial_results_when_one_source_fails(monkeypatch):
    client = PartiallyFailingClient()
    monkeypatch.setattr(rag_api, "os_client", client)

    result = rag_api.execute_searches_node(
        {
            "question": "YIELD팀 수율 이슈",
            "search_query": "YIELD팀 수율 이슈",
            "team": "YIELD팀",
            "week": ["2026-28"],
            "mail_type": None,
            "retrieval_plan": {
                "intent": "single_search",
                "sources": ["mail", "wiki"],
                "sub_questions": ["YIELD팀 수율 이슈"],
                "filters": {"team": "YIELD팀", "weeks": ["2026-28"]},
                "search_strategy": "hybrid",
                "vector_weight": 0.7,
                "keyword_weight": 0.3,
            },
            "retrieval_results": [],
            "reranked_results": [],
            "search_attempts": 0,
            "executed_search_keys": [],
            "trace": [],
        }
    )

    assert result["retrieval_results"]
    assert all(doc["source_type"] == "mail" for doc in result["retrieval_results"])
    assert result["trace"][-1]["search_errors"][0]["source"] == "wiki"
    assert result["retrieval_error"] is None


def test_execute_searches_drops_results_outside_explicit_team_and_week(monkeypatch):
    client = FilterIgnoringClient()
    monkeypatch.setattr(rag_api, "os_client", client)

    result = rag_api.execute_searches_node(
        {
            "question": "YIELD팀 이슈",
            "team": "YIELD팀",
            "week": ["2026-28"],
            "mail_type": None,
            "retrieval_plan": {
                "intent": "single_search",
                "sources": ["mail"],
                "sub_questions": ["YIELD팀 이슈"],
                "filters": {"team": "YIELD팀", "weeks": ["2026-28"]},
                "search_strategy": "hybrid",
                "vector_weight": 0.7,
                "keyword_weight": 0.3,
            },
            "retrieval_results": [],
            "reranked_results": [],
            "search_attempts": 0,
            "executed_search_keys": [],
            "trace": [],
        }
    )

    assert result["retrieval_results"] == []
    assert result["retrieval_error"] == "no_relevant_documents"


def test_graph_revises_invalid_citation_once_and_finalizes(monkeypatch):
    client = EvidenceSearchClient()
    monkeypatch.setattr(rag_api, "os_client", client)
    monkeypatch.setattr(rag_api, "get_llm", lambda: RevisionLLM())
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    result = rag_api.get_naive_rag_graph().invoke(
        {
            "question": "YIELD팀 defect 이슈를 알려줘",
            "search_query": "",
            "context": "",
            "messages": [],
            "route": "",
            "team": "YIELD팀",
            "week": ["2026-28"],
            "mail_type": None,
            "retrieval_error": None,
            "retrieval_results": [],
            "reranked_results": [],
            "search_attempts": 0,
            "rewrite_count": 0,
            "answer_revision_count": 0,
            "executed_search_keys": [],
            "trace": [],
        }
    )

    assert result["answer_revision_count"] == 1
    assert result["citation_valid"] is True
    assert result["answer_evaluation"]["passed"] is True
    assert result["answer"] == result["final_answer"]
    assert "[S1]" in result["final_answer"]
    assert result["route"] == "search"
    assert result["search_attempts"] == 1
    assert result["selected_sources"] == ["mail", "wiki"]
    assert result["retrieval_grade"] == "sufficient"


def test_general_scenario_finishes_without_retrieval(monkeypatch):
    client = RecordingSearchClient()
    monkeypatch.setattr(rag_api, "os_client", client)
    monkeypatch.setattr(rag_api, "get_llm", lambda: ScenarioLLM())
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    result = rag_api.get_naive_rag_graph().invoke(
        {
            "question": "안녕하세요.",
            "messages": [],
            "team": None,
            "week": None,
            "search_attempts": 0,
            "rewrite_count": 0,
            "answer_revision_count": 0,
            "selected_sources": [],
            "retrieval_grade": "",
            "trace": [],
        }
    )

    assert result["final_answer"]
    assert result["route"] == "general"
    assert result["search_attempts"] == 0
    assert result["selected_sources"] == []
    assert result["retrieval_grade"] == ""
    assert client.mail_calls == client.wiki_calls == client.technical_calls == []


def test_statistics_scenario_uses_statistics_path_without_search(monkeypatch):
    client = RecordingSearchClient()

    def fake_statistics(state):
        return {
            "context": "이번 주 제출 현황: 10개 팀",
            "messages": [],
            "trace": rag_api.append_trace(state.get("trace", []), "statistics_completed"),
        }

    monkeypatch.setattr(rag_api, "os_client", client)
    monkeypatch.setattr(rag_api, "get_llm", lambda: ScenarioLLM())
    monkeypatch.setattr(rag_api, "statistics_node", fake_statistics)
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    result = rag_api.get_naive_rag_graph().invoke(
        {
            "question": "이번 주 팀별 보고서 제출 현황을 알려줘.",
            "messages": [],
            "team": None,
            "week": ["2026-28"],
            "search_attempts": 0,
            "rewrite_count": 0,
            "answer_revision_count": 0,
            "selected_sources": [],
            "retrieval_grade": "",
            "trace": [],
        }
    )

    assert result["final_answer"]
    assert result["route"] == "statistics"
    assert result["search_attempts"] == 0
    assert result["selected_sources"] == []
    assert result["retrieval_grade"] == ""
    assert client.mail_calls == client.wiki_calls == client.technical_calls == []


def test_no_result_scenario_terminates_with_grounded_limitation(monkeypatch):
    client = RecordingSearchClient()
    monkeypatch.setattr(rag_api, "os_client", client)
    monkeypatch.setattr(rag_api, "get_llm", lambda: ScenarioLLM())
    monkeypatch.setattr(rag_api, "_naive_rag_graph", None)

    result = rag_api.get_naive_rag_graph().invoke(
        {
            "question": "존재하지 않는 ZXQ-999 장비 이슈를 알려줘",
            "messages": [],
            "team": None,
            "week": None,
            "search_attempts": 0,
            "rewrite_count": 0,
            "answer_revision_count": 0,
            "selected_sources": [],
            "retrieval_results": [],
            "reranked_results": [],
            "executed_search_keys": [],
            "trace": [],
        },
        {"recursion_limit": 15},
    )

    assert "근거" in result["final_answer"]
    assert result["route"] == "search"
    assert result["search_attempts"] == 2
    assert result["selected_sources"]
    assert result["retrieval_grade"] == "irrelevant"
    assert result["retrieval_error"] == "no_relevant_documents"
    assert result["rewrite_count"] == 1
    assert "관련 공정 원인 유사 사례" in result["rewritten_query"]


def test_irrelevant_results_expand_query_and_change_search_strategy():
    result = rag_api.rewrite_query_node(
        {
            "question": "ZXQ-999 장비 이슈",
            "search_query": "ZXQ-999 장비 이슈",
            "retrieval_grade": "irrelevant",
            "missing_information": ["질문에 답할 검색 근거 부족"],
            "search_attempts": 1,
            "rewrite_count": 0,
            "executed_search_keys": [],
            "retrieval_plan": {
                "intent": "single_search",
                "sources": ["mail", "wiki"],
                "sub_questions": ["ZXQ-999 장비 이슈"],
                "filters": {"team": None, "weeks": None},
                "search_strategy": "hybrid",
                "vector_weight": 0.35,
                "keyword_weight": 0.65,
            },
            "trace": [],
        }
    )

    assert result["rewritten_query"] == "ZXQ-999 장비 이슈 관련 공정 원인 유사 사례"
    assert result["retrieval_plan"]["search_strategy"] == "keyword"
    assert result["retrieval_plan"]["keyword_weight"] > result["retrieval_plan"]["vector_weight"]
    assert "technical_document" in result["retrieval_plan"]["sources"]


def test_insufficient_numeric_evidence_rewrites_toward_exact_values():
    result = rag_api.rewrite_query_node(
        {
            "question": "YIELD팀 defect 건수는?",
            "search_query": "YIELD팀 defect",
            "team": "YIELD팀",
            "retrieval_grade": "insufficient",
            "missing_information": ["숫자 근거 부족"],
            "search_attempts": 1,
            "rewrite_count": 0,
            "executed_search_keys": [],
            "retrieval_plan": {
                "intent": "single_search",
                "sources": ["mail", "wiki"],
                "sub_questions": ["YIELD팀 defect"],
                "filters": {"team": "YIELD팀", "weeks": None},
                "search_strategy": "hybrid",
                "vector_weight": 0.35,
                "keyword_weight": 0.65,
            },
            "trace": [],
        }
    )

    assert result["rewritten_query"] == "YIELD팀 defect 건수 수치"
    assert result["retrieval_plan"]["search_strategy"] == "keyword"
    assert result["rewrite_count"] == 1


def test_s_citations_map_back_to_existing_mail_reference_schema():
    documents = [
        {
            "document_id": "mail:mail-1:0",
            "source_type": "mail",
            "title": "YIELD팀 메일",
            "content": "defect 개선",
            "team": "YIELD팀",
            "week": "2026-28",
            "original_score": 5.0,
            "rerank_score": 1.0,
            "citation_id": "S1",
            "metadata": {
                "mail_id": "mail-1",
                "part_index": 0,
                "total_parts": 1,
            },
        },
        {
            "document_id": "wiki:2",
            "source_type": "wiki",
            "title": "Wiki",
            "content": "배경",
            "team": "YIELD팀",
            "week": "2026-28",
            "citation_id": "S2",
            "metadata": {},
        },
    ]

    clean_answer, used = rag_api.parse_used_references(
        "defect 개선입니다 [S1]. 배경입니다 [S2].", documents
    )
    references = rag_api.extract_references(used)

    assert clean_answer.startswith("defect 개선")
    assert len(references) == 1
    assert references[0].mail_id == "mail-1"
    assert references[0].part_index == 0
