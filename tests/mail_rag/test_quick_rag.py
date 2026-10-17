import re
import asyncio
from datetime import datetime, timezone
import pytest
from app.domain.quick_rag import (
    QuickPlan,
    QuickConditions,
    resolve_dates,
    QuickMemory,
    QuickResultRef,
)
from app.retrieval.quick_rag import QuickRetrieval
from app.graphs.quick_rag import QuickRAGWorkflow
from app.domain.chat import ChatRequest
from app.domain.policy import PolicyContext


class Backend:

    def __init__(self, fail=None, names=None):
        self.calls = []
        self.fail = fail
        self.names = names

    async def search(self, index, body):
        self.calls.append((index, body))
        if index == self.fail:
            raise TimeoutError()
        if "aggs" in body:
            return {
                "aggregations": {
                    "identities": {"buckets": [{"key": n} for n in self.names or []]}
                }
            }
        return {
            "hits": {
                "hits": [
                    {
                        "_id": "1",
                        "_score": 1,
                        "_source": {
                            "title": "hello",
                            "text": "content",
                            "source_id": "original",
                            "received_at": "2026-10-15T00:00:00Z",
                        },
                    }
                ]
            }
        }


class Embeddings:

    async def embed(self, text):
        raise AssertionError("metadata must not embed")


class LLM:

    def __init__(self, plan, answer="grounded [S99]"):
        self.plan = plan
        self.calls = []
        self.answer = answer

    async def complete_model(self, system, user, schema):
        if schema.__name__ == "GroundedAnswer":
            self.calls.append("answer")
            return schema(
                evidence_status="sufficient",
                answer=self.answer,
                evidence_ids=re.findall(r"\[(S\d+)\]", self.answer),
            )
        self.calls.append("interpret")
        return self.plan

    async def complete_text(self, system, user):
        self.calls.append("answer")
        return self.answer


def retrieval(backend):
    return QuickRetrieval(
        backend,
        Embeddings(),
        weekly_index="weekly",
        mail_index="mail",
        calendar_index="calendar",
        wiki_index="wiki",
        source_timeout_seconds=0.02,
    )


@pytest.mark.asyncio
async def test_list_one_llm_all_sources_acl_and_no_embedding():
    b = Backend()
    llm = LLM(QuickPlan(intent="list", query="NEW"))
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="NEW 목록"),
        PolicyContext.from_user_id("alice"),
        None,
    )
    assert llm.calls == ["interpret"]
    assert len(b.calls) == 4
    assert result.agent_trace.iteration_count == 1
    assert len(result.evidence) == 4
    assert all(("alice" in str(body) for _, body in b.calls))
    assert all(("range" not in str(body) for _, body in b.calls))


@pytest.mark.asyncio
async def test_timeout_preserves_other_sources():
    outcome = await retrieval(Backend(fail="mail")).search(
        QuickPlan(intent="list"), PolicyContext.from_user_id("alice")
    )
    assert len(outcome.documents) == 3
    assert outcome.disclosures
    assert outcome.failed_sources == ["ews_mail"]


@pytest.mark.asyncio
async def test_sender_ambiguity_and_owner_scope():
    b = Backend(names=["a@example.com", "b@example.com"])
    out = await retrieval(b).search(
        QuickPlan(
            intent="list",
            source_scope="mail",
            conditions=QuickConditions(sender_name="홍길동 TL"),
        ),
        PolicyContext.from_user_id("alice"),
    )
    assert not out.documents
    assert "여러" in out.clarification
    assert "alice" in str(b.calls)
    assert all(("aggs" in body for _, body in b.calls if _ == "mail"))


@pytest.mark.asyncio
async def test_unsupported_filters_skip_source():
    b = Backend()
    out = await retrieval(b).search(
        QuickPlan(
            intent="list",
            source_scope="wiki",
            conditions=QuickConditions(sender_email="a@example.com"),
        ),
        PolicyContext.from_user_id("alice"),
    )
    assert not b.calls
    assert out.disclosures


@pytest.mark.asyncio
async def test_invalid_citations_no_repair():

    class E:

        async def embed(self, text):
            raise RuntimeError()

    b = Backend()
    r = retrieval(b)
    r.embeddings = E()
    llm = LLM(QuickPlan(query="hello"))
    result = await QuickRAGWorkflow(r, llm, llm).invoke(
        ChatRequest(user_id="alice", message="hello"),
        PolicyContext.from_user_id("alice"),
        None,
    )
    assert llm.calls == ["interpret", "answer"]
    assert result.quality.citation_valid is False
    assert "S99" not in result.answer


@pytest.mark.asyncio
async def test_statistics_no_search():
    b = Backend()
    llm = LLM(QuickPlan(intent="statistics"))
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="몇 개?"),
        PolicyContext.from_user_id("alice"),
        None,
    )
    assert not b.calls
    assert llm.calls == ["interpret"]
    assert result.quality.limited_answer


def test_date_defaults_and_timezone():
    now = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    assert (
        resolve_dates(QuickPlan(intent="list", recent=True), now, "Asia/Seoul").start
        is None
    )
    d = resolve_dates(QuickPlan(recent=True), now, "Asia/Seoul")
    assert (d.end - d.start).days == 7
    d = resolve_dates(
        QuickPlan(
            conditions=QuickConditions(date_start="2026-10-17", date_end="2026-10-17")
        ),
        now,
        "Asia/Seoul",
    )
    assert d.start.isoformat() == "2026-10-16T15:00:00+00:00"
    assert (d.end - d.start).days == 1


@pytest.mark.asyncio
async def test_within_results_requeries_original_ids_with_current_acl():
    from app.persistence.conversations import ConversationMemory

    memory = QuickMemory(
        user_id="alice",
        plan=QuickPlan(
            intent="list",
            source_scope="mail",
            conditions=QuickConditions(sender_email="a@example.com"),
        ),
        result_ids=[QuickResultRef(source="ews_mail", id="original")],
    )
    conversation = ConversationMemory(quick_memory=memory)
    llm = LLM(QuickPlan(intent="list", scope="within_results"))
    b = Backend()
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="그중 목록"),
        PolicyContext.from_user_id("alice"),
        conversation,
    )
    assert len(b.calls) == 1
    assert (
        "original" in str(b.calls)
        and "alice" in str(b.calls)
        and ("a@example.com" in str(b.calls))
    )
    assert result.quick_memory.plan.conditions.sender_email == "a@example.com"


@pytest.mark.asyncio
async def test_foreign_memory_not_given_to_interpreter_or_retrieval():
    from types import SimpleNamespace

    memory = QuickMemory(
        user_id="bob",
        plan=QuickPlan(),
        result_ids=[QuickResultRef(source="ews_mail", id="secret")],
    )

    class InspectLLM(LLM):

        async def complete_model(self, system, user, schema):
            assert "secret" not in user and "bob" not in user
            return await super().complete_model(system, user, schema)

    b = Backend()
    llm = InspectLLM(QuickPlan(intent="list", scope="within_results"))
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="그중"),
        PolicyContext.from_user_id("alice"),
        SimpleNamespace(quick_memory=memory, messages=[]),
    )
    assert not b.calls
    assert result.execution.error_code == "NO_PRIOR_RESULTS"


@pytest.mark.asyncio
async def test_hybrid_sources_and_branches_are_concurrent():

    class ConcurrentBackend(Backend):

        def __init__(self):
            super().__init__()
            self.active = 0
            self.maximum = 0

        async def search(self, index, body):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                await asyncio.sleep(0.01)
                return await super().search(index, body)
            finally:
                self.active -= 1

    class E:

        async def embed(self, text):
            await asyncio.sleep(0.001)
            return [1.0]

    b = ConcurrentBackend()
    r = retrieval(b)
    r.embeddings = E()
    r.compatible_sources = frozenset(
        ["weekly_mail", "wiki_summary", "ews_mail", "ews_calendar"]
    )
    r.source_timeout_seconds = 1
    out = await r.search(QuickPlan(query="topic"), PolicyContext.from_user_id("alice"))
    assert out.retrieval_mode == "hybrid"
    assert len(b.calls) == 8
    assert b.maximum >= 5
    assert len(out.documents) == 4


@pytest.mark.asyncio
async def test_calendar_overlap_and_exact_dates_do_not_use_week_index():
    b = Backend()
    out = await retrieval(b).search(
        QuickPlan(
            intent="list",
            conditions=QuickConditions(date_start="2026-10-17", date_end="2026-10-17"),
        ),
        PolicyContext.from_user_id("alice"),
    )
    assert {i for i, _ in b.calls} == {"mail", "calendar", "wiki"}
    calendar = next((body for index, body in b.calls if index == "calendar"))
    assert "'start_at_utc': {'lt':" in str(calendar)
    assert "'end_at_utc': {'gt':" in str(calendar)
    assert len(out.disclosures) == 2


@pytest.mark.asyncio
async def test_sender_not_found_does_not_broaden():
    b = Backend(names=[])
    out = await retrieval(b).search(
        QuickPlan(intent="list", conditions=QuickConditions(sender_name="없는 사람")),
        PolicyContext.from_user_id("alice"),
    )
    assert not out.documents and "찾지 못" in out.clarification
    assert len(b.calls) == 1


@pytest.mark.asyncio
async def test_generated_email_is_not_used_without_literal_or_memory():
    b = Backend()
    llm = LLM(
        QuickPlan(
            intent="list",
            conditions=QuickConditions(sender_email="guessed@example.com"),
        )
    )
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="홍길동 메일"),
        PolicyContext.from_user_id("alice"),
        None,
    )
    assert not b.calls
    assert result.execution.error_code == "CLARIFICATION_REQUIRED"


@pytest.mark.asyncio
async def test_unverified_embeddings_use_bm25_and_disclose():
    b = Backend()
    out = await retrieval(b).search(
        QuickPlan(query="hello"), PolicyContext.from_user_id("alice")
    )
    assert len(b.calls) == 4
    assert out.retrieval_mode == "bm25"
    assert out.disclosures


@pytest.mark.asyncio
async def test_source_specific_original_mail_ids_and_list_mappings():
    class B(Backend):
        async def search(self, index, body):
            self.calls.append((index, body))
            return {
                "hits": {
                    "hits": [
                        {"_id": "part1", "_source": {"mail_id": "mail1", "text": "A"}},
                        {"_id": "part2", "_source": {"mail_id": "mail1", "text": "B"}},
                    ]
                }
            }

    b = B()
    out = await retrieval(b).search(
        QuickPlan(intent="list", source_scope="report"),
        PolicyContext.from_user_id("alice"),
    )
    assert len(out.documents) == 1 and out.documents[0].original_id == "mail1"
    assert b.calls[0][1]["collapse"]["field"] == "mail_id"


@pytest.mark.asyncio
async def test_calendar_upcoming_preference_keeps_past_fallback():
    class B(Backend):
        async def search(self, index, body):
            self.calls.append((index, body))
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "past",
                            "_source": {
                                "source_id": "past",
                                "title": "AX day",
                                "start_at_utc": "2026-10-10T00:00:00Z",
                            },
                        },
                        {
                            "_id": "future",
                            "_source": {
                                "source_id": "future",
                                "title": "AX day",
                                "start_at_utc": "2026-10-20T00:00:00Z",
                            },
                        },
                    ]
                }
            }

    b = B()
    plan = QuickPlan(source_scope="calendar", query="AX day", prefer_upcoming=True)
    out = await retrieval(b).search(
        plan,
        PolicyContext.from_user_id("alice"),
        now=datetime(2026, 10, 17, tzinfo=timezone.utc),
    )
    assert [d.original_id for d in out.documents] == ["future", "past"]
    assert "should" in b.calls[0][1]["query"]["bool"]
    assert not any(
        "start_at_utc" in str(f) for f in b.calls[0][1]["query"]["bool"]["filter"]
    )


@pytest.mark.asyncio
async def test_broad_list_represents_sources_and_discloses_week_precision():
    class B(Backend):
        async def search(self, index, body):
            self.calls.append((index, body))
            rows = []
            for i in range(10):
                raw = {
                    "title": index,
                    "mail_id": f"{index}-{i}",
                    "source_id": f"{index}-{i}",
                }
                raw.update(
                    {"week": "2026-40"}
                    if index in ("weekly", "wiki")
                    else {
                        "received_at": "2026-10-17T00:00:00Z",
                        "start_at_utc": "2026-10-17T00:00:00Z",
                    }
                )
                rows.append({"_id": str(i), "_source": raw})
            return {"hits": {"hits": rows}}

    out = await retrieval(B()).search(
        QuickPlan(intent="list"), PolicyContext.from_user_id("alice")
    )
    assert len(out.documents) == 10
    assert {d.source for d in out.documents} == {
        "weekly_mail",
        "wiki_summary",
        "ews_mail",
        "ews_calendar",
    }
    assert out.documents[0].source.startswith("ews_")
    assert any("주 단위" in d for d in out.disclosures)


@pytest.mark.asyncio
async def test_temporal_followup_replaces_explicit_group_and_can_clear():
    from app.persistence.conversations import ConversationMemory

    previous = QuickMemory(
        user_id="alice",
        plan=QuickPlan(
            intent="list",
            source_scope="calendar",
            conditions=QuickConditions(date_start="2026-09-01", date_end="2026-09-30"),
        ),
        result_ids=[],
    )
    b = Backend()
    llm = LLM(
        QuickPlan(
            intent="list",
            scope="follow_up",
            conditions=QuickConditions(date_period="this_month"),
            temporal_expression="이번 달",
        )
    )
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="이번 달은?"),
        PolicyContext.from_user_id("alice"),
        ConversationMemory(quick_memory=previous),
    )
    c = result.quick_memory.plan.conditions
    assert c.date_period == "this_month" and c.date_start is None and c.date_end is None
    llm.plan = QuickPlan(intent="list", scope="follow_up", clear_conditions=["dates"])
    b.calls = []
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="기간 제한 없이"),
        PolicyContext.from_user_id("alice"),
        ConversationMemory(quick_memory=previous),
    )
    assert "range" not in str(b.calls)


@pytest.mark.asyncio
async def test_followup_preserves_query_recent_and_removes_filters_explicitly():
    from app.persistence.conversations import ConversationMemory

    previous = QuickMemory(
        user_id="alice",
        plan=QuickPlan(
            query="AX",
            recent=True,
            source_scope="mail",
            conditions=QuickConditions(teams=["old-team"]),
        ),
        result_ids=[],
    )
    b = Backend()
    llm = LLM(
        QuickPlan(
            intent="list",
            scope="follow_up",
            conditions=QuickConditions(sender_email="a@example.com"),
            clear_conditions=["teams"],
        )
    )
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="그중 a@example.com 발신"),
        PolicyContext.from_user_id("alice"),
        ConversationMemory(quick_memory=previous),
    )
    assert (
        result.quick_memory.plan.query == "AX"
        and result.quick_memory.plan.recent is True
    )
    assert result.quick_memory.plan.conditions.teams == []
    assert "AX" in str(b.calls) and "old-team" not in str(b.calls)
    llm.plan = QuickPlan(
        intent="list", scope="follow_up", recent=False, clear_conditions=["query"]
    )
    result = await QuickRAGWorkflow(retrieval(b), llm, llm).invoke(
        ChatRequest(user_id="alice", message="주제 기간 없이"),
        PolicyContext.from_user_id("alice"),
        ConversationMemory(quick_memory=previous),
    )
    assert (
        result.quick_memory.plan.query == ""
        and result.quick_memory.plan.recent is False
    )


@pytest.mark.asyncio
async def test_location_uses_phrase_for_text_mapping():
    b = Backend()
    await retrieval(b).search(
        QuickPlan(
            intent="list",
            source_scope="calendar",
            conditions=QuickConditions(location="Meeting Room A"),
        ),
        PolicyContext.from_user_id("alice"),
    )
    assert {"match_phrase": {"location": "Meeting Room A"}} in b.calls[0][1]["query"][
        "bool"
    ]["filter"]


@pytest.mark.asyncio
async def test_memory_contains_only_cited_results_and_new_general_clears():
    from app.persistence.conversations import ConversationMemory

    b = Backend()
    llm = LLM(QuickPlan(query="AX"), answer="확인 [S1]")
    workflow = QuickRAGWorkflow(retrieval(b), llm, llm)
    result = await workflow.invoke(
        ChatRequest(user_id="alice", message="AX"),
        PolicyContext.from_user_id("alice"),
        None,
    )
    assert len(result.evidence) == 4 and len(result.quick_memory.result_ids) == 1
    conversation = ConversationMemory(quick_memory=result.quick_memory)
    llm.plan = QuickPlan(intent="list", scope="within_results")
    b.calls = []
    result = await workflow.invoke(
        ChatRequest(user_id="alice", message="그중 목록"),
        PolicyContext.from_user_id("alice"),
        conversation,
    )
    assert len(b.calls) == 1
    llm.plan = QuickPlan(intent="general", general_answer="안녕하세요")
    result = await workflow.invoke(
        ChatRequest(user_id="alice", message="안녕"),
        PolicyContext.from_user_id("alice"),
        conversation,
    )
    assert result.quick_memory is not None and result.quick_memory.result_ids == []


@pytest.mark.asyncio
async def test_upcoming_list_keeps_nearest_first_after_global_ordering():
    class B(Backend):
        async def search(self, index, body):
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "later",
                            "_source": {
                                "source_id": "later",
                                "start_at_utc": "2099-12-20T00:00:00Z",
                            },
                        },
                        {
                            "_id": "near",
                            "_source": {
                                "source_id": "near",
                                "start_at_utc": "2099-12-10T00:00:00Z",
                            },
                        },
                        {
                            "_id": "past",
                            "_source": {
                                "source_id": "past",
                                "start_at_utc": "2000-01-01T00:00:00Z",
                            },
                        },
                    ]
                }
            }

    out = await retrieval(B()).search(
        QuickPlan(intent="list", source_scope="calendar", prefer_upcoming=True),
        PolicyContext.from_user_id("alice"),
    )
    assert [d.original_id for d in out.documents] == ["near", "later", "past"]


@pytest.mark.asyncio
async def test_wiki_date_filters_use_document_update_time_and_disclose():
    b = Backend()
    out = await retrieval(b).search(
        QuickPlan(
            intent="list",
            source_scope="wiki",
            conditions=QuickConditions(date_start="2026-10-17", date_end="2026-10-17"),
        ),
        PolicyContext.from_user_id("alice"),
    )
    assert len(b.calls) == 1
    assert "updated_at" in str(b.calls[0][1]["query"])
    assert b.calls[0][1]["sort"][0] == {
        "updated_at": {"order": "desc", "missing": "_last"}
    }
    assert any("수정 시각" in d for d in out.disclosures)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "source_scope,timestamp", [("wiki", "updated_at"), ("wiki", "created_at")]
)
async def test_timestamped_week_documents_do_not_claim_coarse_week_recency(
    source_scope, timestamp
):
    class B(Backend):
        async def search(self, index, body):
            return {
                "hits": {
                    "hits": [
                        {
                            "_id": "1",
                            "_source": {
                                "title": "document",
                                "week": "2026-40",
                                timestamp: "2026-10-17T00:00:00Z",
                            },
                        }
                    ]
                }
            }

    out = await retrieval(B()).search(
        QuickPlan(intent="list", source_scope=source_scope),
        PolicyContext.from_user_id("alice"),
    )
    assert not any("주 단위" in d for d in out.disclosures)


@pytest.mark.asyncio
async def test_one_embedding_per_query_shared_across_sources():
    class E:
        calls = 0

        async def embed(self, text):
            self.calls += 1
            await asyncio.sleep(0.01)
            return [1.0]

    r = retrieval(Backend())
    r.source_timeout_seconds = 1
    r.embeddings = E()
    r.compatible_sources = frozenset(
        ["weekly_mail", "wiki_summary", "ews_mail", "ews_calendar"]
    )
    out = await r.search(QuickPlan(query="topic"), PolicyContext.from_user_id("alice"))
    assert out.retrieval_mode == "hybrid"
    assert r.embeddings.calls == 1


@pytest.mark.asyncio
async def test_sender_only_summary_uses_filters_without_embedding():
    b = Backend()
    r = retrieval(b)
    r.compatible_sources = frozenset(["ews_mail"])
    out = await r.search(
        QuickPlan(
            intent="answer",
            source_scope="mail",
            query="",
            conditions=QuickConditions(sender_email="kim.oo@example.com"),
        ),
        PolicyContext.from_user_id("alice"),
    )
    assert not any("임베딩" in d or "의미 검색" in d for d in out.disclosures)
    assert out.documents
    assert all("knn" not in str(body) for _, body in b.calls)


@pytest.mark.asyncio
async def test_summary_passes_resolved_sender_filter_to_answer_model():
    import json

    class Capture(LLM):
        async def complete_model(self, system, user, schema):
            if schema.__name__ == "GroundedAnswer":
                payload = json.loads(user)
                assert (
                    payload["search_conditions"]["sender_email"] == "kim.oo@example.com"
                )
                assert payload["search_scope"] == "mail"
            return await super().complete_model(system, user, schema)

    m = Capture(
        QuickPlan(
            intent="answer",
            source_scope="mail",
            query="",
            conditions=QuickConditions(sender_email="kim.oo@example.com"),
        ),
        "메일은 업무 점검 내용입니다. [S1]",
    )
    r = await QuickRAGWorkflow(retrieval(Backend()), m, m).invoke(
        ChatRequest(user_id="alice", message="kim.oo@example.com이 보낸 메일 요약"),
        PolicyContext.from_user_id("alice"),
    )
    assert r.quality.citation_valid


@pytest.mark.asyncio
async def test_unrequested_date_filters_are_removed_before_search():
    b = Backend()
    m = LLM(
        QuickPlan(
            intent="list",
            source_scope="mail",
            query="",
            recent=True,
            conditions=QuickConditions(
                sender_name="김테스트", date_period="this_week", weeks=["2026-42"]
            ),
        )
    )
    b.names = ["kim.oo@example.com"]
    r = await QuickRAGWorkflow(retrieval(b), m, m).invoke(
        ChatRequest(user_id="alice", message="김테스트가 보낸 메일 요약해줘"),
        PolicyContext.from_user_id("alice"),
    )
    assert r.quick_memory.plan.conditions.date_period is None
    assert not r.quick_memory.plan.conditions.weeks
    assert r.quick_memory.plan.recent is False
    assert "range" not in str(b.calls)


@pytest.mark.asyncio
async def test_realistic_sender_metadata_survives_gateway_sanitization():
    import json
    from app.security.redaction import sanitize_text

    class B(Backend):
        async def search(self, index, body):
            result = await super().search(index, body)
            if "hits" in result:
                result["hits"]["hits"][0]["_source"].update(
                    sender_name="김테스트", sender_email="kim.oo@example.com"
                )
            return result

    class Capture(LLM):
        async def complete_model(self, system, user, schema):
            if schema.__name__ == "GroundedAnswer":
                safe = json.loads(sanitize_text(user))
                assert isinstance(safe["evidence"], list)
                assert "김테스트" in safe["evidence"][0]["text"]
                assert "kim.oo@example.com" in safe["evidence"][0]["text"]
            return await super().complete_model(system, user, schema)

    m = Capture(
        QuickPlan(
            source_scope="mail",
            conditions=QuickConditions(sender_email="kim.oo@example.com"),
        ),
        "확인 [S1]",
    )
    r = await QuickRAGWorkflow(retrieval(B()), m, m).invoke(
        ChatRequest(user_id="alice", message="kim.oo@example.com 메일 요약"),
        PolicyContext.from_user_id("alice"),
    )
    assert r.quality.citation_valid


@pytest.mark.asyncio
async def test_ordinary_mail_does_not_infer_other_mail_type():
    backend = Backend(names=["kim@example.com"])
    llm = LLM(
        QuickPlan(
            source_scope="mail",
            query="장비 정기 점검",
            conditions=QuickConditions(sender_name="김테스트", mail_type="other"),
        ),
        answer="점검 내용 [S1]",
    )
    wf = QuickRAGWorkflow(retrieval(backend), llm, llm)
    await wf.invoke(
        ChatRequest(
            user_id="alice", message="김테스트의 장비 정기 점검 메일을 요약해줘"
        ),
        PolicyContext.from_user_id("alice"),
    )
    assert backend.calls
    for _, body in backend.calls:
        assert not any(
            "mail_type" in str(f)
            for f in body.get("query", {}).get("bool", {}).get("filter", [])
        )


@pytest.mark.asyncio
async def test_explicit_weekly_report_type_is_retained():
    backend = Backend()
    llm = LLM(
        QuickPlan(
            intent="list",
            source_scope="report",
            conditions=QuickConditions(mail_type="weekly_report"),
        )
    )
    wf = QuickRAGWorkflow(retrieval(backend), llm, llm)
    await wf.invoke(
        ChatRequest(user_id="alice", message="주간 보고 메일 목록 보여줘"),
        PolicyContext.from_user_id("alice"),
    )
    assert any(
        {"term": {"mail_type": "weekly_report"}}
        in body.get("query", {}).get("bool", {}).get("filter", [])
        for _, body in backend.calls
    )


@pytest.mark.asyncio
async def test_explicit_api_mail_type_survives_follow_up():
    from app.persistence.conversations import ConversationMemory

    backend = Backend()
    llm = LLM(QuickPlan(intent="list", source_scope="report"))
    wf = QuickRAGWorkflow(retrieval(backend), llm, llm)
    first = await wf.invoke(
        ChatRequest(
            user_id="alice",
            message="목록 보여줘",
            filters={"mail_type": "weekly_report"},
        ),
        PolicyContext.from_user_id("alice"),
    )
    backend.calls.clear()
    llm.plan = QuickPlan(intent="list", scope="follow_up")
    second = await wf.invoke(
        ChatRequest(user_id="alice", message="이어서 목록 보여줘"),
        PolicyContext.from_user_id("alice"),
        ConversationMemory(quick_memory=first.quick_memory),
    )
    assert second.quick_memory.plan.conditions.mail_type == "weekly_report"
    assert any(
        {"term": {"mail_type": "weekly_report"}}
        in body.get("query", {}).get("bool", {}).get("filter", [])
        for _, body in backend.calls
    )
