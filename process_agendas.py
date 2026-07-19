"""Run one-week LOTCD classification into JSON files."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from agenda_extract import build_splitter
from classification_store import (
    DEFAULT_DATA_DIR,
    DEFAULT_RULES_PATH,
    JsonClassificationStore,
)
from classification_workbench import run_week_classification
from knowledge_models import Mail


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
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    relative = mail_dir.relative_to(DATA_DIR)
    week = str(meta.get("week") or relative.parts[0])
    team = str(meta.get("team") or relative.parts[1])
    raw_mail_id = str(meta.get("mail_id") or mail_dir.name)
    return Mail(
        id=f"{week}:{team}:{raw_mail_id}",
        subject=str(meta.get("subject") or mail_dir.name),
        sender_team=team,
        sender=str(meta.get("sender") or "unknown"),
        received_at=_received_at(meta, combined_path),
        body=combined_path.read_text(encoding="utf-8"),
        reply_to=None,
        source_path=combined_path.resolve().relative_to(DATA_DIR.resolve().parent).as_posix(),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--allow-dummy-taxonomy", action="store_true")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--rules", type=Path, default=DEFAULT_RULES_PATH)
    args = parser.parse_args()
    if not args.allow_external_llm:
        parser.error("--allow-external-llm is required")
    store = JsonClassificationStore(args.data_dir, args.rules)
    if store.taxonomy.is_dummy and not args.allow_dummy_taxonomy:
        parser.error("--allow-dummy-taxonomy is required for dummy rules")
    files = sorted((DATA_DIR / args.week).glob("**/combined.txt"))
    if args.limit is not None:
        files = files[: args.limit]
    summary = run_week_classification(
        week=args.week,
        store=store,
        mail_directories=[path.parent for path in files],
        splitter=build_splitter(store.taxonomy),
        rerun=args.rerun,
    )
    print(summary.model_dump_json())
    return 1 if summary.workflow_state == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
