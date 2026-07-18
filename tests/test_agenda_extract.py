from __future__ import annotations

from pathlib import Path

import pytest

from agenda_extract import (
    AgendaDraft,
    AgendaDraftList,
    CanonicalResolver,
    extract_mail,
    llm_connection,
    prior_sentence_context,
    strip_quoted_history,
)
from knowledge_models import (
    AliasRecord,
    CategoryPath,
    MailDocument,
    TaxonomyDocument,
)
from knowledge_store import SQLiteKnowledgeStore


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "knowledge"


def resolver() -> CanonicalResolver:
    taxonomy = TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )
    return CanonicalResolver(taxonomy)


def test_strip_quoted_history_excludes_original_message():
    body = "신규 조치입니다.\n\n----- Original Message -----\n이전 내용입니다."

    active, quoted = strip_quoted_history(body)

    assert active == "신규 조치입니다."
    assert "Original Message" in quoted


def test_group_alias_resolves_to_multiple_lotcds():
    resolved = resolver().resolve("SP LPDDR5 24G 제품군 Edge defect 증가")

    assert resolved.scope == "multi_lotcd"
    assert {path.lotcd for path in resolved.target_paths} == {"4SA", "6SA"}


def test_unknown_lotcd_routes_to_review():
    resolved = resolver().resolve("DRAM 신규 LOTCD 4ZZ 수율 하락", "DRAM수율전략")

    assert resolved.scope == "unknown"
    assert resolved.review_required is True
    assert resolved.candidate_paths[0].lotcd == "4ZZ"


def test_prior_sentence_context_resolves_coreference():
    body = "DRAM 4SA는 화요일, NAND 4H1은 수요일에 작업합니다. 작업 시간에는 해당 LOT 투입을 중지해 주세요."
    quote = "작업 시간에는 해당 LOT 투입을 중지해 주세요."

    context = prior_sentence_context(body, quote)
    resolved = resolver().resolve(context)

    assert {path.lotcd for path in resolved.target_paths} == {"4SA", "4H1"}


def test_extraction_upsert_is_idempotent_and_preserves_review(tmp_path):
    taxonomy = TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )
    mails = MailDocument.model_validate_json(
        (FIXTURES / "mails.json").read_text(encoding="utf-8")
    ).mails
    mail = next(item for item in mails if item.id == "dummy_mail_002")
    quote = "4SA는 장비 조건 변경 이후 수율이 1.2%p 하락해 원인 분석 중입니다."

    def splitter(_text: str) -> AgendaDraftList:
        return AgendaDraftList(
            agendas=[
                AgendaDraft(
                    source_quote=quote,
                    classification_context=quote,
                    summary="4SA 수율 하락",
                    topic="yield",
                    state="investigating",
                    item_kind="lotcd_specific",
                )
            ]
        )

    result = extract_mail(mail, splitter, CanonicalResolver(taxonomy))
    store = SQLiteKnowledgeStore(tmp_path / "knowledge.db")

    first = store.save_extraction(mail, result)
    second = store.save_extraction(mail, result)
    assert first["inserted"] == 1
    assert second["updated"] == 1
    assert len([item for item in store.agendas if item.mail_id == mail.id]) == 1
    assert store.pending_search_sync() == [mail.id]

    agenda_id = result.agendas[0].id
    store.update_classification(
        agenda_id=agenda_id,
        target_paths=result.agendas[0].target_paths,
        review_status="confirmed",
        changed_by="reviewer",
    )
    third = store.save_extraction(mail, result)
    assert third["preserved"] == 1


def test_extract_mail_keeps_aggregate_without_targets():
    taxonomy = TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )
    mails = MailDocument.model_validate_json(
        (FIXTURES / "mails.json").read_text(encoding="utf-8")
    ).mails
    mail = next(item for item in mails if item.id == "dummy_mail_002")
    quote = "Spica 전체 수율은 전주 수준을 유지했습니다."

    def splitter(_text: str) -> AgendaDraftList:
        return AgendaDraftList(
            agendas=[
                AgendaDraft(
                    source_quote=quote,
                    classification_context=quote,
                    summary="Spica 전체 수율 유지",
                    topic="yield",
                    state="stable",
                    item_kind="aggregate",
                )
            ]
        )

    result = extract_mail(mail, splitter, CanonicalResolver(taxonomy))

    assert result.agendas[0].item_kind == "aggregate"
    assert result.agendas[0].decision.status == "aggregate"
    assert result.agendas[0].target_paths == []


def test_extract_mail_applies_contextual_alias_via_sender_team():
    taxonomy = TaxonomyDocument.model_validate_json(
        (FIXTURES / "taxonomy.json").read_text(encoding="utf-8")
    )
    mail = MailDocument.model_validate_json(
        (FIXTURES / "mails.json").read_text(encoding="utf-8")
    ).mails[0].model_copy(
        update={"body": "legacy edge defect", "sender_team": "Spica team"}
    )
    alias = AliasRecord(
        id=101,
        value="legacy edge",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        context_tech="Spica",
    )

    def splitter(_text: str) -> AgendaDraftList:
        return AgendaDraftList(
            agendas=[
                AgendaDraft(
                    source_quote=mail.body,
                    classification_context=mail.body,
                    summary="legacy edge defect",
                    topic="quality",
                    state="investigating",
                    item_kind="lotcd_specific",
                )
            ]
        )

    result = extract_mail(mail, splitter, CanonicalResolver(taxonomy, [alias]))

    assert result.agendas[0].decision.target_path.lotcd == "4SA"


def test_external_knowledge_llm_configuration_is_allowed_with_ack(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "http://internal-llm.example/v1")
    monkeypatch.setenv("KNOWLEDGE_LLM_API_KEY", "internal-secret")
    monkeypatch.setenv("KNOWLEDGE_LLM_MODEL", "internal-model")
    monkeypatch.setenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", "TrUe")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.example/v1")

    connection = llm_connection()

    assert connection.base_url == "http://internal-llm.example/v1"
    assert connection.model == "internal-model"
    assert connection.api_key.get_secret_value() == "internal-secret"
    assert "internal-secret" not in repr(connection)


def test_external_knowledge_llm_configuration_requires_data_policy_ack(monkeypatch):
    monkeypatch.setenv(
        "KNOWLEDGE_LLM_BASE_URL", "https://internal-llm.example/v1"
    )
    monkeypatch.delenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", raising=False)

    with pytest.raises(RuntimeError, match="KNOWLEDGE_LLM_DATA_POLICY_ACK=true"):
        llm_connection()


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
    ],
)
def test_loopback_llm_configuration_does_not_require_ack(monkeypatch, base_url):
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", base_url)
    monkeypatch.delenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", raising=False)

    assert llm_connection().base_url == base_url


def test_knowledge_llm_uses_glm_default_model(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "http://localhost:8000/v1")

    connection = llm_connection()

    assert connection.model == "z-ai/glm-4.7-flash"


def test_openrouter_llm_configuration_requires_data_policy_ack(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("KNOWLEDGE_LLM_DATA_POLICY_ACK", raising=False)
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.example/v1")

    with pytest.raises(RuntimeError, match="KNOWLEDGE_LLM_DATA_POLICY_ACK=true"):
        llm_connection()
