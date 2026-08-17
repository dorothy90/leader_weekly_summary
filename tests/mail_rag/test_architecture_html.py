from html.parser import HTMLParser
from pathlib import Path
import re


ARTIFACT = Path("MultiSource_Agentic_RAG_Architecture.html")
REQUIRED_SECTIONS = {
    "overview",
    "system-map",
    "llm-rail",
    "responsibility-boundary",
    "multi-index",
    "multi-turn",
    "verified-trace",
    "limitations",
}


class ArchitectureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.stages = []
        self.external_resources = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if values.get("data-stage"):
            self.stages.append(values["data-stage"])
        if tag == "button":
            self.buttons.append(values)
        resource = values.get("src") or values.get("href")
        if resource and re.match(r"https?://", resource):
            self.external_resources.append(resource)


def artifact_text():
    return ARTIFACT.read_text(encoding="utf-8")


def parsed_artifact():
    parser = ArchitectureParser()
    parser.feed(artifact_text())
    return parser


def test_architecture_html_contains_complete_current_system_contract():
    parser = parsed_artifact()
    text = artifact_text()

    assert REQUIRED_SECTIONS <= parser.ids
    assert parser.stages == ["routing", "planner", "judge", "answer"]
    for required in (
        "POST /v1/chat",
        "IntentDecision",
        "PlanningDecision",
        "JudgeDecision",
        "AnswerDecision",
        "search_domain_knowledge",
        "search_mail",
        "search_calendar",
        "expand_calendar_event",
        "routing → planner → judge → answer",
        "전체 메시지 기록",
        "의미적 뒷받침",
    ):
        assert required in text


def test_architecture_html_is_offline_and_contains_no_secret_examples():
    parser = parsed_artifact()
    text = artifact_text()

    assert parser.external_resources == []
    assert not re.search(
        r"(?i)(api[_-]?key|password)\s*[:=]\s*['\"][^'\"]+",
        text,
    )
    assert "x-trace-id" not in text
    assert "mongodb://" not in text
    assert "@example.com" not in text
