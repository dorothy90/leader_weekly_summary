"""Plan and optionally execute an owner-safe v2 parent/child migration."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Sequence

from opensearchpy import helpers

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.content.mail import mail_content_id, mail_content_locator


MIGRATION_VERSION = "parent-child-v2.1"
PARSER_VERSION = "mail-v2"
CHUNKER_VERSION = "child-v2"
SCROLL_KEEPALIVE = "2m"
CANONICAL_HASH_VERSION = "mapped-document-v1"
MUTABLE_DOCUMENT_FIELDS = frozenset({"indexed_at", "created_at", "updated_at"})
MAX_PARENT_BYTES = 12_000
MAX_CHILD_BYTES = 4_000


def stable_id(*parts: str) -> str:
    return sha256("\x1f".join(str(part) for part in parts).encode()).hexdigest()


def canonical_document_hash(document: dict) -> str:
    """Hash every mapped value except intentionally mutable timestamps."""
    canonical = {
        key: value
        for key, value in document.items()
        if key not in MUTABLE_DOCUMENT_FIELDS
    }
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode()).hexdigest()


def build_parent_child(
    source: dict,
    parser_version: str = PARSER_VERSION,
    chunker_version: str = CHUNKER_VERSION,
) -> tuple[list[dict], list[dict]]:
    return build_grouped_parent_children([source], parser_version, chunker_version)


def _split_utf8_exact(text: str, byte_limit: int) -> list[str]:
    """Split only between Unicode code points and preserve text exactly."""
    if not text:
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for character in text:
        character_size = len(character.encode("utf-8"))
        if current and size + character_size > byte_limit:
            chunks.append("".join(current))
            current, size = [], 0
        current.append(character)
        size += character_size
    if current:
        chunks.append("".join(current))
    return chunks


def _safe_content_fields(source: dict, user_id: str, mail_id: str) -> tuple[str, str]:
    expected = mail_content_id(user_id, mail_id)
    locator = mail_content_locator(expected)
    if (
        source.get("content_id") == expected
        and source.get("document_locator") == locator
    ):
        return expected, locator
    return "", ""


def _compatibility_values(source: dict, user_id: str, mail_id: str) -> tuple[str, ...]:
    content_id, locator = _safe_content_fields(source, user_id, mail_id)
    return (
        user_id,
        mail_id,
        str(source.get("section") or source.get("source_type") or "body"),
        str(source.get("team") or ""),
        str(source.get("week") or ""),
        str(source.get("mail_type") or "other"),
        str(source.get("embedding_model") or "legacy"),
        str(source.get("parser_version") or "legacy"),
        str(source.get("chunker_version") or "legacy"),
        content_id,
        locator,
    )


def build_grouped_parent_children(
    sources: list[dict],
    parser_version: str = PARSER_VERSION,
    chunker_version: str = CHUNKER_VERSION,
) -> tuple[list[dict], list[dict]]:
    """Build bounded section parents and retrieval children from ordered legacy chunks."""
    if not sources:
        return [], []
    ordered = sorted(sources, key=lambda item: int(item.get("part_index", 0)))
    first = ordered[0]
    user_id = str(first.get("user_id") or "").strip()
    mail_id = str(first.get("mail_id") or "").strip()
    if not user_id or not mail_id:
        return [], []
    texts = [str(source.get("text") or "") for source in ordered]
    if any(text == "" for text in texts):
        return [], []
    combined = "\n\n".join(texts)
    source_spans = []
    cursor = 0
    for index, (source, text) in enumerate(zip(ordered, texts)):
        if index:
            cursor += 2
        source_spans.append((cursor, cursor + len(text), source))
        cursor += len(text)
    parent_texts = _split_utf8_exact(combined, MAX_PARENT_BYTES)
    parents: list[dict] = []
    children: list[dict] = []
    compatibility = _compatibility_values(first, user_id, mail_id)
    compatibility_digest = stable_id(*compatibility)
    absolute_start = 0
    for batch_ordinal, parent_text in enumerate(parent_texts):
        absolute_end = absolute_start + len(parent_text)
        section = compatibility[2]
        parent_id = stable_id(
            user_id,
            mail_id,
            section,
            compatibility_digest,
            parser_version,
            str(batch_ordinal),
        )
        parent = {
            "parent_id": parent_id,
            "mail_id": mail_id,
            "user_id": user_id,
            "text": parent_text,
            "team": compatibility[3],
            "week": compatibility[4],
            "mail_type": compatibility[5],
            "section_ordinal": batch_ordinal,
            "content_hash": sha256(parent_text.encode()).hexdigest(),
            "parser_version": parser_version,
            "chunker_version": chunker_version,
            "embedding_model": compatibility[6],
            "indexed_at": first.get("indexed_at") or "1970-01-01T00:00:00Z",
        }
        if compatibility[9] and compatibility[10]:
            parent["content_id"] = compatibility[9]
            parent["document_locator"] = compatibility[10]
        parents.append(parent)
        boundaries = {absolute_start, absolute_end}
        for span_start, span_end, _source in source_spans:
            if absolute_start < span_start < absolute_end:
                boundaries.add(span_start)
            if absolute_start < span_end < absolute_end:
                boundaries.add(span_end)
        points = sorted(boundaries)
        child_texts: list[tuple[str, object | None]] = []
        for start, end in zip(points, points[1:]):
            interval = combined[start:end]
            for child_text in _split_utf8_exact(interval, MAX_CHILD_BYTES):
                embedding = None
                for span_start, span_end, source in source_spans:
                    if (
                        start == span_start
                        and end == span_end
                        and child_text == str(source.get("text") or "")
                    ):
                        embedding = source.get("embedding")
                        break
                child_texts.append((child_text, embedding))
        total = len(child_texts)
        for child_ordinal, (text, embedding) in enumerate(child_texts):
            child_id = stable_id(parent_id, chunker_version, str(child_ordinal))
            child = {
                **parent,
                "child_id": child_id,
                "text": text,
                "content_hash": sha256(text.encode()).hexdigest(),
                "part_index": child_ordinal,
                "total_parts": total,
            }
            if embedding is not None:
                child["embedding"] = embedding
            children.append(child)
        absolute_start = absolute_end
    return parents, children


@dataclass
class MigrationReport:
    scanned: int = 0
    planned_parents: int = 0
    planned_children: int = 0
    missing_owner_ids: list[str] = field(default_factory=list)
    unknown_owner_ids: list[str] = field(default_factory=list)
    invalid_source_ids: list[str] = field(default_factory=list)
    collision_ids: list[str] = field(default_factory=list)


@dataclass
class MigrationPlan:
    parent_actions: list[dict]
    child_actions: list[dict]
    report: MigrationReport


def plan_migration(
    records: Iterable[dict],
    allowed_owners: set[str],
    seen_parent_hashes: dict[str, str] | None = None,
    parser_version: str = PARSER_VERSION,
    chunker_version: str = CHUNKER_VERSION,
) -> MigrationPlan:
    """Stage every valid document while quarantining unapproved owners."""
    allowed = {str(owner).strip() for owner in allowed_owners if str(owner).strip()}
    if not allowed:
        raise ValueError("at least one explicit allowed owner is required")
    report = MigrationReport()
    seen = seen_parent_hashes if seen_parent_hashes is not None else {}
    parents: dict[str, dict] = {}
    children: dict[str, dict] = {}
    grouped: dict[tuple[str, ...], list[dict]] = {}
    for record in records:
        report.scanned += 1
        source_id = str(record.get("_id") or "<unknown>")
        source = record.get("_source") or {}
        owner = str(source.get("user_id") or "").strip()
        if not owner:
            report.missing_owner_ids.append(source_id)
            continue
        if owner not in allowed:
            report.unknown_owner_ids.append(source_id)
            continue
        mail_id = str(source.get("mail_id") or "").strip()
        if not mail_id or str(source.get("text") or "") == "":
            report.invalid_source_ids.append(source_id)
            continue
        key = _compatibility_values(source, owner, mail_id)
        grouped.setdefault(key, []).append(source)

    for key, sources in grouped.items():
        unique_sources = {}
        for item in sources:
            unique_sources.setdefault(int(item.get("part_index", 0)), item)
        if len(unique_sources) != len(sources):
            report.collision_ids.append(stable_id(*key, "duplicate-part-index"))
        parent_docs, child_docs = build_grouped_parent_children(
            list(unique_sources.values()), parser_version, chunker_version
        )
        candidates = [
            *((parent["parent_id"], parent, parents) for parent in parent_docs),
            *((f"child:{child['child_id']}", child, children) for child in child_docs),
        ]
        collision = False
        for collision_key, document, _ in candidates:
            document_hash = canonical_document_hash(document)
            prior_hash = seen.get(collision_key)
            if prior_hash is not None and prior_hash != document_hash:
                report.collision_ids.append(collision_key.removeprefix("child:"))
                collision = True
        if collision:
            continue
        for collision_key, document, target in candidates:
            seen[collision_key] = canonical_document_hash(document)
            document_id = (
                document.get("parent_id") if target is parents else document["child_id"]
            )
            target[document_id] = {"_id": document_id, "_source": document}
    parent_actions = [parents[key] for key in sorted(parents)]
    child_actions = [children[key] for key in sorted(children)]
    report.planned_parents = len(parent_actions)
    report.planned_children = len(child_actions)
    report.missing_owner_ids.sort()
    report.unknown_owner_ids.sort()
    report.invalid_source_ids.sort()
    report.collision_ids = sorted(set(report.collision_ids))
    return MigrationPlan(parent_actions, child_actions, report)


def build_bulk_actions(
    plan: MigrationPlan, parent_index: str, child_index: str
) -> list[dict]:
    if plan.report.collision_ids:
        raise ValueError("migration plan contains deterministic ID collisions")
    return [
        {"_op_type": "index", "_index": parent_index, **action}
        for action in plan.parent_actions
    ] + [
        {"_op_type": "index", "_index": child_index, **action}
        for action in plan.child_actions
    ]


def detect_target_collisions(
    client, index: str, actions: list[dict], batch_size: int = 500
) -> list[str]:
    """Preflight complete mapped documents, allowing only canonical matches."""
    planned = {item["_id"]: item["_source"] for item in actions}
    collisions: list[str] = []
    ids = sorted(planned)
    for offset in range(0, len(ids), batch_size):
        response = client.mget(
            index=index, body={"ids": ids[offset : offset + batch_size]}
        )
        for existing in response.get("docs", []):
            if not existing.get("found"):
                continue
            document_id = str(existing.get("_id"))
            old = existing.get("_source") or {}
            new = planned.get(document_id) or {}
            if canonical_document_hash(old) != canonical_document_hash(new):
                collisions.append(document_id)
    return sorted(set(collisions))


def collect_source_snapshot(client, index: str, batch_size: int = 250) -> list[dict]:
    """Collect one stable OpenSearch scroll snapshot and always release it."""
    scroll_id: str | None = None
    records: list[dict] = []
    try:
        response = client.search(
            index=index,
            body={"size": batch_size, "query": {"match_all": {}}, "sort": ["_doc"]},
            scroll=SCROLL_KEEPALIVE,
        )
        scroll_id = response.get("_scroll_id")
        while True:
            hits = response.get("hits", {}).get("hits", [])
            if not hits:
                return records
            records.extend(hits)
            response = client.scroll(scroll_id=scroll_id, scroll=SCROLL_KEEPALIVE)
            scroll_id = response.get("_scroll_id") or scroll_id
    finally:
        if scroll_id:
            client.clear_scroll(scroll_id=scroll_id)


def build_migration_identity(
    source_index: str,
    parent_index: str,
    child_index: str,
    allowed_owners: set[str],
    parser_version: str,
    chunker_version: str,
    batch_size: int = 250,
) -> dict:
    owners = sorted(
        str(owner).strip() for owner in allowed_owners if str(owner).strip()
    )
    if not owners:
        raise ValueError("at least one explicit allowed owner is required")
    return {
        "migration_version": MIGRATION_VERSION,
        "source_index": source_index,
        "parent_index": parent_index,
        "child_index": child_index,
        "owner_mapping_digest": stable_id(*owners),
        "parser_version": parser_version,
        "chunker_version": chunker_version,
        "batch_size": batch_size,
        "snapshot_strategy": "scroll",
        "scroll_keepalive": SCROLL_KEEPALIVE,
        "embedding_strategy": "reuse-existing",
        "canonical_hash_version": CANONICAL_HASH_VERSION,
        "mutable_document_fields": sorted(MUTABLE_DOCUMENT_FIELDS),
        "parent_template_version": "weekly_mail_parent_v2",
        "child_template_version": "weekly_mail_child_v2",
    }


def _read_checkpoint(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def validate_checkpoint(checkpoint: dict, expected_identity: dict) -> None:
    if checkpoint and checkpoint.get("identity") != expected_identity:
        raise ValueError("checkpoint does not match this migration identity")


def _write_checkpoint(path: Path, identity: dict, report: MigrationReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "identity": identity,
        "status": "complete",
        "report_digest": canonical_document_hash(asdict(report)),
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def run_migration(
    client,
    *,
    source_index: str,
    parent_index: str,
    child_index: str,
    allowed_owners: set[str],
    checkpoint_path: Path,
    apply: bool,
    bulk_writer=helpers.bulk,
    batch_size: int = 250,
    parser_version: str = PARSER_VERSION,
    chunker_version: str = CHUNKER_VERSION,
) -> MigrationReport:
    """Complete all source/target preflight before making any write visible."""
    identity = build_migration_identity(
        source_index,
        parent_index,
        child_index,
        allowed_owners,
        parser_version,
        chunker_version,
        batch_size,
    )
    validate_checkpoint(_read_checkpoint(checkpoint_path), identity)
    records = collect_source_snapshot(client, source_index, batch_size)
    plan = plan_migration(
        records,
        allowed_owners,
        parser_version=parser_version,
        chunker_version=chunker_version,
    )
    target_collisions = detect_target_collisions(
        client, parent_index, plan.parent_actions
    ) + detect_target_collisions(client, child_index, plan.child_actions)
    plan.report.collision_ids = sorted(
        set(plan.report.collision_ids + target_collisions)
    )
    if plan.report.collision_ids or not apply:
        return plan.report
    actions = build_bulk_actions(plan, parent_index, child_index)
    if actions:
        bulk_writer(client, actions)
    _write_checkpoint(checkpoint_path, identity, plan.report)
    return plan.report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate embedded legacy chunks")
    parser.add_argument("--source-index", required=True)
    parser.add_argument("--parent-index", required=True)
    parser.add_argument("--child-index", required=True)
    parser.add_argument("--owner", action="append", required=True, dest="owners")
    parser.add_argument(
        "--checkpoint", type=Path, default=Path(".migration-checkpoint.json")
    )
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument("--parser-version", default=PARSER_VERSION)
    parser.add_argument("--chunker-version", default=CHUNKER_VERSION)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    from app.api.dependencies import build_opensearch_client

    report = run_migration(
        build_opensearch_client(),
        source_index=args.source_index,
        parent_index=args.parent_index,
        child_index=args.child_index,
        allowed_owners=set(args.owners),
        checkpoint_path=args.checkpoint,
        apply=args.apply,
        batch_size=args.batch_size,
        parser_version=args.parser_version,
        chunker_version=args.chunker_version,
    )
    print(
        json.dumps(
            {"mode": "apply" if args.apply else "dry-run", **asdict(report)},
            sort_keys=True,
        )
    )
    return 0 if not report.collision_ids else 2


if __name__ == "__main__":
    raise SystemExit(main())
