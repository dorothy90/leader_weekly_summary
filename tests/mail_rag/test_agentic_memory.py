import asyncio
from datetime import UTC, datetime

from app.domain.agentic import AgentMemoryUpdate, EventReference
from app.domain.policy import PolicyContext
from app.persistence.conversations import (
    ConversationMemory,
    InMemoryConversationStore,
    apply_agent_memory_update,
)


def test_agent_memory_update_is_bounded_and_sanitized():
    policy = PolicyContext.from_user_id("kim")
    update = AgentMemoryUpdate(
        entities={"product": "NAND", "issue": "Cell Leakage"},
        current_topic="NAND Cell Leakage",
        search_history=[f"search-{index}" for index in range(16)],
        previous_event_reference=EventReference(
            event_id="event-kim-1",
            subject="NAND Yield Review",
            start_at_utc=datetime(2026, 8, 12, 1, tzinfo=UTC),
        ),
        retrieved_source_refs=["mail-1", "event-kim-1"],
        unresolved_information=["추가 측정 결과"],
    )

    memory = apply_agent_memory_update(ConversationMemory(), update, policy)

    assert memory.entities == {"product": "NAND", "issue": "Cell Leakage"}
    assert memory.previous_event_reference.event_id == "event-kim-1"
    assert len(memory.search_history) == 16


def test_structured_memory_round_trips_through_owner_scoped_store():
    store = InMemoryConversationStore()
    policy = PolicyContext.from_user_id("kim")
    memory = apply_agent_memory_update(
        ConversationMemory(),
        AgentMemoryUpdate(
            current_topic="NAND",
            previous_event_reference=EventReference(event_id="event-kim-1"),
        ),
        policy,
    )

    asyncio.run(store.save("conversation-1", policy, memory))
    loaded = asyncio.run(store.load("conversation-1", policy))

    assert loaded.current_topic == "NAND"
    assert loaded.previous_event_reference.event_id == "event-kim-1"
