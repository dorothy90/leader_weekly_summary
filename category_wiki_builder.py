"""Build category Wiki pages from the existing OpenSearch mail chunks.

The vector ingestion remains owned by ``embed_vectordb.py``. This builder reads
``weekly_mail`` text chunks, reconstructs each mail, creates structured agendas
without embeddings, and writes one current Wiki page plus weekly snapshots for
every Domain, Tech, and LOTCD taxonomy node.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from opensearchpy import OpenSearch, helpers

from agenda_extract import CanonicalResolver, build_splitter, extract_mail, llm_connection
from knowledge_models import CategoryPath, Mail, TaxonomyDocument


SOURCE_INDEX = os.getenv("OPENSEARCH_INDEX", "weekly_mail")
AGENDA_INDEX = os.getenv("AGENDA_INDEX_NAME", "mail_agendas")
PAGE_INDEX = os.getenv("CATEGORY_WIKI_INDEX", "category_wiki_pages")
DEFAULT_TAXONOMY_PATH = Path("fixtures/knowledge/taxonomy.json")
TERMINAL_STATES = {
    "closed",
    "completed",
    "normal",
    "positive",
    "resolved",
    "stable",
}
AGENDA_HASH_FIELDS = (
    "mail_id",
    "week",
    "summary",
    "source_quote",
    "state",
    "topic",
    "target_paths",
    "candidate_paths",
    "source_doc_ids",
    "review_status",
)


@dataclass(frozen=True)
class SourceMail:
    id: str
    raw_mail_id: str
    week: str
    team: str
    subject: str
    body: str
    source_doc_ids: tuple[str, ...]


@dataclass(frozen=True)
class CategoryNode:
    id: str
    level: Literal["domain", "tech", "lotcd"]
    domain: str
    tech: str | None
    lotcd: str | None
    title: str
    product: str | None = None
    fab_id: str | None = None


PageRenderer = Callable[[CategoryNode, list[dict[str, Any]], str], str]


def load_taxonomy(path: Path | None = None) -> TaxonomyDocument:
    configured = path or Path(
        os.getenv("KNOWLEDGE_TAXONOMY_PATH", str(DEFAULT_TAXONOMY_PATH))
    )
    return TaxonomyDocument.model_validate_json(
        configured.read_text(encoding="utf-8")
    )


def agenda_index_definition() -> dict[str, Any]:
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
                "agenda_id": {"type": "keyword"},
                "mail_id": {"type": "keyword"},
                "raw_mail_id": {"type": "keyword"},
                "source_doc_ids": {"type": "keyword"},
                "week": {"type": "keyword"},
                "subject": {
                    "type": "text",
                    "analyzer": "knowledge_korean",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "sender_team": {"type": "keyword"},
                "summary": {"type": "text", "analyzer": "knowledge_korean"},
                "source_quote": {"type": "text", "analyzer": "knowledge_korean"},
                "source_start": {"type": "integer"},
                "source_end": {"type": "integer"},
                "classification_context": {
                    "type": "text",
                    "analyzer": "knowledge_korean",
                },
                "scope": {"type": "keyword"},
                "topic": {"type": "keyword"},
                "state": {"type": "keyword"},
                "issue_id": {"type": "keyword"},
                "confidence": {"type": "float"},
                "review_status": {"type": "keyword"},
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
                "candidate_paths": {
                    "type": "nested",
                    "properties": {
                        "domain": {"type": "keyword"},
                        "tech": {"type": "keyword"},
                        "lotcd": {"type": "keyword"},
                    },
                },
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                "updated_week": {"type": "keyword"},
                "content_hash": {"type": "keyword"},
                "issue_id_source": {"type": "keyword"},
            },
        },
    }


def page_index_definition() -> dict[str, Any]:
    return {
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
            "refresh_interval": "5s",
            "analysis": {
                "tokenizer": {
                    "category_nori": {
                        "type": "nori_tokenizer",
                        "decompound_mode": "mixed",
                    }
                },
                "analyzer": {
                    "category_korean": {
                        "type": "custom",
                        "tokenizer": "category_nori",
                        "filter": ["nori_readingform", "lowercase"],
                    }
                },
            },
        },
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "category_id": {"type": "keyword"},
                "page_kind": {"type": "keyword"},
                "level": {"type": "keyword"},
                "domain": {"type": "keyword"},
                "tech": {"type": "keyword"},
                "lotcd": {"type": "keyword"},
                "title": {
                    "type": "text",
                    "analyzer": "category_korean",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                "product": {"type": "keyword"},
                "fab_id": {"type": "keyword"},
                "as_of_week": {"type": "keyword"},
                "body_markdown": {"type": "text", "analyzer": "category_korean"},
                "agenda_count": {"type": "integer"},
                "open_issue_ids": {"type": "keyword"},
                "resolved_issue_ids": {"type": "keyword"},
                "review_agenda_ids": {"type": "keyword"},
                "source_agenda_ids": {"type": "keyword"},
                "source_doc_ids": {"type": "keyword"},
                "source_hash": {"type": "keyword"},
                "taxonomy_version": {"type": "integer"},
                "generated_at": {"type": "date"},
            },
        },
    }


def ensure_index(client: OpenSearch, index: str, definition: dict[str, Any]) -> None:
    if not client.indices.exists(index=index):
        client.indices.create(index=index, body=definition)
        return
    current = client.indices.get_mapping(index=index)
    current_properties = (
        current.get(index, {}).get("mappings", {}).get("properties", {})
    )
    desired = definition["mappings"]["properties"]
    missing = {key: value for key, value in desired.items() if key not in current_properties}
    if missing:
        client.indices.put_mapping(index=index, body={"properties": missing})


def merge_overlapping_chunks(chunks: Iterable[str]) -> str:
    iterator = iter(chunks)
    try:
        merged = next(iterator)
    except StopIteration:
        return ""
    for chunk in iterator:
        max_size = min(len(merged), len(chunk), 2500)
        overlap = 0
        for size in range(max_size, 0, -1):
            if merged.endswith(chunk[:size]):
                overlap = size
                break
        merged += chunk[overlap:]
    return merged


def fetch_source_mails(
    client: OpenSearch,
    weeks: list[str] | None = None,
    *,
    page_size: int = 500,
) -> list[SourceMail]:
    filters: list[dict[str, Any]] = [{"term": {"type": "original_part"}}]
    if weeks:
        filters.append({"terms": {"week": weeks}})
    search_after = None
    hits: list[dict[str, Any]] = []
    while True:
        body: dict[str, Any] = {
            "size": page_size,
            "query": {"bool": {"filter": filters}},
            "sort": [
                {"week": "asc"},
                {"mail_id": "asc"},
                {"part_index": "asc"},
                {"_id": "asc"},
            ],
            "_source": [
                "text",
                "week",
                "team",
                "mail_id",
                "subject",
                "part_index",
            ],
        }
        if search_after is not None:
            body["search_after"] = search_after
        response = client.search(index=SOURCE_INDEX, body=body)
        batch = response.get("hits", {}).get("hits", [])
        hits.extend(batch)
        if len(batch) < page_size:
            break
        search_after = batch[-1]["sort"]

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for hit in hits:
        source = hit["_source"]
        key = (
            str(source.get("week", "unknown")),
            str(source.get("team", "unknown")),
            str(source.get("mail_id", "unknown")),
        )
        grouped[key].append(hit)

    mails: list[SourceMail] = []
    for (week, team, raw_mail_id), parts in sorted(grouped.items()):
        ordered = sorted(parts, key=lambda hit: hit["_source"].get("part_index", 0))
        canonical_id = f"{week}:{team}:{raw_mail_id}"
        mails.append(
            SourceMail(
                id=canonical_id,
                raw_mail_id=raw_mail_id,
                week=week,
                team=team,
                subject=str(ordered[0]["_source"].get("subject", raw_mail_id)),
                body=merge_overlapping_chunks(
                    str(hit["_source"].get("text", "")) for hit in ordered
                ),
                source_doc_ids=tuple(hit["_id"] for hit in ordered),
            )
        )
    return mails


def _week_datetime(week: str) -> datetime:
    match = re.fullmatch(r"(\d{4})-W?(\d{1,2})", week)
    if not match:
        return datetime.now(UTC)
    return datetime.fromisocalendar(int(match.group(1)), int(match.group(2)), 1).replace(
        tzinfo=UTC
    )


def _normalize_week(week: str) -> str:
    match = re.fullmatch(r"(\d{4})-W?(\d{1,2})", week)
    if not match:
        return week
    return f"{match.group(1)}-W{int(match.group(2)):02d}"


def _path_token(path: CategoryPath | dict[str, Any]) -> str:
    if isinstance(path, CategoryPath):
        domain, tech, lotcd = path.domain, path.tech, path.lotcd
    else:
        domain, tech, lotcd = path["domain"], path.get("tech"), path.get("lotcd")
    return "/".join(value for value in (domain, tech, lotcd) if value)


def derive_issue_id(paths: list[CategoryPath], topic: str) -> str:
    path_key = "|".join(sorted(_path_token(path) for path in paths)) or "unclassified"
    digest = hashlib.sha256(f"{path_key}|{topic.casefold()}".encode()).hexdigest()[:12]
    return f"issue:{digest}"


def agenda_content_hash(document: dict[str, Any]) -> str:
    payload = {field: document.get(field) for field in AGENDA_HASH_FIELDS}
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def apply_agenda_version(
    document: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    now: datetime,
    observed_week: str | None = None,
) -> dict[str, Any]:
    current = dict(document)
    current_hash = agenda_content_hash(current)
    previous_hash = (
        str(previous.get("content_hash") or agenda_content_hash(previous))
        if previous
        else None
    )
    if previous and current_hash == previous_hash:
        return {
            **current,
            "created_at": previous["created_at"],
            "updated_at": previous["updated_at"],
            "updated_week": previous["updated_week"],
            "content_hash": previous_hash,
        }
    timestamp = now.isoformat()
    change_week = str(
        observed_week
        or current.get("updated_week")
        or current["week"]
    )
    return {
        **current,
        "created_at": previous.get("created_at", timestamp) if previous else timestamp,
        "updated_at": timestamp,
        "updated_week": change_week,
        "content_hash": current_hash,
    }


def normalize_agenda_document(document: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(document)
    paths = [
        CategoryPath.model_validate(path)
        for path in normalized.get("target_paths", [])
    ]
    if not normalized.get("week"):
        received_at = normalized.get("received_at")
        try:
            received = datetime.fromisoformat(str(received_at))
            iso = received.isocalendar()
            normalized["week"] = f"{iso.year}-{iso.week:02d}"
        except (TypeError, ValueError):
            normalized["week"] = "unknown"
    normalized.setdefault("candidate_paths", [])
    normalized.setdefault("source_doc_ids", [])
    normalized.setdefault("review_status", "confirmed")
    normalized.setdefault(
        "issue_id",
        derive_issue_id(paths, str(normalized.get("topic", "unknown"))),
    )
    return normalized


def build_agenda_documents(
    mail: SourceMail,
    extraction,
) -> list[dict[str, Any]]:
    now = datetime.now(UTC).isoformat()
    documents: list[dict[str, Any]] = []
    for agenda in extraction.agendas:
        target_paths = [path.model_dump() for path in agenda.target_paths]
        candidate_paths = [path.model_dump() for path in agenda.candidate_paths]
        documents.append(
            {
                "agenda_id": agenda.id,
                "mail_id": mail.id,
                "raw_mail_id": mail.raw_mail_id,
                "source_doc_ids": list(mail.source_doc_ids),
                "week": mail.week,
                "subject": mail.subject,
                "sender_team": mail.team,
                "summary": agenda.summary,
                "source_quote": agenda.source_quote,
                "source_start": agenda.source_start,
                "source_end": agenda.source_end,
                "classification_context": agenda.classification_context,
                "scope": agenda.scope,
                "topic": agenda.topic,
                "state": agenda.state,
                "issue_id": derive_issue_id(agenda.target_paths, agenda.topic),
                "issue_id_source": "derived",
                "confidence": agenda.confidence,
                "review_status": "pending" if agenda.review_required else "confirmed",
                "domains": sorted({path.domain for path in agenda.target_paths}),
                "techs": sorted({path.tech for path in agenda.target_paths if path.tech}),
                "lotcds": sorted({path.lotcd for path in agenda.target_paths if path.lotcd}),
                "target_paths": target_paths,
                "candidate_paths": candidate_paths,
                "created_at": now,
            }
        )
    return documents


def replace_mail_agendas(
    client: OpenSearch,
    mail_id: str,
    documents: list[dict[str, Any]],
    *,
    observed_week: str | None = None,
) -> None:
    agenda_ids = [str(document["agenda_id"]) for document in documents]
    previous_by_id: dict[str, dict[str, Any]] = {}
    if agenda_ids:
        response = client.mget(index=AGENDA_INDEX, body={"ids": agenda_ids})
        previous_by_id = {
            str(hit["_id"]): hit["_source"]
            for hit in response.get("docs", [])
            if hit.get("found")
        }
    now = datetime.now(UTC)
    versioned_documents = [
        apply_agenda_version(
            document,
            previous_by_id.get(str(document["agenda_id"])),
            now=now,
            observed_week=observed_week,
        )
        for document in documents
    ]
    client.delete_by_query(
        index=AGENDA_INDEX,
        body={"query": {"term": {"mail_id": mail_id}}},
        conflicts="proceed",
        refresh=False,
    )
    if versioned_documents:
        helpers.bulk(
            client,
            [
                {"_index": AGENDA_INDEX, "_id": doc["agenda_id"], "_source": doc}
                for doc in versioned_documents
            ],
        )


def fetch_agendas(
    client: OpenSearch,
    *,
    as_of_week: str | None = None,
    page_size: int = 500,
) -> list[dict[str, Any]]:
    search_after = None
    results: list[dict[str, Any]] = []
    while True:
        body: dict[str, Any] = {
            "size": page_size,
            "query": {"match_all": {}},
            "sort": [{"_id": "asc"}],
        }
        if search_after is not None:
            body["search_after"] = search_after
        response = client.search(index=AGENDA_INDEX, body=body)
        batch = response.get("hits", {}).get("hits", [])
        results.extend(normalize_agenda_document(hit["_source"]) for hit in batch)
        if len(batch) < page_size:
            break
        search_after = batch[-1]["sort"]
    if as_of_week:
        return [
            agenda
            for agenda in results
            if agenda.get("week") != "unknown" and str(agenda["week"]) <= as_of_week
        ]
    return results


def category_nodes(taxonomy: TaxonomyDocument) -> list[CategoryNode]:
    nodes: list[CategoryNode] = []
    for domain in taxonomy.domains:
        domain_key = domain.name.lower()
        nodes.append(
            CategoryNode(
                id=f"domain:{domain_key}",
                level="domain",
                domain=domain.name,
                tech=None,
                lotcd=None,
                title=domain.name,
            )
        )
        for tech in domain.techs:
            tech_key = tech.name.lower()
            nodes.append(
                CategoryNode(
                    id=f"tech:{domain_key}:{tech_key}",
                    level="tech",
                    domain=domain.name,
                    tech=tech.name,
                    lotcd=None,
                    title=tech.name,
                )
            )
            for lotcd in tech.lotcds:
                nodes.append(
                    CategoryNode(
                        id=f"lotcd:{lotcd.code.lower()}",
                        level="lotcd",
                        domain=domain.name,
                        tech=tech.name,
                        lotcd=lotcd.code,
                        title=lotcd.code,
                        product=lotcd.product,
                        fab_id=lotcd.fab_id,
                    )
                )
    return nodes


def agenda_matches_node(agenda: dict[str, Any], node: CategoryNode) -> bool:
    paths = agenda.get("target_paths") or agenda.get("candidate_paths") or []
    return any(
        path.get("domain") == node.domain
        and (node.tech is None or path.get("tech") == node.tech)
        and (node.lotcd is None or path.get("lotcd") == node.lotcd)
        for path in paths
    )


def issue_timelines(
    agendas: list[dict[str, Any]],
    *,
    as_of_week: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for agenda in agendas:
        if agenda.get("review_status") == "confirmed":
            grouped[str(agenda["issue_id"])].append(agenda)
    issues: list[dict[str, Any]] = []
    for issue_id, events in grouped.items():
        events.sort(key=lambda item: (str(item.get("week", "")), str(item["agenda_id"])))
        latest = events[-1]
        current_state = str(latest.get("state", "unknown"))
        previous_terminal = any(
            str(event.get("state", "")).casefold() in TERMINAL_STATES
            for event in events[:-1]
        )
        event_type = (
            "reopened"
            if previous_terminal and current_state.casefold() not in TERMINAL_STATES
            else "resolved"
            if current_state.casefold() in TERMINAL_STATES
            else "created"
            if len(events) == 1
            else "updated"
        )
        issues.append(
            {
                "issue_id": issue_id,
                "title": latest["summary"],
                "topic": latest.get("topic", "unknown"),
                "current_state": current_state,
                "event_type": event_type,
                "first_seen_week": events[0].get("week"),
                "last_seen_week": latest.get("week"),
                "resolved_week": (
                    latest.get("week")
                    if current_state.casefold() in TERMINAL_STATES
                    else None
                ),
                "is_open": current_state.casefold() not in TERMINAL_STATES,
                "updated_this_week": latest.get("week") == as_of_week,
                "events": events,
            }
        )
    return sorted(issues, key=lambda issue: (not issue["is_open"], issue["issue_id"]))


def deterministic_page_renderer(
    node: CategoryNode,
    issues: list[dict[str, Any]],
    as_of_week: str,
) -> str:
    open_issues = [issue for issue in issues if issue["is_open"]]
    resolved_this_week = [
        issue
        for issue in issues
        if not issue["is_open"] and issue["resolved_week"] == as_of_week
    ]
    resolved_before = [
        issue
        for issue in issues
        if not issue["is_open"] and issue["resolved_week"] != as_of_week
    ]
    lines = [
        "## 이번 주 현황",
        "",
        f"- 기준 주차: {as_of_week}",
        f"- 진행 중 이슈: {len(open_issues)}건",
        f"- 이번 주 해결 이슈: {len(resolved_this_week)}건",
        "",
        "## 진행 중 이슈",
        "",
    ]
    if not open_issues:
        lines.append("- 진행 중 이슈 없음")
    for issue in open_issues:
        lines.extend(
            [
                f"### {issue['title']}",
                "",
                f"- 상태: {issue['current_state']}",
                f"- 최초 발생: {issue['first_seen_week']}",
                f"- 마지막 업데이트: {issue['last_seen_week']}",
                "",
            ]
        )
        for event in issue["events"]:
            lines.append(
                f"- {event['week']}: {event['summary']} [{event['agenda_id']}]"
            )
        lines.append("")
    lines.extend(["## 이번 주 해결 이슈", ""])
    if not resolved_this_week:
        lines.append("- 이번 주 해결 이슈 없음")
    for issue in resolved_this_week:
        lines.extend(
            [
                f"### {issue['title']}",
                "",
                f"- 해결 주차: {issue['resolved_week']}",
                f"- 최초 발생: {issue['first_seen_week']}",
                "",
            ]
        )
        for event in issue["events"]:
            lines.append(
                f"- {event['week']}: {event['summary']} [{event['agenda_id']}]"
            )
        lines.append("")
    lines.extend(["## 과거 해결 이슈", ""])
    if not resolved_before:
        lines.append("- 과거 해결 이슈 없음")
    for issue in resolved_before:
        lines.append(
            f"- {issue['title']} · {issue['first_seen_week']} → {issue['resolved_week']} [{issue['issue_id']}]"
        )
    return "\n".join(lines).strip()


def build_llm_page_renderer() -> PageRenderer:
    from langchain_openai import ChatOpenAI

    connection = llm_connection()
    llm = ChatOpenAI(
        model=connection.model,
        api_key=connection.api_key.get_secret_value(),
        base_url=connection.base_url,
        temperature=0,
    )

    def render(node: CategoryNode, issues: list[dict[str, Any]], as_of_week: str) -> str:
        context = []
        for issue in issues:
            context.append(
                {
                    "issue_id": issue["issue_id"],
                    "current_state": issue["current_state"],
                    "first_seen_week": issue["first_seen_week"],
                    "last_seen_week": issue["last_seen_week"],
                    "events": [
                        {
                            "agenda_id": event["agenda_id"],
                            "week": event["week"],
                            "summary": event["summary"],
                            "source_quote": event["source_quote"],
                            "state": event["state"],
                        }
                        for event in issue["events"]
                    ],
                }
            )
        prompt = f"""다음 근거만 사용해 반도체 수율 Wiki 본문을 한국어 Markdown으로 작성하세요.
분류: {_path_token({'domain': node.domain, 'tech': node.tech, 'lotcd': node.lotcd})}
기준 주차: {as_of_week}

필수 섹션:
## 이번 주 현황
## 진행 중 이슈
## 이번 주 해결 이슈
## 주차별 이력
## 과거 해결 이슈

규칙:
- 근거에 없는 원인, 수치, 담당자, 해결 여부를 추측하지 않습니다.
- 이번 주에 언급되지 않은 진행 이슈도 삭제하지 않습니다.
- resolved/closed/completed/stable 상태만 해결로 취급합니다.
- H1 제목과 Sources 섹션은 작성하지 않습니다.
- 각 주장 끝에 관련 agenda_id를 대괄호로 표시합니다.

근거:
{json.dumps(context, ensure_ascii=False)}"""
        response = llm.invoke(prompt)
        return str(response.content).strip()

    return render


def build_page_documents(
    taxonomy: TaxonomyDocument,
    agendas: list[dict[str, Any]],
    *,
    as_of_week: str,
    renderer: PageRenderer = deterministic_page_renderer,
) -> list[dict[str, Any]]:
    generated_at = datetime.now(UTC).isoformat()
    pages: list[dict[str, Any]] = []
    for node in category_nodes(taxonomy):
        related = [agenda for agenda in agendas if agenda_matches_node(agenda, node)]
        issues = issue_timelines(related, as_of_week=as_of_week)
        confirmed = [
            agenda for agenda in related if agenda.get("review_status") == "confirmed"
        ]
        reviews = [
            agenda for agenda in related if agenda.get("review_status") != "confirmed"
        ]
        body = renderer(node, issues, as_of_week)
        frontmatter = [
            "---",
            f"category_id: {node.id}",
            f"level: {node.level}",
            f"domain: {node.domain}",
        ]
        if node.tech:
            frontmatter.append(f"tech: {node.tech}")
        if node.lotcd:
            frontmatter.append(f"lotcd: {node.lotcd}")
        if node.product:
            frontmatter.append(f"product: {node.product}")
        if node.fab_id:
            frontmatter.append(f"fab: {node.fab_id}")
        frontmatter.extend(
            [
                f"as_of_week: {as_of_week}",
                f"agenda_count: {len(confirmed)}",
                "---",
                "",
                f"# {node.title}",
                "",
            ]
        )
        source_agenda_ids = sorted({str(agenda["agenda_id"]) for agenda in confirmed})
        source_doc_ids = sorted(
            {
                str(source_id)
                for agenda in confirmed
                for source_id in agenda.get("source_doc_ids", [])
            }
        )
        source_lines = ["", "## Sources", ""]
        source_lines.extend(
            f"- [[{agenda['agenda_id']}]] · {agenda['week']} · {agenda['subject']}"
            for agenda in confirmed
        )
        if reviews:
            source_lines.extend(["", "## 분류 검토 필요", ""])
            source_lines.extend(
                f"- {agenda['summary']} [{agenda['agenda_id']}]"
                for agenda in reviews
            )
        body_markdown = "\n".join(frontmatter) + body + "\n" + "\n".join(source_lines)
        source_hash = hashlib.sha256(
            json.dumps(
                [
                    {
                        "agenda_id": agenda["agenda_id"],
                        "week": agenda.get("week"),
                        "state": agenda.get("state"),
                        "summary": agenda.get("summary"),
                        "review_status": agenda.get("review_status"),
                    }
                    for agenda in related
                ],
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        page = {
            "category_id": node.id,
            "page_kind": "latest",
            "level": node.level,
            "domain": node.domain,
            "tech": node.tech,
            "lotcd": node.lotcd,
            "title": node.title,
            "product": node.product,
            "fab_id": node.fab_id,
            "as_of_week": as_of_week,
            "body_markdown": body_markdown,
            "agenda_count": len(confirmed),
            "open_issue_ids": [issue["issue_id"] for issue in issues if issue["is_open"]],
            "resolved_issue_ids": [
                issue["issue_id"] for issue in issues if not issue["is_open"]
            ],
            "review_agenda_ids": sorted(
                {str(agenda["agenda_id"]) for agenda in reviews}
            ),
            "source_agenda_ids": source_agenda_ids,
            "source_doc_ids": source_doc_ids,
            "source_hash": source_hash,
            "taxonomy_version": taxonomy.version,
            "generated_at": generated_at,
        }
        pages.append(page)
    return pages


def save_pages(client: OpenSearch, pages: list[dict[str, Any]]) -> int:
    actions: list[dict[str, Any]] = []
    for page in pages:
        actions.append(
            {"_index": PAGE_INDEX, "_id": page["category_id"], "_source": page}
        )
        snapshot = {**page, "page_kind": "snapshot"}
        actions.append(
            {
                "_index": PAGE_INDEX,
                "_id": f"{page['category_id']}:{page['as_of_week']}",
                "_source": snapshot,
            }
        )
    if actions:
        helpers.bulk(client, actions)
        client.indices.refresh(index=PAGE_INDEX)
    return len(pages)


def run(
    *,
    weeks: list[str] | None = None,
    taxonomy_path: Path | None = None,
    allow_external_llm: bool = False,
    allow_dummy_taxonomy: bool = False,
    extract_agendas: bool = True,
    deterministic: bool = False,
    dry_run: bool = False,
    client: OpenSearch | None = None,
    renderer: PageRenderer | None = None,
) -> dict[str, int]:
    taxonomy = load_taxonomy(taxonomy_path)
    if taxonomy.is_dummy and not allow_dummy_taxonomy:
        raise RuntimeError(
            "Dummy taxonomy is active. Import real mapping or explicitly allow it."
        )
    if (extract_agendas or not deterministic) and not allow_external_llm and renderer is None:
        raise RuntimeError("External LLM use requires explicit allow_external_llm=True")
    if client is None:
        from embed_vectordb import get_opensearch_client

        client = get_opensearch_client()
    if not dry_run:
        ensure_index(client, AGENDA_INDEX, agenda_index_definition())
        ensure_index(client, PAGE_INDEX, page_index_definition())

    extracted_count = 0
    mail_count = 0
    dry_run_documents: list[dict[str, Any]] = []
    observed_week = max((_normalize_week(week) for week in weeks or []), default=None)
    if extract_agendas:
        splitter = build_splitter(taxonomy)
        resolver = CanonicalResolver(taxonomy)
        for source_mail in fetch_source_mails(client, weeks):
            mail = Mail(
                id=source_mail.id,
                subject=source_mail.subject,
                sender_team=source_mail.team,
                sender="unknown",
                received_at=_week_datetime(source_mail.week),
                body=source_mail.body,
                reply_to=None,
            )
            extraction = extract_mail(mail, splitter, resolver)
            documents = build_agenda_documents(source_mail, extraction)
            if not dry_run:
                replace_mail_agendas(
                    client,
                    source_mail.id,
                    documents,
                    observed_week=observed_week,
                )
            else:
                dry_run_documents.extend(documents)
            extracted_count += len(documents)
            mail_count += 1
        if not dry_run:
            client.indices.refresh(index=AGENDA_INDEX)

    agenda_index_exists = client.indices.exists(index=AGENDA_INDEX)
    available_agendas = fetch_agendas(client) if agenda_index_exists else []
    if dry_run_documents:
        replaced_mail_ids = {document["mail_id"] for document in dry_run_documents}
        available_agendas = [
            agenda
            for agenda in available_agendas
            if agenda.get("mail_id") not in replaced_mail_ids
        ] + dry_run_documents
    available_weeks = sorted(
        {str(agenda.get("week")) for agenda in available_agendas if agenda.get("week")}
    )
    requested_weeks = sorted(weeks or available_weeks)
    if not requested_weeks:
        return {"mails": mail_count, "agendas": extracted_count, "pages": 0}
    as_of_week = requested_weeks[-1]
    page_renderer = renderer or (
        deterministic_page_renderer if deterministic else build_llm_page_renderer()
    )
    pages = build_page_documents(
        taxonomy,
        [agenda for agenda in available_agendas if str(agenda.get("week")) <= as_of_week],
        as_of_week=as_of_week,
        renderer=page_renderer,
    )
    if not dry_run:
        save_pages(client, pages)
    return {"mails": mail_count, "agendas": extracted_count, "pages": len(pages)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--week", action="append", dest="weeks")
    parser.add_argument("--taxonomy", type=Path)
    parser.add_argument("--allow-external-llm", action="store_true")
    parser.add_argument("--allow-dummy-taxonomy", action="store_true")
    parser.add_argument("--skip-agenda-extraction", action="store_true")
    parser.add_argument("--deterministic", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        stats = run(
            weeks=args.weeks,
            taxonomy_path=args.taxonomy,
            allow_external_llm=args.allow_external_llm,
            allow_dummy_taxonomy=args.allow_dummy_taxonomy,
            extract_agendas=not args.skip_agenda_extraction,
            deterministic=args.deterministic,
            dry_run=args.dry_run,
        )
    except RuntimeError as exc:
        print(str(exc))
        return 2
    print(json.dumps(stats, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
