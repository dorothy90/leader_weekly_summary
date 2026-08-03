import asyncio

import pytest
from pydantic import ValidationError

from app.domain.chat import ExecutionMetadata
from app.domain.errors import AppError, ErrorCode
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.persistence.conversations import (
    ConversationMemory,
    InMemoryConversationStore,
    MongoConversationStore,
    TurnRecord,
)


def run(coro):
    return asyncio.run(coro)


def test_conversation_create_read_and_update_require_exact_owner():
    store = InMemoryConversationStore()
    kim = PolicyContext.from_user_id("kim")
    lee = PolicyContext.from_user_id("lee")
    original = ConversationMemory(messages=[{"role": "user", "content": "hello"}])

    run(store.save("c1", kim, original))
    assert run(store.load("c1", kim)) == original

    for operation in (
        store.load("c1", lee),
        store.save("c1", lee, ConversationMemory()),
    ):
        with pytest.raises(AppError) as error:
            run(operation)
        assert error.value.code == ErrorCode.UNAUTHORIZED_RESOURCE


def test_missing_conversation_returns_none_and_memory_is_bounded():
    store = InMemoryConversationStore()
    assert run(store.load("missing", PolicyContext.from_user_id("kim"))) is None

    with pytest.raises(ValidationError):
        ConversationMemory(
            messages=[{"role": "user", "content": str(index)} for index in range(21)]
        )


def test_conversation_ids_are_validated_before_persistence():
    store = InMemoryConversationStore()
    policy = PolicyContext.from_user_id("kim")

    for conversation_id in ("", "../secret", "contains spaces", "x" * 129):
        with pytest.raises(ValueError):
            run(store.save(conversation_id, policy, ConversationMemory()))


def test_conversation_rejects_differently_owned_cited_evidence():
    store = InMemoryConversationStore()
    policy = PolicyContext.from_user_id("kim")
    foreign = Evidence(
        evidence_id="S1",
        source_type="mail",
        document_id="mail-1",
        title="title",
        excerpt="excerpt",
        score=1,
        user_id="lee",
        acl_decision_id="acl",
        content_hash="hash",
    )

    with pytest.raises(AppError) as error:
        run(
            store.save(
                "c1",
                policy,
                ConversationMemory(cited_evidence=[foreign]),
            )
        )
    assert error.value.code == ErrorCode.UNAUTHORIZED_RESOURCE


def test_conversation_persists_bounded_turn_execution_envelope():
    store = InMemoryConversationStore()
    policy = PolicyContext.from_user_id("kim")
    failed = TurnRecord(
        user_content="반도체",
        assistant_content=None,
        route="fast",
        executed_system="fast_rag",
        execution=ExecutionMetadata(
            status="failed",
            failure_stage="planning",
            error_code="LLM_TIMEOUT",
            retryable=True,
            include_in_llm_history=False,
        ),
    )

    run(store.save("c1", policy, ConversationMemory(turns=[failed])))
    loaded = run(store.load("c1", policy))

    assert loaded.turns == [failed]
    assert loaded.turns[0].execution.include_in_llm_history is False


def test_conversation_rejects_cross_owner_turn_evidence():
    store = InMemoryConversationStore()
    policy = PolicyContext.from_user_id("kim")
    foreign = Evidence(
        evidence_id="S1",
        source_type="mail",
        document_id="mail-1",
        title="title",
        excerpt="excerpt",
        score=1,
        user_id="lee",
        acl_decision_id="acl",
        content_hash="hash",
    )
    turn = TurnRecord(
        user_content="질문",
        assistant_content="답변",
        route="fast",
        executed_system="fast_rag",
        execution=ExecutionMetadata(status="succeeded"),
        cited_evidence=[foreign],
    )

    with pytest.raises(AppError) as error:
        run(store.save("c1", policy, ConversationMemory(turns=[turn])))

    assert error.value.code == ErrorCode.UNAUTHORIZED_RESOURCE


def test_mongo_store_uses_intrinsically_unique_id_for_owner_safe_create():
    class Result:
        matched_count = 0
        upserted_id = "c1"

    class Collection:
        def __init__(self):
            self.update_filter = None

        async def find_one(self, query, projection=None):
            return None

        async def update_one(self, query, update, upsert):
            self.update_filter = query
            return Result()

    collection = Collection()
    store = MongoConversationStore(collection)
    run(
        store.save(
            "c1",
            PolicyContext.from_user_id("kim"),
            ConversationMemory(),
        )
    )

    assert collection.update_filter == {"_id": "c1", "user_id": "kim"}


def test_stale_conversation_revision_cannot_silently_overwrite_a_newer_turn():
    store = InMemoryConversationStore()
    policy = PolicyContext.from_user_id("kim")
    run(store.save("c1", policy, ConversationMemory()))
    first_writer = run(store.load("c1", policy))
    stale_writer = run(store.load("c1", policy))

    run(
        store.save(
            "c1",
            policy,
            first_writer.model_copy(
                update={"messages": [{"role": "user", "content": "첫 요청"}]}
            ),
        )
    )

    with pytest.raises(AppError) as error:
        run(
            store.save(
                "c1",
                policy,
                stale_writer.model_copy(
                    update={"messages": [{"role": "user", "content": "동시 요청"}]}
                ),
            )
        )

    assert error.value.code == ErrorCode.CONVERSATION_CONFLICT
