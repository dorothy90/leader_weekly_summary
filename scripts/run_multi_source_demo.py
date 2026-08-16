from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.dependencies import build_demo_container
from app.domain.chat import ChatRequest
from app.domain.policy import PolicyContext


DEFAULT_QUESTION = (
    "김OO이 지난주 메일에서 이야기한 NAND 수율 문제가 어떤 회의에서 "
    "논의됐고 회의에서 어떤 Action을 하기로 했으며 기술적으로 어떤 "
    "의미인지 설명해줘."
)
CANONICAL_TOOLS = [
    "search_mail",
    "search_calendar",
    "expand_calendar_event",
    "search_domain_knowledge",
]
_SAFE_ERROR = "요청을 처리할 수 없습니다."


async def run(question: str, user_id: str) -> int:
    container = build_demo_container()
    request = ChatRequest(
        user_id=user_id,
        message=question,
        response_mode="fast",
    )
    result = await container.fast.invoke(
        request,
        PolicyContext.from_user_id(user_id),
        None,
    )
    print(result.answer)
    print("\nSources:", ", ".join(item.source_type for item in result.evidence))
    tool_calls = list(result.agent_trace.tool_calls) if result.agent_trace else []
    if result.agent_trace:
        print("Tool calls:", " -> ".join(tool_calls))
        print("Iterations:", result.agent_trace.iteration_count)
    required = {"mail", "calendar", "domain_knowledge"}
    actual = {item.source_type for item in result.evidence}
    complete = bool(
        required <= actual
        and result.quality.citation_valid
        and not result.quality.limited_answer
        and result.execution is not None
        and result.execution.status == "succeeded"
        and tool_calls == CANONICAL_TOOLS
    )
    return 0 if complete else 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--user-id", default="kim")
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.question, args.user_id))
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        print(_SAFE_ERROR, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
