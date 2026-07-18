"""Derived OpenSearch index for confirmed agenda search."""

from __future__ import annotations

import os
import re

from opensearchpy import OpenSearch, helpers

from knowledge_store import SQLiteKnowledgeStore


AGENDA_INDEX_NAME = os.getenv("AGENDA_INDEX_NAME", "mail_agendas")


def index_definition() -> dict:
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "5s",
            "analysis": {
                "tokenizer": {
                    "knowledge_nori": {
                        "type": "nori_tokenizer",
                        "decompound_mode": "mixed",
                    }
                },
                "analyzer": {
                    "knowledge_korean": {
                        "type": "custom",
                        "tokenizer": "knowledge_nori",
                        "filter": ["nori_readingform", "lowercase"],
                    }
                },
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "text": {"type": "text", "analyzer": "knowledge_korean"},
                "summary": {"type": "text", "analyzer": "knowledge_korean"},
                "source_quote": {"type": "text", "analyzer": "knowledge_korean"},
                "agenda_id": {"type": "keyword"},
                "mail_id": {"type": "keyword"},
                "subject": {
                    "type": "text",
                    "analyzer": "knowledge_korean",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "sender_team": {"type": "keyword"},
                "received_at": {"type": "date"},
                "scope": {"type": "keyword"},
                "topic": {"type": "keyword"},
                "state": {"type": "keyword"},
                "confidence": {"type": "float"},
                "review_status": {"type": "keyword"},
                "decision_status": {"type": "keyword"},
                "domains": {"type": "keyword"},
                "techs": {"type": "keyword"},
                "lotcds": {"type": "keyword"},
                "target_paths": {
                    "type": "nested",
                    "properties": {
                        "domain": {"type": "keyword"},
                        "tech": {"type": "keyword"},
                        "lotcd": {"type": "keyword"},
                    },
                },
            },
        },
    }


def ensure_index(
    client: OpenSearch, index_name: str = AGENDA_INDEX_NAME
) -> None:
    if client.indices.exists(index=index_name):
        client.indices.put_mapping(
            index=index_name,
            body={"properties": {"decision_status": {"type": "keyword"}}},
        )
    else:
        client.indices.create(index=index_name, body=index_definition())


def build_documents(store: SQLiteKnowledgeStore, mail_id: str) -> list[dict]:
    with store._connect() as connection:
        trace_rows = connection.execute(
            """
            SELECT
                trace.agenda_id,
                trace.decision_status,
                trace.run_id = week.active_run_id AS is_active
            FROM classification_trace trace
            JOIN classification_run run ON run.id = trace.run_id
            LEFT JOIN week_classification week ON week.week = run.week
            JOIN agenda ON agenda.id = trace.agenda_id
            WHERE agenda.mail_id = ?
            """,
            (mail_id,),
        ).fetchall()
    traced_agendas = {row["agenda_id"] for row in trace_rows}
    active_decisions = {
        row["agenda_id"]: row["decision_status"]
        for row in trace_rows
        if row["is_active"]
    }

    documents = []
    for agenda in store.agendas:
        if agenda.mail_id != mail_id:
            continue
        view = store.agenda_view(agenda)
        if view.id in traced_agendas:
            decision_status = active_decisions.get(view.id)
            if decision_status not in {"confirmed", "manually_corrected"}:
                continue
            if len(view.target_paths) != 1 or not view.target_paths[0].lotcd:
                continue
        else:
            if view.review_status != "confirmed":
                continue
            decision_status = "confirmed"
        paths = [path.model_dump() for path in view.target_paths]
        documents.append(
            {
                "agenda_id": view.id,
                "mail_id": view.mail_id,
                "text": f"{view.summary}\n{view.source_quote}",
                "summary": view.summary,
                "source_quote": view.source_quote,
                "subject": view.subject,
                "sender_team": view.sender_team,
                "received_at": view.received_at.isoformat(),
                "scope": view.scope,
                "topic": view.topic,
                "state": view.state,
                "confidence": view.confidence,
                "review_status": view.review_status,
                "decision_status": decision_status,
                "domains": sorted({path.domain for path in view.target_paths}),
                "techs": sorted(
                    {path.tech for path in view.target_paths if path.tech}
                ),
                "lotcds": sorted(
                    {path.lotcd for path in view.target_paths if path.lotcd}
                ),
                "target_paths": paths,
            }
        )
    return documents


def sync_mail(
    client: OpenSearch,
    store: SQLiteKnowledgeStore,
    mail_id: str,
    index_name: str = AGENDA_INDEX_NAME,
) -> int:
    ensure_index(client, index_name)
    client.delete_by_query(
        index=index_name,
        body={"query": {"term": {"mail_id": mail_id}}},
        refresh=False,
        conflicts="proceed",
    )
    documents = build_documents(store, mail_id)
    if documents:
        helpers.bulk(
            client,
            [
                {
                    "_index": index_name,
                    "_id": document["agenda_id"],
                    "_source": document,
                }
                for document in documents
            ],
        )
    client.indices.refresh(index=index_name)
    return len(documents)


def sync_pending(
    client: OpenSearch,
    store: SQLiteKnowledgeStore,
    index_name: str = AGENDA_INDEX_NAME,
) -> dict:
    synced_mails = 0
    indexed_agendas = 0
    failed_mails: list[str] = []
    for mail_id in store.pending_search_sync():
        try:
            indexed_agendas += sync_mail(client, store, mail_id, index_name)
            store.complete_search_sync(mail_id)
            synced_mails += 1
        except Exception:
            failed_mails.append(mail_id)
    return {
        "synced_mails": synced_mails,
        "indexed_agendas": indexed_agendas,
        "failed_mails": failed_mails,
    }


def search_agenda_ids(
    client: OpenSearch,
    query: str,
    index_name: str = AGENDA_INDEX_NAME,
    limit: int = 500,
) -> list[str]:
    normalized_query = query.strip()
    if re.fullmatch(r"[46][A-Z][A-Z0-9]{1,4}", normalized_query.upper()):
        search_query = {"term": {"lotcds": normalized_query.upper()}}
    else:
        search_query = {
            "multi_match": {
                "query": normalized_query,
                "fields": ["summary^3", "source_quote^2", "subject", "text"],
                "type": "best_fields",
                "operator": "and",
            }
        }
    response = client.search(
        index=index_name,
        body={
            "size": limit,
            "query": search_query,
            "sort": ["_score", {"received_at": "desc"}, {"agenda_id": "asc"}],
            "_source": ["agenda_id"],
        },
    )
    return [hit["_source"]["agenda_id"] for hit in response["hits"]["hits"]]
