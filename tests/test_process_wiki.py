import os

import pytest

import process_wiki
from agenda_extract import llm_connection


class PublishedRun:
    status = "published"

    @staticmethod
    def model_dump_json():
        return "{}"


def _use_shared_connection(observed):
    def process(**_kwargs):
        observed["connection"] = llm_connection()
        observed["ack"] = os.getenv("KNOWLEDGE_LLM_DATA_POLICY_ACK")
        return PublishedRun()

    return process


def test_loopback_cli_does_not_require_external_ack(monkeypatch):
    observed = {}
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "http://localhost:8000/v1")
    monkeypatch.delenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", raising=False)
    monkeypatch.setattr(process_wiki, "process_week", _use_shared_connection(observed))

    assert process_wiki.main(["--week", "2026-W30"]) == 0
    assert observed["connection"].base_url == "http://localhost:8000/v1"
    assert observed["ack"] is None


def test_external_cli_without_ack_is_rejected_by_shared_policy(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.delenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", raising=False)
    monkeypatch.setattr(process_wiki, "process_week", _use_shared_connection({}))

    with pytest.raises(RuntimeError, match="External LLM use requires"):
        process_wiki.main(["--week", "2026-W30"])


@pytest.mark.parametrize("previous", [None, "previous"])
def test_external_cli_flag_scopes_ack_to_call(monkeypatch, previous):
    observed = {}
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "https://llm.example/v1")
    if previous is None:
        monkeypatch.delenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", raising=False)
    else:
        monkeypatch.setenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", previous)
    monkeypatch.setattr(process_wiki, "process_week", _use_shared_connection(observed))

    assert process_wiki.main(
        ["--week", "2026-W30", "--allow-external-llm"]
    ) == 0
    assert observed["connection"].base_url == "https://llm.example/v1"
    assert observed["ack"] == "true"
    assert os.getenv("KNOWLEDGE_LLM_DATA_POLICY_ACK") == previous
