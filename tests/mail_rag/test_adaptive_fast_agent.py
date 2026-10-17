import asyncio
import json
import time
from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from app.graphs.fast_agent import FastEvidenceAgent
from app.domain.quick_rag import QuickPlan, QuickConditions
from app.retrieval.quick_rag import QuickDocument, QuickSearchOutcome, QuickRetrieval


def doc(i, text):
    return QuickDocument(source="ews_mail", id=str(i), original_id=str(i), text=text)


def final():
    return AIMessage(
        content=json.dumps(
            dict(
                answer="원인 보고 [S1]",
                missing="완료 미확인",
                evidence_ids=["S1"],
                evidence_status="partial",
            )
        )
    )


def search(q, i):
    return AIMessage(
        content="",
        tool_calls=[dict(name="search_evidence", args={"queries": [q]}, id=str(i))],
    )


@pytest.mark.asyncio
async def test_loop_continues_when_new_evidence_arrives_and_stops_without_progress():
    seen = []
    flags = []

    class Retrieval:
        async def search(self, plan, policy, **kwargs):
            seen.append(plan)
            return QuickSearchOutcome(documents=[doc(2, "원인 확인")])

    replies = [
        search("누설 원인", 1),
        search("누설 조치", 2),
        AIMessage(
            content="",
            tool_calls=[
                dict(
                    name="select_evidence",
                    args={"passage_ids": ["S1:P1", "S2:P1"]},
                    id="select",
                )
            ],
        ),
        final(),
    ]

    async def respond(messages):
        return replies.pop(0)

    def factory(tools, final):
        flags.append(final)
        return RunnableLambda(respond)

    outcome = QuickSearchOutcome(documents=[doc(1, "누설 증가")])
    await FastEvidenceAgent(None, model_factory=factory).run(
        "조사",
        QuickPlan(
            investigate=True, conditions=QuickConditions(sender_email="kim@example.com")
        ),
        outcome,
        Retrieval(),
        object(),
        None,
        [],
        [],
        "Asia/Seoul",
        deadline=time.monotonic() + 20,
    )
    assert flags == [False, False, False, True]
    assert len(seen) == 2
    assert all(p.conditions.sender_email == "kim@example.com" for p in seen)
    assert outcome.supplemental_batches == 2


@pytest.mark.asyncio
async def test_low_remaining_budget_skips_tools_and_still_answers():
    flags = []

    class Retrieval:
        async def search(self, *args, **kwargs):
            raise AssertionError("no search budget")

    async def respond(messages):
        return final()

    def factory(tools, final):
        flags.append(final)
        return RunnableLambda(respond)

    await FastEvidenceAgent(None, model_factory=factory).run(
        "조사",
        QuickPlan(investigate=True),
        QuickSearchOutcome(documents=[doc(1, "누설")]),
        Retrieval(),
        object(),
        None,
        [],
        [],
        "Asia/Seoul",
        deadline=time.monotonic() + 5,
    )
    assert flags == [True]


@pytest.mark.asyncio
async def test_expansion_parallel_fusion_and_filter_preservation():
    active = 0
    peak = 0
    seen = []

    class Retrieval(QuickRetrieval):
        async def search(self, plan, policy, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            seen.append(plan)
            await asyncio.sleep(0.01)
            active -= 1
            return QuickSearchOutcome(
                documents=[doc(1, "same"), doc(plan.query, "distinct")],
                resolved_plan=plan,
            )

    r = Retrieval(
        None, None, weekly_index="w", mail_index="m", calendar_index="c", wiki_index="i"
    )
    p = QuickPlan(
        query="leakage",
        search_queries=["누설", "누설 원인"],
        conditions=QuickConditions(sender_email="kim@example.com"),
    )
    result = await r.search_expanded(p, object())
    assert peak == 3
    assert len(result.documents) == 4
    assert result.documents[0].original_id == "1"
    assert all(x.conditions.sender_email == "kim@example.com" for x in seen)


@pytest.mark.asyncio
async def test_selected_passages_exclude_irrelevant_sections():
    seen = []

    async def respond(messages):
        seen.append(messages)
        if len(seen) == 1:
            return AIMessage(
                content="",
                tool_calls=[
                    dict(
                        name="select_evidence", args={"passage_ids": ["S1:P2"]}, id="s"
                    )
                ],
            )
        return final()

    outcome = QuickSearchOutcome(documents=[doc(1, "UNRELATED_ROUTINE\n\n원인 확인")])
    await FastEvidenceAgent(
        None, model_factory=lambda tools, final: RunnableLambda(respond)
    ).run("원인", QuickPlan(), outcome, object(), object(), None, [], [], "Asia/Seoul")
    assert "UNRELATED_ROUTINE" not in str(seen[-1])
    assert "원인 확인" in str(seen[-1])


@pytest.mark.asyncio
async def test_followup_cannot_replace_confirmed_sender_with_body_name():
    from app.graphs.quick_rag import QuickRAGWorkflow
    from app.domain.quick_rag import QuickMemory
    from app.domain.chat import ChatRequest
    from app.domain.policy import PolicyContext

    seen = []

    class Interpreter:
        async def interpret(self, *args):
            return QuickPlan(
                intent="list",
                scope="follow_up",
                conditions=QuickConditions(sender_name="김OO"),
            )

    class Retrieval:
        def capabilities(self):
            return {}

        async def search(self, plan, *args, **kwargs):
            seen.append(plan)
            return QuickSearchOutcome(documents=[doc(1, "문제")])

    memory = QuickMemory(
        user_id="kim",
        plan=QuickPlan(conditions=QuickConditions(sender_email="kim@example.com")),
    )
    await QuickRAGWorkflow(Retrieval(), Interpreter(), None).invoke(
        ChatRequest(user_id="kim", message="그 내용 더 조사해줘"),
        __import__(
            "app.domain.policy", fromlist=["PolicyContext"]
        ).PolicyContext.from_user_id("kim"),
        SimpleNamespace(quick_memory=memory),
    )
    assert seen[0].conditions.sender_name is None
    assert seen[0].conditions.sender_email == "kim@example.com"


@pytest.mark.asyncio
async def test_slow_decision_preserves_final_answer_budget():
    flags = []

    async def slow(messages):
        await asyncio.sleep(10)
        return search("cause", 1)

    async def answer(messages):
        return final()

    def factory(tools, is_final):
        flags.append(is_final)
        return RunnableLambda(answer if is_final else slow)

    started = time.monotonic()
    await FastEvidenceAgent(None, model_factory=factory).run(
        "조사",
        QuickPlan(investigate=True),
        QuickSearchOutcome(documents=[doc(1, "누설")]),
        object(),
        object(),
        None,
        [],
        [],
        "Asia/Seoul",
        deadline=time.monotonic() + 8.1,
    )
    # Decision is bounded separately; final answer must still execute if it times out.
    assert flags[-1] is True


@pytest.mark.asyncio
async def test_new_matching_passage_in_same_parent_is_not_discarded():
    captured = []

    class Retrieval:
        async def search(self, *args, **kwargs):
            return QuickSearchOutcome(documents=[doc(1, "DISTANT_CAUSE")])

    replies = [
        search("cause", 1),
        AIMessage(
            content="",
            tool_calls=[
                dict(name="select_evidence", args={"passage_ids": ["S1:P2"]}, id="s")
            ],
        ),
        final(),
    ]

    async def respond(messages):
        captured.append(messages)
        return replies.pop(0)

    outcome = QuickSearchOutcome(documents=[doc(1, "initial symptom")])
    await FastEvidenceAgent(
        None, model_factory=lambda tools, final: RunnableLambda(respond)
    ).run(
        "cause",
        QuickPlan(investigate=True),
        outcome,
        Retrieval(),
        object(),
        None,
        [],
        [],
        "Asia/Seoul",
    )
    assert "DISTANT_CAUSE" in str(captured[-1])
    assert outcome.investigation_stop != "no_new_evidence"


@pytest.mark.asyncio
async def test_low_budget_followup_does_not_restore_before_answer():
    from app.domain.quick_rag import QuickMemory, QuickResultRef

    class Retrieval:
        async def search(self, *args, **kwargs):
            raise AssertionError("reserve must prevent restore")

    async def respond(messages):
        return final()

    p = QuickPlan(scope="follow_up")
    m = QuickMemory(
        user_id="kim", plan=p, result_ids=[QuickResultRef(source="ews_mail", id="1")]
    )
    await FastEvidenceAgent(
        None, model_factory=lambda tools, final: RunnableLambda(respond)
    ).run(
        "follow",
        p,
        QuickSearchOutcome(documents=[doc(1, "issue")]),
        Retrieval(),
        object(),
        m,
        [],
        [],
        "Asia/Seoul",
        deadline=time.monotonic() + 5,
    )


@pytest.mark.asyncio
async def test_parallel_lanes_keep_completed_results_when_one_stalls():
    cancelled = []

    class Retrieval(QuickRetrieval):
        async def search(self, plan, policy, **kwargs):
            if plan.query == "slow":
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise
            return QuickSearchOutcome(documents=[doc(1, "available evidence")])

    r = Retrieval(
        None, None, weekly_index="w", mail_index="m", calendar_index="c", wiki_index="i"
    )
    start = time.monotonic()
    result = await r.search_expanded(
        QuickPlan(query="slow", search_queries=["fast"]), object()
    )
    assert result.documents[0].text == "available evidence"
    assert cancelled and time.monotonic() - start < 4.5
    assert result.disclosures


@pytest.mark.asyncio
async def test_passage_citations_normalize_only_known_ids():
    async def answer(messages):
        return AIMessage(
            content=json.dumps(
                dict(
                    answer="사실 [S1:P1]",
                    missing="",
                    evidence_ids=["S1:P1"],
                    evidence_status="sufficient",
                )
            )
        )

    result, _ = await FastEvidenceAgent(
        None, model_factory=lambda tools, final: RunnableLambda(answer)
    ).run(
        "fact",
        QuickPlan(),
        QuickSearchOutcome(documents=[doc(1, "fact")]),
        object(),
        object(),
        None,
        [],
        [],
        "Asia/Seoul",
        deadline=time.monotonic() + 5,
    )
    assert result.answer == "사실 [S1]" and result.evidence_ids == ["S1"]


@pytest.mark.asyncio
async def test_selecting_passages_with_search_avoids_extra_selection_call():
    flags = []

    class Retrieval:
        async def search(self, *args, **kwargs):
            return QuickSearchOutcome()

    replies = [
        AIMessage(
            content="",
            tool_calls=[
                dict(
                    name="search_evidence",
                    args={"queries": ["cause"], "passage_ids": ["S1:P1"]},
                    id="s",
                )
            ],
        ),
        final(),
    ]

    async def reply(messages):
        return replies.pop(0)

    def factory(tools, is_final):
        flags.append(is_final)
        return RunnableLambda(reply)

    o = QuickSearchOutcome(documents=[doc(1, "issue")])
    await FastEvidenceAgent(None, model_factory=factory).run(
        "cause",
        QuickPlan(investigate=True),
        o,
        Retrieval(),
        object(),
        None,
        [],
        [],
        "Asia/Seoul",
    )
    assert flags == [False, True]
    assert o.selected_passage_count == 1


def test_action_item_is_not_execution_evidence():
    from app.graphs.fast_agent import action_evidence_status

    assert action_evidence_status(
        "상태를 확인했고 Action으로 수율 편차 원인을 분석한다."
    ).startswith("TASK_ITEM_ONLY")
    assert action_evidence_status(
        "Action: 누설 원인 분석 완료, 조치 검증 진행 중"
    ).startswith("SOURCE_TEXT")


@pytest.mark.asyncio
async def test_search_round_cap_blocks_fourth_batch():
    count=0
    offered=[]
    class Retrieval:
        async def search(self,*args,**kwargs):
            nonlocal count
            count+=1
            return QuickSearchOutcome(documents=[doc(count+1,"new finding "+str(count))])
    replies=[search("cause",1),search("progress",2),search("action",3),AIMessage(content="",tool_calls=[dict(name="select_evidence",args={"passage_ids":["S1:P1"]},id="select")]),final()]
    async def respond(messages): return replies.pop(0)
    def factory(tools,is_final):
        offered.append([tool.name for tool in tools])
        return RunnableLambda(respond)
    outcome=QuickSearchOutcome(documents=[doc(1,"issue")])
    await FastEvidenceAgent(None,model_factory=factory).run("investigate",QuickPlan(investigate=True),outcome,Retrieval(),object(),None,[],[],"Asia/Seoul")
    assert count==3 and outcome.supplemental_batches==3
    assert offered[3]==["select_evidence"]
