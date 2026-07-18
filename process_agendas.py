"""Extract canonical agendas from processed mail folders.

External LLM use and dummy taxonomy use both require explicit opt-in. This keeps
the existing weekly pipeline unchanged until deployment policy and real mapping
data are configured.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from agenda_extract import CanonicalResolver, build_splitter, extract_mail
from agenda_opensearch import sync_mail
from classification_workbench import run_week_classification
from knowledge_models import Mail
from knowledge_store import DEFAULT_DB_PATH, SQLiteKnowledgeStore


DATA_DIR = Path("data")


def _received_at(meta: dict, combined_path: Path) -> datetime:
    value = meta.get("received") or meta.get("received_at")
    if value:
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            pass
    return datetime.fromtimestamp(combined_path.stat().st_mtime, tz=UTC)


def mail_from_directory(mail_dir: Path) -> Mail:
    combined_path = mail_dir / "combined.txt"
    meta_path = mail_dir / "meta.json"
    meta = (
        json.loads(meta_path.read_text(encoding="utf-8"))
        if meta_path.exists()
        else {}
    )
    relative = mail_dir.relative_to(DATA_DIR)
    week = str(meta.get("week") or (relative.parts[0] if relative.parts else "unknown"))
    team = str(
        meta.get("team")
        or (relative.parts[1] if len(relative.parts) > 1 else "unknown")
    )
    raw_mail_id = str(meta.get("mail_id") or mail_dir.name)
    mail_id = f"{week}:{team}:{raw_mail_id}"
    return Mail(
        id=mail_id,
        subject=str(meta.get("subject") or mail_dir.name),
        sender_team=team,
        sender=str(meta.get("sender") or "unknown"),
        received_at=_received_at(meta, combined_path),
        body=combined_path.read_text(encoding="utf-8"),
        reply_to=None,
    )


def process_all(
    week: str | None = None,
    *,
    allow_external_llm: bool = False,
    allow_dummy_taxonomy: bool = False,
    dry_run: bool = False,
    limit: int | None = None,
    db_path: Path | None = None,
    opensearch_client=None,
) -> dict[str, int]:
    if not allow_external_llm:
        raise RuntimeError("External LLM use requires explicit allow_external_llm=True")

    configured_db_path = db_path or Path(
        os.getenv("KNOWLEDGE_DB_PATH", str(DEFAULT_DB_PATH))
    )
    store = SQLiteKnowledgeStore(configured_db_path)
    taxonomy = store.taxonomy
    if taxonomy.is_dummy and not allow_dummy_taxonomy:
        raise RuntimeError(
            "Dummy taxonomy is active. Import real mapping or explicitly allow dummy taxonomy."
        )

    search_root = DATA_DIR / week if week else DATA_DIR
    combined_files = sorted(search_root.glob("**/combined.txt"))
    if limit is not None:
        combined_files = combined_files[:limit]

    splitter = build_splitter(taxonomy)
    resolver = CanonicalResolver(taxonomy, store.aliases())
    stats = {"processed": 0, "failed": 0, "agendas": 0}
    for combined_path in combined_files:
        mail_dir = combined_path.parent
        try:
            mail = mail_from_directory(mail_dir)
            result = extract_mail(mail, splitter, resolver)
            if not dry_run:
                store.save_extraction(mail, result)
                if opensearch_client is not None:
                    sync_mail(opensearch_client, store, mail.id)
                    store.complete_search_sync(mail.id)
                (mail_dir / "agendas.json").write_text(
                    result.model_dump_json(indent=2), encoding="utf-8"
                )
            stats["processed"] += 1
            stats["agendas"] += len(result.agendas)
            print(f"agenda {mail.id}: {len(result.agendas)}")
        except Exception as exc:
            stats["failed"] += 1
            print(f"agenda failed {mail_dir}: {type(exc).__name__}: {exc}")
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--allow-dummy-taxonomy", action="store_true")
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--index-opensearch", action="store_true")
    parser.add_argument("--rerun", action="store_true")
    args = parser.parse_args()
    if not args.dry_run and not args.week:
        parser.error("--week is required for a persisted Workbench run")
    try:
        if args.dry_run:
            stats = process_all(
                week=args.week,
                allow_external_llm=args.allow_external_llm,
                allow_dummy_taxonomy=args.allow_dummy_taxonomy,
                dry_run=True,
                limit=args.limit,
                db_path=args.db_path,
                opensearch_client=None,
            )
        else:
            if not args.allow_external_llm:
                raise RuntimeError(
                    "External LLM use requires explicit allow_external_llm=True"
                )
            if args.index_opensearch:
                raise RuntimeError(
                    "OpenSearch indexing is disabled for persisted Workbench runs"
                )
            configured_db_path = args.db_path or Path(
                os.getenv("KNOWLEDGE_DB_PATH", str(DEFAULT_DB_PATH))
            )
            store = SQLiteKnowledgeStore(configured_db_path)
            taxonomy = store.taxonomy
            if taxonomy.is_dummy and not args.allow_dummy_taxonomy:
                raise RuntimeError(
                    "Dummy taxonomy is active. Import real mapping or explicitly "
                    "allow dummy taxonomy."
                )
            combined_files = sorted(
                (DATA_DIR / args.week).glob("**/combined.txt")
            )
            if args.limit is not None:
                combined_files = combined_files[:args.limit]
            summary = run_week_classification(
                week=args.week,
                store=store,
                mail_directories=[path.parent for path in combined_files],
                splitter=build_splitter(taxonomy),
                rerun=args.rerun,
            )
            print(json.dumps(summary.model_dump(mode="json"), ensure_ascii=False))
            return 1 if summary.workflow_state == "failed" else 0
    except RuntimeError as exc:
        print(str(exc))
        return 2
    print(json.dumps(stats, ensure_ascii=False))
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
