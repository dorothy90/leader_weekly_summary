"""Explicit owner backfill for legacy OpenSearch documents.

The command is a dry run unless ``--apply`` is supplied. It never derives an
owner from team, index name, or document contents.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def build_update_query(user_id: str) -> dict:
    owner = str(user_id or "").strip()
    if not owner:
        raise ValueError("user_id must not be blank")
    return {
        "query": {"bool": {"must_not": {"exists": {"field": "user_id"}}}},
        "script": {
            "lang": "painless",
            "source": "ctx._source.user_id = params.user_id",
            "params": {"user_id": owner},
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill one explicit owner")
    parser.add_argument("--index", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the update (default only reports the planned count)",
    )
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    body = build_update_query(args.user_id)
    from app.api.dependencies import build_opensearch_client

    client = build_opensearch_client()
    if not args.apply or args.dry_run:
        count = client.count(index=args.index, body={"query": body["query"]})
        print(json.dumps({"mode": "dry-run", "eligible": count.get("count", 0)}))
        return 0
    response = client.update_by_query(
        index=args.index,
        body=body,
        conflicts="proceed",
        refresh=True,
    )
    print(
        json.dumps(
            {
                "mode": "apply",
                "updated": response.get("updated", 0),
                "version_conflicts": response.get("version_conflicts", 0),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
