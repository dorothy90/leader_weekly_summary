from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from opensearchpy.helpers import bulk

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.dependencies import build_embedding_gateway, build_opensearch_client
from app.config.settings import Settings


FIXTURE = Path("fixtures/multi_source_demo/corpus.json")
INDEX_SUFFIX = "dummy-202608-v1"
METADATA_FIELDS = {
    "attachment_id",
    "attachment_name",
    "attendee_emails",
    "calendar_item_id",
    "chunk_index",
    "end_at_utc",
    "organizer_email",
    "sender_email",
    "start_at_utc",
    "timezone",
}
SOURCE_FIELDS = {
    "content_kind",
    "employee_id",
    "is_active",
    "is_cancelled",
    "mail_type",
    "parent_event_id",
    "source_id",
    "source_type",
    "team",
    "text",
    "title",
    "week",
}


def build_index_definition(dimension: int) -> dict:
    keyword_fields = (
        "attachment_id",
        "attachment_name",
        "attendee_emails",
        "calendar_item_id",
        "content_kind",
        "employee_id",
        "mail_type",
        "occurrence_id",
        "organizer_email",
        "parent_event_id",
        "sender_email",
        "series_master_id",
        "source_id",
        "source_type",
        "team",
        "timezone",
        "week",
    )
    properties = {name: {"type": "keyword"} for name in keyword_fields}
    properties.update(
        {
            "is_active": {"type": "boolean"},
            "is_cancelled": {"type": "boolean"},
            "chunk_index": {"type": "integer"},
            "subject": {"type": "text"},
            "title": {"type": "text"},
            "location": {"type": "text"},
            "text": {"type": "text"},
            "received_at": {"type": "date"},
            "sent_at": {"type": "date"},
            "modified_at": {"type": "date"},
            "start_at_utc": {"type": "date"},
            "end_at_utc": {"type": "date"},
            "embedding": {
                "type": "knn_vector",
                "dimension": dimension,
                "method": {
                    "name": "hnsw",
                    "space_type": "cosinesimil",
                    "engine": "faiss",
                    "parameters": {"ef_construction": 128, "m": 16},
                },
            },
        }
    )
    return {
        "settings": {
            "index": {
                "knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,
            }
        },
        "mappings": {"dynamic": "strict", "properties": properties},
    }


def build_index_document(document: dict, embedding: list[float]) -> dict:
    indexed = {
        key: document[key]
        for key in SOURCE_FIELDS
        if document.get(key) is not None
    }
    metadata = document.get("metadata")
    if isinstance(metadata, dict):
        indexed.update(
            {
                key: metadata[key]
                for key in METADATA_FIELDS
                if metadata.get(key) is not None
            }
        )
    if document.get("source_type") == "mail" and document.get("occurred_at"):
        indexed["received_at"] = document["occurred_at"]
    if document.get("source_type") == "calendar" and document.get("title"):
        indexed["subject"] = document["title"]
    indexed["embedding"] = embedding
    return indexed


def build_alias_actions(
    alias: str,
    target_index: str,
    current_indices: set[str],
) -> list[dict]:
    actions = [
        {"remove": {"index": index, "alias": alias}}
        for index in sorted(current_indices - {target_index})
    ]
    actions.append({"add": {"index": target_index, "alias": alias}})
    return actions


async def _embed_documents(gateway, documents: list[dict]) -> list[list[float]]:
    texts = [f"{item.get('title', '')}\n{item['text']}" for item in documents]
    vectors: list[list[float]] = []
    for start in range(0, len(texts), 16):
        response = await gateway.client.embeddings.create(
            model=gateway.model,
            input=texts[start : start + 16],
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        vectors.extend(list(item.embedding) for item in ordered)
    if len(vectors) != len(documents):
        raise RuntimeError("embedding response count mismatch")
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) != 1 or not dimensions or 0 in dimensions:
        raise RuntimeError("embedding dimensions are inconsistent")
    return vectors


def _current_alias_indices(client, alias: str) -> set[str]:
    if not client.indices.exists_alias(name=alias):
        if client.indices.exists(index=alias):
            raise RuntimeError(f"{alias} exists as an index, not an alias")
        return set()
    return set(client.indices.get_alias(name=alias))


async def seed() -> dict[str, int]:
    settings = Settings.from_env()
    documents = json.loads(FIXTURE.read_text(encoding="utf-8"))
    if not isinstance(documents, list) or not documents:
        raise RuntimeError("dummy fixture is empty")

    logical_indices = {
        "domain_knowledge": settings.domain_knowledge_index,
        "mail": settings.mail_index_alias,
        "calendar": settings.calendar_index_alias,
    }
    if {item.get("source_type") for item in documents} != set(logical_indices):
        raise RuntimeError("dummy fixture source set is invalid")

    embedding_gateway = build_embedding_gateway(settings)
    try:
        vectors = await _embed_documents(embedding_gateway, documents)
    finally:
        await embedding_gateway.client.close()

    dimension = len(vectors[0])
    targets = {
        source: f"{logical}-{INDEX_SUFFIX}"
        for source, logical in logical_indices.items()
    }
    client = build_opensearch_client(settings)
    try:
        for target in targets.values():
            if not client.indices.exists(index=target):
                client.indices.create(
                    index=target,
                    body=build_index_definition(dimension),
                )

        actions = []
        counts = {source: 0 for source in logical_indices}
        for item, vector in zip(documents, vectors, strict=True):
            source = item["source_type"]
            counts[source] += 1
            actions.append(
                {
                    "_op_type": "index",
                    "_index": targets[source],
                    "_id": item["document_id"],
                    "_source": build_index_document(item, vector),
                }
            )
        indexed, errors = bulk(
            client,
            actions,
            chunk_size=25,
            refresh=True,
            raise_on_error=True,
        )
        if errors or indexed != len(actions):
            raise RuntimeError("OpenSearch bulk indexing was incomplete")

        alias_actions = []
        for source, alias in logical_indices.items():
            alias_actions.extend(
                build_alias_actions(
                    alias,
                    targets[source],
                    _current_alias_indices(client, alias),
                )
            )
        client.indices.update_aliases(body={"actions": alias_actions})
        for target in targets.values():
            client.indices.refresh(index=target)
        return counts
    finally:
        client.close()


def main() -> int:
    counts = asyncio.run(seed())
    print("OpenSearch dummy seed complete")
    for source, count in counts.items():
        print(f"{source}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
