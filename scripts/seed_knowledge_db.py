"""Create a demo or production Knowledge SQLite database."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from knowledge_store import DEFAULT_DB_PATH, SQLiteKnowledgeStore


def seed_database(
    db_path: Path,
    taxonomy_path: Path | None = None,
    *,
    reset: bool = False,
) -> dict:
    if taxonomy_path and not taxonomy_path.exists():
        raise FileNotFoundError(taxonomy_path)
    if db_path.exists() and taxonomy_path and not reset:
        raise RuntimeError(
            "Existing DB cannot switch taxonomy. Use a new path or pass --reset."
        )
    if reset and db_path.exists():
        db_path.unlink()

    previous = os.environ.get("KNOWLEDGE_TAXONOMY_PATH")
    try:
        if taxonomy_path:
            os.environ["KNOWLEDGE_TAXONOMY_PATH"] = str(taxonomy_path)
        else:
            os.environ.pop("KNOWLEDGE_TAXONOMY_PATH", None)
        store = SQLiteKnowledgeStore(db_path)
    finally:
        if previous is None:
            os.environ.pop("KNOWLEDGE_TAXONOMY_PATH", None)
        else:
            os.environ["KNOWLEDGE_TAXONOMY_PATH"] = previous

    taxonomy = store.taxonomy
    return {
        "db_path": str(db_path),
        "is_dummy": taxonomy.is_dummy,
        "domains": len(taxonomy.domains),
        "techs": sum(len(domain.techs) for domain in taxonomy.domains),
        "lotcds": sum(
            len(tech.lotcds)
            for domain in taxonomy.domains
            for tech in domain.techs
        ),
        "mails": len(store.mails),
        "agendas": len(store.agendas),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--taxonomy", type=Path)
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    try:
        result = seed_database(
            db_path=args.db_path,
            taxonomy_path=args.taxonomy,
            reset=args.reset,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(str(exc))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
