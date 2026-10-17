import json
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from app.graphs.fast_agent import FastEvidenceAgent
from app.domain.quick_rag import QuickPlan, QuickConditions
from app.retrieval.quick_rag import QuickDocument, QuickSearchOutcome


@pytest.mark.asyncio
async def test_agent_searches_gap_preserves_sender_and_finishes_in_two_calls():
    seen = []

    class Retrieval:
        async def search(self, plan, policy, **kwargs):
            seen.append(plan)
            return QuickSearchOutcome(
                documents=[
                    QuickDocument(
                        source="ews_mail",
                        id="2",
                        original_id="2",
                        text="원인 분석은 진행 중이며 조치 검증은 내일 예정이다.",
                    )
                ],
                searched_sources=["ews_mail"],
            )

    replies = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_evidence",
                    "args": {"queries": ["누설 증가 원인 조치"]},
                    "id": "t1",
                }
            ],
        ),
        AIMessage(
            content=json.dumps(
                {
                    "answer": "원인 분석 중이며 검증 예정이다. [S2]",
                    "missing": "확정 원인은 미확인",
                    "evidence_ids": ["S2"],
                    "evidence_status": "partial",
                }
            )
        ),
    ]

    async def respond(messages):
        return replies.pop(0)

    runner = FastEvidenceAgent(
        None, model_factory=lambda tools, final: RunnableLambda(respond)
    )
    outcome = QuickSearchOutcome(
        documents=[
            QuickDocument(
                source="ews_mail", id="1", original_id="1", text="누설 증가 보고"
            )
        ]
    )
    calls = []
    answer, queries = await runner.run(
        "조사해줘",
        QuickPlan(
            source_scope="mail",
            conditions=QuickConditions(sender_email="kim@example.com"),
        ),
        outcome,
        Retrieval(),
        object(),
        None,
        calls,
        [],
        "Asia/Seoul",
    )
    assert answer.evidence_status == "partial"
    assert len(calls) == 2
    assert seen[0].conditions.sender_email == "kim@example.com"
    assert len(outcome.documents) == 2
    assert queries == ["누설 증가 원인 조치"]


@pytest.mark.asyncio
async def test_repeat_query_does_not_rerun_same_search_and_context_is_passed():
    from app.domain.quick_rag import QuickMemory

    class Retrieval:
        async def search(self, *args, **kwargs):
            raise AssertionError("Duplicate search must not execute")

    seen = []
    replies = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "search_evidence",
                    "args": {"queries": ["누설 원인"]},
                    "id": "t1",
                }
            ],
        ),
        AIMessage(
            content=json.dumps(
                {
                    "answer": "누설 보고가 있다. [S1]",
                    "missing": "추가 원인은 확인되지 않았다.",
                    "evidence_ids": ["S1"],
                    "evidence_status": "partial",
                }
            )
        ),
    ]

    replies.insert(
        1,
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "select_evidence",
                    "args": {"passage_ids": ["S1:P1"]},
                    "id": "select",
                }
            ],
        ),
    )

    async def respond(messages):
        seen.extend(messages)
        return replies.pop(0)

    plan = QuickPlan(query="누설", scope="follow_up")
    memory = QuickMemory(
        user_id="kim",
        plan=plan,
        search_queries=["누설 원인"],
        last_answer="누설 문제 보고",
        unresolved="원인",
    )
    outcome = QuickSearchOutcome(
        documents=[
            QuickDocument(source="ews_mail", id="1", original_id="1", text="누설 보고")
        ]
    )
    runner = FastEvidenceAgent(
        None, model_factory=lambda tools, final: RunnableLambda(respond)
    )
    result, queries = await runner.run(
        "더 조사해줘",
        plan,
        outcome,
        Retrieval(),
        object(),
        memory,
        [],
        [],
        "Asia/Seoul",
    )
    assert not queries
    assert "누설 문제 보고" in str(seen)
    assert "duplicate_queries" in str(seen)
    assert result.evidence_status == "partial"


@pytest.mark.asyncio
async def test_context_expansion_keeps_owner_and_original_parent_filters():
    from app.retrieval.quick_rag import QuickRetrieval
    from types import SimpleNamespace

    seen = []

    class Backend:
        async def search(self, index, body):
            seen.append(body)
            return {
                "hits": {
                    "hits": [
                        {
                            "_source": {
                                "text": "follow up cause and action",
                                "chunk_index": 1,
                            }
                        },
                        {"_source": {"text": "initial issue", "chunk_index": 0}},
                    ]
                }
            }

    retrieval = QuickRetrieval(
        Backend(),
        None,
        weekly_index="w",
        mail_index="m",
        calendar_index="c",
        wiki_index="i",
    )
    docs = [
        QuickDocument(
            source="ews_mail", id="c1", original_id="parent", text="initial issue"
        )
    ]
    await retrieval.expand_documents(docs, SimpleNamespace(user_id="kim"))
    assert {"term": {"employee_id": "kim"}} in seen[0]["query"]["bool"]["filter"]
    assert {"term": {"source_id": "parent"}} in seen[0]["query"]["bool"]["filter"]
    assert docs[0].text == "initial issue\nfollow up cause and action"


def test_excess_tool_queries_are_bounded_without_repair_call():
    from app.graphs.fast_agent import SearchArguments

    assert SearchArguments(queries=["one", "two", "three"]).queries == ["one", "two"]


@pytest.mark.asyncio
async def test_sdk_tool_roundtrip_with_full_initial_evidence_budget():
    import httpx
    from openai import AsyncOpenAI
    from app.llm.gateway import OpenAILLMGateway

    requests = []

    async def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "search_evidence",
                            "arguments": json.dumps({"queries": ["specific cause"]}),
                        },
                    }
                ],
            }
            finish = "tool_calls"
        else:
            message = {
                "role": "assistant",
                "content": json.dumps(
                    {
                        "answer": "추가 근거를 확인했습니다. [S11]",
                        "missing": "해결 완료는 미확인",
                        "evidence_ids": ["S11"],
                        "evidence_status": "partial",
                    }
                ),
            }
            finish = "stop"
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "test",
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            },
        )

    client = AsyncOpenAI(
        api_key="test",
        base_url="https://example.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    class Retrieval:
        async def search(self, *args, **kwargs):
            return QuickSearchOutcome(
                documents=[
                    QuickDocument(
                        source="ews_mail",
                        id="new",
                        original_id="new",
                        text="additional finding",
                    )
                ]
            )

    outcome = QuickSearchOutcome(
        documents=[
            QuickDocument(
                source="ews_mail", id=str(i), original_id=str(i), text="initial report"
            )
            for i in range(10)
        ]
    )
    calls = []
    try:
        result, queries = await FastEvidenceAgent(OpenAILLMGateway(client, "test")).run(
            "investigate",
            QuickPlan(investigate=True),
            outcome,
            Retrieval(),
            object(),
            None,
            calls,
            [],
            "Asia/Seoul",
        )
    finally:
        await client.close()
    assert result.evidence_ids == ["S11"]
    assert len(outcome.documents) == 11
    assert len(requests) == 2
    assert requests[0]["tool_choice"] == "required"
    # New evidence allows another decision within budget.
    assert requests[1].get("tools")
    assert any(
        "additional finding" in (m.get("content") or "")
        for m in requests[1]["messages"]
    )


@pytest.mark.asyncio
async def test_workflow_records_one_supplemental_batch_and_persists_investigation():
    from app.graphs.quick_rag import QuickRAGWorkflow
    from app.domain.chat import ChatRequest
    from app.domain.policy import PolicyContext
    from app.llm.quick_rag import GroundedAnswer

    class Interpreter:
        async def interpret(self, *args):
            return QuickPlan(query="issue", investigate=True)

    class Retrieval:
        def capabilities(self):
            return {}

        async def search(self, *args, **kwargs):
            return QuickSearchOutcome(
                documents=[
                    QuickDocument(
                        source="ews_mail",
                        id="1",
                        original_id="original",
                        text="confirmed issue",
                    )
                ],
                searched_sources=["ews_mail"],
            )

    class Agent:
        async def run(self, *args, **kwargs):
            return GroundedAnswer(
                answer="문제가 보고됐습니다. [S1]",
                missing="원인은 미확인",
                evidence_ids=["S1"],
                evidence_status="partial",
            ), ["cause", "action"]

    result = await QuickRAGWorkflow(
        Retrieval(), Interpreter(), None, evidence_agent=Agent()
    ).invoke(
        ChatRequest(user_id="kim", message="조사"), PolicyContext.from_user_id("kim")
    )
    assert result.execution.additional_search_count == 1
    assert result.quick_memory.last_answer
    assert result.quick_memory.unresolved == "원인은 미확인"
    assert result.quick_memory.result_ids[0].id == "original"
    assert result.quick_memory.search_queries == ["issue", "cause", "action"]


@pytest.mark.asyncio
async def test_final_context_selects_issue_evidence_and_excludes_previous_answer():
    from app.domain.quick_rag import QuickMemory

    seen = []

    class Retrieval:
        async def search(self, *args, **kwargs):
            return QuickSearchOutcome()

    async def respond(messages):
        seen.append(messages)
        if len(seen) == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "search_evidence",
                        "args": {
                            "queries": ["specific root cause"],
                            "focus_ids": ["S1"],
                        },
                        "id": "t",
                    }
                ],
            )
        if len(seen) == 2:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "select_evidence",
                        "args": {"passage_ids": ["S1:P1"]},
                        "id": "select",
                    }
                ],
            )
        return AIMessage(
            content=json.dumps(
                {
                    "answer": "문제 보고. [S1]",
                    "missing": "원인 미확인",
                    "evidence_ids": ["S1"],
                    "evidence_status": "partial",
                }
            )
        )

    p = QuickPlan(scope="follow_up", query="specific issue", investigate=True)
    memory = QuickMemory(
        user_id="kim", plan=p, last_answer="OLD_UNVERIFIED_CLAIM", unresolved="cause"
    )
    o = QuickSearchOutcome(
        documents=[
            QuickDocument(
                source="ews_mail", id="1", original_id="1", text="specific issue"
            ),
            QuickDocument(
                source="ews_mail",
                id="2",
                original_id="2",
                text="UNRELATED_ROUTINE_REPORT",
            ),
        ]
    )
    await FastEvidenceAgent(
        None, model_factory=lambda tools, final: RunnableLambda(respond)
    ).run("더 조사", p, o, Retrieval(), object(), memory, [], [], "Asia/Seoul")
    assert "OLD_UNVERIFIED_CLAIM" in str(seen[0])
    assert "OLD_UNVERIFIED_CLAIM" not in str(seen[-1])
    assert "UNRELATED_ROUTINE_REPORT" not in str(seen[-1])
