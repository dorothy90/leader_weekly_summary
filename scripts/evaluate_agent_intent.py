"""Real-model evaluation gate for typed agent intent paraphrases."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
from typing import Any

from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config.settings import Settings
from app.domain.agentic import QueryAnalysis, SourceName, TimeScope
from app.llm.agentic import AgentAnalyzer, StructuredAgentModel
from app.llm.gateway import OpenAILLMGateway
from app.persistence.conversations import ConversationMemory


DEFAULT_DATASET_PATH = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "datasets"
    / "agent_intent_paraphrases.json"
)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )

    question: str = Field(min_length=1, max_length=4000)
    expected_sources: list[SourceName] = Field(min_length=1, max_length=3)
    expected_time_scope: TimeScope
    calendar_detail_required: StrictBool


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cases: list[EvaluationCase] = Field(min_length=1)


def load_dataset(path: str | Path) -> EvaluationDataset:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return EvaluationDataset.model_validate(payload)


def compare_case(
    expected: EvaluationCase,
    actual: QueryAnalysis,
) -> list[str]:
    failures = []
    actual_sources = [item.source for item in actual.source_requests]
    if actual.analysis_status != "ready":
        failures.append(f"analysis_status: {actual.analysis_status}")
    if actual_sources != expected.expected_sources:
        failures.append(
            f"sources: expected {expected.expected_sources}, got {actual_sources}"
        )
    if actual.time_scope != expected.expected_time_scope:
        failures.append(
            "time_scope: expected "
            f"{expected.expected_time_scope}, got {actual.time_scope}"
        )
    if actual.calendar_detail_required != expected.calendar_detail_required:
        failures.append(
            "calendar_detail_required: expected "
            f"{expected.calendar_detail_required}, "
            f"got {actual.calendar_detail_required}"
        )
    return failures


def _print_json(record: dict[str, Any]) -> None:
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


async def run_evaluation(
    dataset: EvaluationDataset,
    analyzer: AgentAnalyzer,
    timezone_name: str,
) -> int:
    passed = 0
    for index, case in enumerate(dataset.cases, start=1):
        try:
            actual = await analyzer.analyze(
                case.question,
                ConversationMemory(),
                timezone_name,
            )
            failures = compare_case(case, actual)
        except Exception:
            failures = ["model_failure"]
        if failures:
            _print_json(
                {
                    "case": index,
                    "status": "FAIL",
                    "failures": failures,
                }
            )
        else:
            passed += 1
            _print_json({"case": index, "status": "PASS"})
    total = len(dataset.cases)
    _print_json(
        {
            "passed": passed,
            "total": total,
            "summary": f"{passed}/{total} passed",
        }
    )
    return 0 if passed == total else 1


def _build_analyzer(settings: Settings) -> StructuredAgentModel:
    endpoint = settings.resolve_llm_endpoint()
    gateway = OpenAILLMGateway(
        AsyncOpenAI(
            api_key=endpoint.api_key.get_secret_value(),
            base_url=endpoint.base_url,
            timeout=endpoint.timeout_seconds,
        ),
        endpoint.model,
    )
    return StructuredAgentModel(
        gateway,
        timeout_seconds=endpoint.timeout_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate typed agent intent paraphrases with the real model."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    args = parser.parse_args(argv)

    try:
        settings = Settings.from_env()
        endpoint = settings.resolve_llm_endpoint()
    except Exception:
        print('{"error":"settings_invalid"}', file=sys.stderr)
        return 1
    if not endpoint.api_key.get_secret_value().strip():
        print("OPENROUTER_API_KEY is required", file=sys.stderr)
        return 2
    try:
        dataset = load_dataset(args.dataset)
        analyzer = _build_analyzer(settings)
    except (OSError, json.JSONDecodeError, ValidationError):
        print('{"error":"dataset_invalid"}', file=sys.stderr)
        return 1
    except Exception:
        print('{"error":"model_setup_failure"}', file=sys.stderr)
        return 1
    return asyncio.run(
        run_evaluation(dataset, analyzer, settings.default_user_timezone)
    )


if __name__ == "__main__":
    raise SystemExit(main())
