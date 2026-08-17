from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path
import sys

import httpx

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.dependencies import build_demo_container
from app.api.main import create_app
from app.config.settings import Settings
from app.llm.demo_scenarios import (
    ScenarioName,
    StaticIntentAnalyzer,
    scenario_decisions,
)


DEFAULT_QUESTION = (
    "김OO이 지난주 메일에서 이야기한 NAND 수율 문제가 어떤 회의에서 "
    "논의됐고 회의에서 어떤 Action을 하기로 했으며 기술적으로 어떤 "
    "의미인지 설명해줘."
)
SCENARIO_QUESTIONS: dict[ScenarioName, tuple[str, ...]] = {
    "canonical": (DEFAULT_QUESTION,),
    "weekly-calendar": ("이번주 일정 뭐야?",),
    "event-action": ("2026-08-07 NAND Yield Review 회의에서 Action 뭐였어?",),
    "followup": (
        "NAND Yield Review 회의 찾아줘",
        "그 회의에서 Action 뭐였어?",
    ),
}
CANONICAL_TOOLS = [
    "search_mail",
    "search_calendar",
    "expand_calendar_event",
    "search_domain_knowledge",
]
_SAFE_ERROR = "요청을 처리할 수 없습니다."
_MISSING_LLM_KEY = (
    "자유 형식 데모 질문에는 OPENROUTER_API_KEY 설정이 필요합니다."
)
_SCENARIO_NOW: dict[ScenarioName, datetime] = {
    "canonical": datetime(2026, 8, 16, 12, tzinfo=UTC),
    "weekly-calendar": datetime(2026, 8, 17, 0, tzinfo=UTC),
    "event-action": datetime(2026, 8, 17, 0, tzinfo=UTC),
    "followup": datetime(2026, 8, 17, 0, tzinfo=UTC),
}


async def _post(app, payload):
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://demo",
    ) as client:
        return await client.post("/v1/chat", json=payload)


def _print_response(body, tool_calls):
    print(body["answer"])
    print(
        "\nSources:",
        ", ".join(item["source_type"] for item in body["references"]),
    )
    print("Tool calls:", " -> ".join(tool_calls))
    print("Iterations:", len(tool_calls))


def _complete(body) -> bool:
    quality = body.get("quality") or {}
    execution = body.get("execution") or {}
    return bool(
        body.get("references")
        and quality.get("citation_valid")
        and not quality.get("limited_answer")
        and execution.get("status") == "succeeded"
    )


async def run(
    question: str,
    user_id: str,
    *,
    offline_scenario: ScenarioName | None = None,
    settings: Settings | None = None,
) -> int:
    current = settings or Settings.from_env()
    analyzer = (
        StaticIntentAnalyzer(
            scenario_decisions(offline_scenario),
            now=_SCENARIO_NOW[offline_scenario],
        )
        if offline_scenario is not None
        else None
    )
    container = build_demo_container(current, agent_model=analyzer)
    app = create_app(container)
    questions = (
        SCENARIO_QUESTIONS[offline_scenario]
        if offline_scenario is not None
        else (question,)
    )

    bodies = []
    conversation_id = None
    turn_two_start = 0
    for turn, display_question in enumerate(questions, start=1):
        if turn == 2:
            turn_two_start = len(container.fast.agentic.search.calls)
        payload = {
            "user_id": user_id,
            "message": display_question,
            "response_mode": "fast",
        }
        if conversation_id is not None:
            payload["conversation_id"] = conversation_id
        response = await _post(app, payload)
        if response.status_code != 200:
            print(_SAFE_ERROR, file=sys.stderr)
            return 1
        body = response.json()
        bodies.append(body)
        if conversation_id is None:
            conversation_id = body["conversation_id"]
        elif body["conversation_id"] != conversation_id:
            return 1

    tool_calls = [
        action.tool for action, _owner in container.fast.agentic.search.calls
    ]
    _print_response(bodies[-1], tool_calls)
    complete = all(_complete(body) for body in bodies)

    if offline_scenario == "canonical":
        required = {"mail", "calendar", "domain_knowledge"}
        actual = {
            item["source_type"] for item in bodies[-1]["references"]
        }
        complete = complete and required <= actual and tool_calls == CANONICAL_TOOLS
    elif offline_scenario == "weekly-calendar":
        complete = complete and tool_calls == ["search_calendar"]
    elif offline_scenario == "event-action":
        complete = complete and tool_calls == [
            "search_calendar",
            "expand_calendar_event",
        ]
    elif offline_scenario == "followup":
        turn_two_tools = tool_calls[turn_two_start:]
        complete = complete and turn_two_tools == ["expand_calendar_event"]
    return 0 if complete else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?")
    parser.add_argument("--user-id", default="kim")
    parser.add_argument(
        "--offline-scenario",
        choices=(
            "canonical",
            "weekly-calendar",
            "event-action",
            "followup",
        ),
    )
    args = parser.parse_args()
    if args.question is not None and args.offline_scenario is not None:
        parser.error("question and --offline-scenario are mutually exclusive")

    settings = Settings.from_env()
    offline_scenario = args.offline_scenario
    question = args.question
    if question is None and offline_scenario is None:
        offline_scenario = "canonical"
        question = DEFAULT_QUESTION
    elif offline_scenario is not None:
        question = SCENARIO_QUESTIONS[offline_scenario][0]
    elif not settings.openrouter_api_key.get_secret_value().strip():
        print(_MISSING_LLM_KEY, file=sys.stderr)
        return 2

    try:
        return asyncio.run(
            run(
                question,
                args.user_id,
                offline_scenario=offline_scenario,
                settings=settings,
            )
        )
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        print(_SAFE_ERROR, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
