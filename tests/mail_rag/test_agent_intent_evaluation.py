import asyncio
from datetime import UTC, datetime
import json

import pytest
from pydantic import ValidationError

from app.config.settings import Settings
from app.domain.agentic import IntentDecision, QueryAnalysis, SourceRequest
from scripts.evaluate_agent_intent import (
    DEFAULT_DATASET_PATH,
    EvaluationCase,
    EvaluationDataset,
    compare_case,
    load_dataset,
    main,
    run_evaluation,
)


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def analysis_for(
    *sources: str,
    time_scope: str = "current_week",
    detail: bool = False,
) -> QueryAnalysis:
    return QueryAnalysis.from_intent(
        IntentDecision(
            intent="이번 주 일정 확인",
            source_requests=[
                SourceRequest(source=source, query="일정") for source in sources
            ],
            time_scope=time_scope,
            calendar_detail_required=detail,
        ),
        now=NOW,
    )


class FakeAnalyzer:
    def __init__(self, analyses):
        self.analyses = iter(analyses)
        self.memories = []

    async def analyze(self, question, memory, timezone_name):
        self.memories.append(memory)
        result = next(self.analyses)
        if isinstance(result, Exception):
            raise result
        return result


def test_default_dataset_has_the_four_required_paraphrases():
    dataset = load_dataset(DEFAULT_DATASET_PATH)

    assert [case.question for case in dataset.cases] == [
        "이번주 일정알려줘",
        "이번주 일정 뭐야?",
        "이번주 일정이 뭔지 알려줘",
        "이번 주 스케줄 보여줘",
    ]
    assert all(case.expected_sources == ["calendar"] for case in dataset.cases)
    assert all(case.expected_time_scope == "current_week" for case in dataset.cases)
    assert all(case.calendar_detail_required is False for case in dataset.cases)


def test_dataset_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(
            {
                "cases": [
                    {
                        "question": "이번주 일정 뭐야?",
                        "expected_sources": ["calendar"],
                        "expected_time_scope": "current_week",
                        "calendar_detail_required": False,
                        "unexpected": "not allowed",
                    }
                ]
            }
        )


def test_compare_rejects_extra_source():
    expected = EvaluationCase(
        question="이번주 일정 뭐야?",
        expected_sources=["calendar"],
        expected_time_scope="current_week",
        calendar_detail_required=False,
    )
    actual = QueryAnalysis.from_intent(
        IntentDecision(
            intent="wrong",
            source_requests=[
                SourceRequest(source="calendar", query="일정"),
                SourceRequest(source="domain_knowledge", query="일정 의미"),
            ],
            time_scope="current_week",
        ),
        now=NOW,
    )

    assert compare_case(expected, actual) == [
        "sources: expected ['calendar'], got ['calendar', 'domain_knowledge']"
    ]


def test_compare_reports_unavailable_time_and_detail_mismatches():
    expected = EvaluationCase(
        question="이번주 일정 뭐야?",
        expected_sources=["calendar"],
        expected_time_scope="current_week",
        calendar_detail_required=True,
    )

    assert compare_case(expected, QueryAnalysis.unavailable()) == [
        "analysis_status: unavailable",
        "sources: expected ['calendar'], got []",
        "time_scope: expected current_week, got none",
        "calendar_detail_required: expected True, got False",
    ]


def test_run_evaluation_prints_safe_json_and_uses_fresh_memory(capsys):
    dataset = EvaluationDataset(
        cases=[
            EvaluationCase(
                question="first-private-question",
                expected_sources=["calendar"],
                expected_time_scope="current_week",
                calendar_detail_required=False,
            ),
            EvaluationCase(
                question="second-private-question",
                expected_sources=["calendar"],
                expected_time_scope="current_week",
                calendar_detail_required=False,
            ),
        ]
    )
    analyzer = FakeAnalyzer([analysis_for("calendar"), analysis_for("calendar")])

    exit_code = asyncio.run(run_evaluation(dataset, analyzer, "Asia/Seoul"))

    output = capsys.readouterr().out
    records = [json.loads(line) for line in output.splitlines()]
    assert exit_code == 0
    assert records == [
        {"case": 1, "status": "PASS"},
        {"case": 2, "status": "PASS"},
        {"passed": 2, "total": 2, "summary": "2/2 passed"},
    ]
    assert len(analyzer.memories) == 2
    assert analyzer.memories[0] is not analyzer.memories[1]
    assert "private-question" not in output


def test_run_evaluation_hides_model_failure_details(capsys):
    dataset = EvaluationDataset(
        cases=[
            EvaluationCase(
                question="private-question",
                expected_sources=["calendar"],
                expected_time_scope="current_week",
                calendar_detail_required=False,
            )
        ]
    )
    analyzer = FakeAnalyzer([RuntimeError("secret provider response")])

    exit_code = asyncio.run(run_evaluation(dataset, analyzer, "Asia/Seoul"))

    output = capsys.readouterr().out
    assert exit_code == 1
    assert [json.loads(line) for line in output.splitlines()] == [
        {"case": 1, "status": "FAIL", "failures": ["model_failure"]},
        {"passed": 0, "total": 1, "summary": "0/1 passed"},
    ]
    assert "secret" not in output
    assert "provider" not in output
    assert "Traceback" not in output


def test_evaluator_requires_configured_model_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    def empty_settings(cls):
        return cls(openrouter_api_key="")

    monkeypatch.setattr(Settings, "from_env", classmethod(empty_settings))

    assert main([]) == 2
    assert "OPENROUTER_API_KEY" in capsys.readouterr().err


def test_evaluator_hides_settings_failure_details(monkeypatch, capsys):
    def fail_settings(cls):
        raise RuntimeError("secret invalid setting")

    monkeypatch.setattr(Settings, "from_env", classmethod(fail_settings))

    assert main([]) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.err) == {"error": "settings_invalid"}
    assert "secret" not in captured.err
    assert "Traceback" not in captured.err
