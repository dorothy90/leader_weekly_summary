"""Owner-safe v1/v2 retrieval comparison with metadata-only JSONL output."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _owned_ids(hits: list[dict], user_id: str) -> list[str]:
    return [
        str(hit["document_id"])
        for hit in hits
        if hit.get("user_id") == user_id and hit.get("document_id")
    ]


def compare_rankings(
    query: str,
    user_id: str,
    v1_hits: list[dict],
    v2_hits: list[dict],
    *,
    v1_latency_ms: float,
    v2_latency_ms: float,
) -> dict:
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("user_id is required")
    v1_ids = _owned_ids(v1_hits, owner)
    v2_ids = _owned_ids(v2_hits, owner)
    union = set(v1_ids) | set(v2_ids)
    overlap = len(set(v1_ids) & set(v2_ids)) / len(union) if union else 1.0
    return {
        "query_hash": sha256(query.encode()).hexdigest(),
        "v1_document_ids": v1_ids,
        "v2_document_ids": v2_ids,
        "overlap": overlap,
        "v1_latency_ms": round(v1_latency_ms, 3),
        "v2_latency_ms": round(v2_latency_ms, 3),
    }


async def run_shadow(
    user_id: str, queries: Sequence[str], v1_service, v2_service
) -> list[dict]:
    """Run identical owner-scoped hybrid requests and retain safe metadata only."""
    from app.domain.evidence import SearchTask
    from app.domain.policy import PolicyContext

    policy = PolicyContext.from_user_id(user_id)
    rows: list[dict] = []
    for query in queries:
        task = SearchTask(query=query, source="mail")
        started = perf_counter()
        v1_result = await v1_service.search(task, policy)
        v1_latency = (perf_counter() - started) * 1000
        started = perf_counter()
        v2_result = await v2_service.search(task, policy)
        v2_latency = (perf_counter() - started) * 1000

        def safe_hits(result) -> list[dict]:
            return [
                {
                    "document_id": item.document_id,
                    "user_id": item.user_id,
                }
                for item in result.evidence
            ]

        rows.append(
            compare_rankings(
                query,
                policy.user_id,
                safe_hits(v1_result),
                safe_hits(v2_result),
                v1_latency_ms=v1_latency,
                v2_latency_ms=v2_latency,
            )
        )
    return rows


def write_jsonl(output: Path, rows: Sequence[dict]) -> None:
    output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--v1-index", required=True)
    parser.add_argument("--v2-index", required=True)
    parser.add_argument("--v2-parent-index")
    return parser.parse_args(argv)


async def _main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    queries = [
        line.strip() for line in args.queries.read_text(encoding="utf-8").splitlines()
    ]
    queries = [query for query in queries if query]
    from openai import AsyncOpenAI

    from app.api.dependencies import build_opensearch_client
    from app.config.settings import get_settings
    from app.retrieval.embedding import OpenAIEmbeddingGateway
    from app.retrieval.opensearch import AsyncOpenSearchGateway
    from app.retrieval.service import RetrievalService

    settings = get_settings()
    backend = AsyncOpenSearchGateway(build_opensearch_client(settings))
    ai = AsyncOpenAI(
        api_key=settings.openrouter_api_key.get_secret_value(),
        base_url=settings.openrouter_base_url or None,
    )
    embeddings = OpenAIEmbeddingGateway(ai, settings.embedding_model)
    v1 = RetrievalService(backend, embeddings, args.v1_index)
    v2 = RetrievalService(backend, embeddings, args.v2_index, args.v2_parent_index)
    write_jsonl(args.output, await run_shadow(args.user_id, queries, v1, v2))
    print(json.dumps({"queries": len(queries), "status": "complete"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
