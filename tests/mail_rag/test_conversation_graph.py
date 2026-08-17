import asyncio
from types import SimpleNamespace

from app.domain.chat import ChatRequest, ExecutionMetadata
from app.domain.evidence import Evidence
from app.domain.policy import PolicyContext
from app.graphs.conversation import (
    MAX_CONTEXT_MESSAGES,
    build_conversation_state,
    contextualize_request,
    eligible_prior_evidence,
    message_dicts,
)
from app.persistence.conversations import TurnRecord


def test_conversation_state_is_bounded_and_always_preserves_latest_user_turn():
    history = []
    for index in range(20):
        history.append(
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"메시지 {index}",
            }
        )
    state = build_conversation_state(
        ChatRequest(user_id="kim", message="최신 질문"),
        SimpleNamespace(messages=history),
    )

    messages = message_dicts(state)
    assert len(messages) <= MAX_CONTEXT_MESSAGES
    assert messages[0]["role"] == "user"
    assert messages[-1] == {"role": "user", "content": "최신 질문"}


def test_conversation_state_redacts_secrets_before_any_model_receives_history():
    state = build_conversation_state(
        ChatRequest(user_id="kim", message="다시 알려줘"),
        SimpleNamespace(
            messages=[
                {"role": "user", "content": "api key: history-secret file:/srv/x"},
                {"role": "assistant", "content": "확인"},
            ]
        ),
    )

    serialized = str(message_dicts(state)).casefold()
    assert "history-secret" not in serialized
    assert "file:/srv" not in serialized


def test_conversation_state_uses_messages_as_the_canonical_history():
    limited = TurnRecord(
        user_content="장례식장 정보 알려줘",
        assistant_content="일반 선택 기준을 안내합니다.",
        execution=ExecutionMetadata(
            status="limited",
            failure_stage="retrieval",
            error_code="NO_EVIDENCE",
            include_in_llm_history=True,
        ),
    )

    state = build_conversation_state(
        ChatRequest(user_id="kim", message="강남역에서 제일 가까운 데는?"),
        SimpleNamespace(
            messages=[
                {"role": "user", "content": limited.user_content},
                {"role": "assistant", "content": limited.assistant_content},
            ],
            turns=[limited],
        ),
    )

    assert message_dicts(state) == [
        {"role": "user", "content": "장례식장 정보 알려줘"},
        {"role": "assistant", "content": "일반 선택 기준을 안내합니다."},
        {"role": "user", "content": "강남역에서 제일 가까운 데는?"},
    ]


def test_contextualization_failure_keeps_the_previous_user_subject():
    class BrokenLLM:
        async def complete_messages(self, system, messages):
            raise TimeoutError("contextualizer timed out")

    request = ChatRequest(user_id="kim", message="강남역에서 제일 가까운 데는?")
    conversation = SimpleNamespace(
        messages=[
            {"role": "user", "content": "장례식장 정보 알려줘"},
            {"role": "assistant", "content": "일반 선택 기준을 안내합니다."},
        ]
    )

    contextualized = asyncio.run(
        contextualize_request(BrokenLLM(), request, conversation)
    )

    assert contextualized.message == (
        "장례식장 정보 알려줘\n후속 요청: 강남역에서 제일 가까운 데는?"
    )


def test_prior_evidence_is_success_only_owner_scoped_and_deduplicated():
    def evidence(owner, document_id):
        return Evidence(
            evidence_id="S1",
            source_type="mail",
            document_id=document_id,
            title="보고",
            excerpt="수율 이슈",
            score=1,
            user_id=owner,
            acl_decision_id="acl",
            content_hash="hash",
        )

    good = evidence("kim", "mail-1")
    foreign = evidence("lee", "mail-2")
    conversation = SimpleNamespace(
        cited_evidence=[],
        turns=[
            TurnRecord(
                user_content="질문",
                assistant_content="답변 [S1]",
                execution=ExecutionMetadata(status="succeeded"),
                cited_evidence=[good],
            ),
            TurnRecord(
                user_content="실패 질문",
                assistant_content=None,
                execution=ExecutionMetadata(
                    status="failed", include_in_llm_history=False
                ),
                cited_evidence=[good, foreign],
            ),
        ],
    )

    selected = eligible_prior_evidence(
        conversation, PolicyContext.from_user_id("kim")
    )

    assert [item.document_id for item in selected] == ["mail-1"]
