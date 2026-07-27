#!/usr/bin/env python3
"""Offline deterministic evaluation for multi-turn contextualization."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# The offline evaluator never opens MongoDB; avoid importing the optional driver.
motor_asyncio = ModuleType("motor.motor_asyncio")
motor_asyncio.AsyncIOMotorClient = object
sys.modules["motor.motor_asyncio"] = motor_asyncio

from rag_api_opensearch_v3 import (
    AIMessage,
    ConversationMemory,
    HumanMessage,
    fallback_contextualized_turn,
    route_after_contextualization,
)


DEFAULT_FIXTURE = ROOT / "fixtures" / "multiturn_eval.json"


def _messages(raw_messages: list[dict[str, str]]) -> list[Any]:
    message_types = {"user": HumanMessage, "assistant": AIMessage}
    return [
        message_types[item["role"]](content=item["content"])
        for item in raw_messages
        if item.get("role") in message_types
    ]


def evaluate_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    failures: list[dict[str, Any]] = []
    for case in cases:
        memory = ConversationMemory.model_validate(case.get("memory") or {})
        turn = fallback_contextualized_turn(
            case["question"], memory, _messages(case.get("history") or [])
        )
        actual = turn.model_dump()
        mismatches = {
            key: {"expected": value, "actual": actual.get(key)}
            for key, value in case["expected"].items()
            if actual.get(key) != value
        }
        expected_next = case.get("expected_next_route")
        if expected_next:
            next_route = route_after_contextualization(
                {
                    "follow_up_type": turn.follow_up_type,
                    "conversation_memory": memory.model_dump(),
                }
            )
            if next_route != expected_next:
                mismatches["next_route"] = {
                    "expected": expected_next,
                    "actual": next_route,
                }
        if mismatches:
            failures.append({"id": case["id"], "mismatches": mismatches})

    total = len(cases)
    passed = total - len(failures)
    return {
        "total": total,
        "passed": passed,
        "accuracy": round(passed / total, 4) if total else 0.0,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", nargs="?", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--threshold", type=float, default=0.9)
    args = parser.parse_args()

    cases = json.loads(args.fixture.read_text(encoding="utf-8"))
    summary = evaluate_cases(cases)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["accuracy"] >= args.threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
