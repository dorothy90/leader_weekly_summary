from pathlib import Path

from app.llm import agentic
from app.llm.prompts import (
    ANSWER_SYSTEM_PROMPT,
    INTENT_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    PLANNER_SYSTEM_PROMPT,
)


def test_multi_source_semantic_prompts_live_in_dedicated_module():
    agentic_source = Path("app/llm/agentic.py").read_text(encoding="utf-8")

    assert "INTENT_SYSTEM_PROMPT =" not in agentic_source
    assert "PLANNER_SYSTEM_PROMPT =" not in agentic_source
    assert "JUDGE_SYSTEM_PROMPT =" not in agentic_source
    assert "ANSWER_SYSTEM_PROMPT =" not in agentic_source
    assert "from app.llm.prompts import" in agentic_source


def test_multi_source_prompts_keep_all_four_semantic_contracts():
    assert "IntentDecision" in INTENT_SYSTEM_PROMPT
    assert "다음에 실행할 도구 하나" in PLANNER_SYSTEM_PROMPT
    assert "현재 검색 근거가 충분한지" in JUDGE_SYSTEM_PROMPT
    assert "[S1], [S2]" in ANSWER_SYSTEM_PROMPT
    assert agentic.INTENT_SYSTEM_PROMPT is INTENT_SYSTEM_PROMPT
    assert agentic.PLANNER_SYSTEM_PROMPT is PLANNER_SYSTEM_PROMPT
    assert agentic.JUDGE_SYSTEM_PROMPT is JUDGE_SYSTEM_PROMPT
    assert agentic.ANSWER_SYSTEM_PROMPT is ANSWER_SYSTEM_PROMPT


def test_multi_source_prompts_treat_conversation_history_as_context_not_evidence():
    prompts = (
        INTENT_SYSTEM_PROMPT,
        PLANNER_SYSTEM_PROMPT,
        JUDGE_SYSTEM_PROMPT,
        ANSWER_SYSTEM_PROMPT,
    )

    assert all("conversation_history" in prompt for prompt in prompts)
    assert "검색 근거로 취급하지 마세요" in ANSWER_SYSTEM_PROMPT
