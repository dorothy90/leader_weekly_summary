"""Dry-run-first, partition-bound owner backfill with reconciliation."""

from __future__ import annotations

import argparse
import json
import sys
from hashlib import sha256
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

APPROVED_IMMUTABLE_PARTITION_FIELDS = frozenset(
    {"corpus_id", "ingestion_batch_id", "migration_partition"}
)


def _term(field: str, value: str) -> dict:
    name, selected = str(field).strip(), str(value).strip()
    if name not in APPROVED_IMMUTABLE_PARTITION_FIELDS or not selected:
        raise ValueError("an approved immutable partition field is required")
    return {"term": {name: selected}}


def build_update_query(
    user_id: str, partition_field: str, partition_value: str
) -> dict:
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("user_id must not be blank")
    partition = _term(partition_field, partition_value)
    return {
        "query": {
            "bool": {
                "filter": [partition],
                "must_not": [{"exists": {"field": "user_id"}}],
            }
        },
        "script": {
            "lang": "painless",
            "source": "ctx._source.user_id = params.user_id",
            "params": {"user_id": owner},
        },
    }


def _identity(index, owner, field, value, expected_count):
    payload = {
        "index": index,
        "user_id": owner,
        "partition_field": field,
        "partition_value": value,
        "expected_count": expected_count,
        "version": "owner-backfill-v2",
    }
    payload["digest"] = sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def run_backfill(
    client,
    *,
    index: str,
    user_id: str,
    partition_field: str,
    partition_value: str,
    expected_count: int,
    checkpoint_path: Path,
    apply: bool,
) -> dict:
    if expected_count < 0:
        raise ValueError("expected_count must be non-negative")
    body = build_update_query(user_id, partition_field, partition_value)
    identity = _identity(
        index,
        user_id.strip(),
        partition_field.strip(),
        partition_value.strip(),
        expected_count,
    )
    partition = _term(partition_field, partition_value)
    owned_query = {
        "bool": {"filter": [partition, {"term": {"user_id": user_id.strip()}}]}
    }

    existing_checkpoint = None
    if checkpoint_path.exists():
        existing_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if existing_checkpoint.get("identity") != identity:
            raise ValueError("checkpoint does not match this backfill identity")

    if apply:
        if existing_checkpoint is None:
            raise ValueError("apply requires a matching planned checkpoint")
        if existing_checkpoint.get("status") == "applied":
            return {**existing_checkpoint["report"], "idempotent": True}
        if existing_checkpoint.get("status") != "planned":
            raise ValueError("apply requires an unconsumed planned checkpoint")
        planned = existing_checkpoint["report"]
    else:
        if existing_checkpoint and existing_checkpoint.get("status") == "applied":
            return {**existing_checkpoint["report"], "idempotent": True}
        before_partition = client.count(
            index=index, body={"query": {"bool": {"filter": [partition]}}}
        ).get("count", 0)
        eligible_before = client.count(index=index, body={"query": body["query"]}).get(
            "count", 0
        )
        owned_before = client.count(index=index, body={"query": owned_query}).get(
            "count", 0
        )
        if eligible_before != expected_count:
            raise ValueError("eligible count does not match approved expected count")
        report = {
            "status": "planned",
            "before_partition": before_partition,
            "eligible_before": eligible_before,
            "owned_before": owned_before,
            "updated": 0,
            "conflicts": 0,
            "eligible_after": eligible_before,
            "owned_after": owned_before,
        }
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        checkpoint_path.write_text(
            json.dumps(
                {"identity": identity, "status": "planned", "report": report},
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        return report

    before_partition = client.count(
        index=index, body={"query": {"bool": {"filter": [partition]}}}
    ).get("count", 0)
    eligible_before = client.count(index=index, body={"query": body["query"]}).get(
        "count", 0
    )
    owned_before = client.count(index=index, body={"query": owned_query}).get(
        "count", 0
    )
    if (
        eligible_before != planned["eligible_before"]
        or eligible_before != expected_count
    ):
        raise ValueError("eligible count does not match planned expected count")
    if (
        before_partition != planned["before_partition"]
        or owned_before != planned["owned_before"]
    ):
        raise ValueError("partition counts changed after the planned checkpoint")
    report = dict(planned)
    response = client.update_by_query(
        index=index, body=body, conflicts="abort", refresh=True
    )
    report["updated"] = int(response.get("updated", 0))
    report["conflicts"] = int(response.get("version_conflicts", 0))
    if report["conflicts"] or report["updated"] != eligible_before:
        raise RuntimeError("backfill conflicts or update-count mismatch")
    report["eligible_after"] = client.count(
        index=index, body={"query": body["query"]}
    ).get("count", 0)
    report["owned_after"] = client.count(index=index, body={"query": owned_query}).get(
        "count", 0
    )
    after_partition = client.count(
        index=index, body={"query": {"bool": {"filter": [partition]}}}
    ).get("count", 0)
    if (
        report["eligible_after"] != 0
        or after_partition != before_partition
        or report["owned_after"] != owned_before + report["updated"]
    ):
        raise RuntimeError("backfill reconciliation failed")
    report["status"] = "applied"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text(
        json.dumps(
            {"identity": identity, "status": "applied", "report": report},
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill one approved corpus partition"
    )
    parser.add_argument("--index", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument(
        "--partition-field",
        required=True,
        choices=sorted(APPROVED_IMMUTABLE_PARTITION_FIELDS),
    )
    parser.add_argument("--partition-value", required=True)
    parser.add_argument("--expected-count", required=True, type=int)
    parser.add_argument(
        "--checkpoint", type=Path, default=Path(".owner-backfill-checkpoint.json")
    )
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from app.api.dependencies import build_opensearch_client

    try:
        report = run_backfill(
            build_opensearch_client(),
            index=args.index,
            user_id=args.user_id,
            partition_field=args.partition_field,
            partition_value=args.partition_value,
            expected_count=args.expected_count,
            checkpoint_path=args.checkpoint,
            apply=args.apply,
        )
    except (ValueError, RuntimeError) as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__}))
        return 2
    print(
        json.dumps(
            {"mode": "apply" if args.apply else "dry-run", **report}, sort_keys=True
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
