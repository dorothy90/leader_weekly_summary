from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from typing import Any, Literal

from opensearchpy import OpenSearch, helpers
from pydantic import BaseModel, ConfigDict, Field


ISSUE_INDEX = os.getenv("WIKI_ISSUE_INDEX", "wiki_issue_ledger")
TERMINAL_STATES = {"resolved", "closed", "completed", "stable", "positive", "normal"}


class LedgerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueStateEvent(LedgerModel):
    week: str
    agenda_id: str
    state: str
    event_type: Literal["created", "updated", "resolved", "reopened"]


class WikiIssue(LedgerModel):
    issue_id: str
    category_paths: list[str]
    subject_keys: list[str] = Field(default_factory=list)
    topic: str
    title: str
    current_status: Literal["ongoing", "resolved", "reopened"]
    agenda_ids: list[str]
    state_history: list[IssueStateEvent]
    first_seen_week: str
    last_updated_week: str
    resolved_week: str | None = None
    reopened_count: int = 0
    confidence: Literal["low", "medium", "high"] = "medium"
    review_required: bool = False


class IssueSuggestion(LedgerModel):
    agenda_id: str
    issue_id: str | None
    decision: Literal["link", "new", "review"]
    confidence: Literal["low", "medium", "high"]
    reason: str


class IssueLedgerResult(LedgerModel):
    issues: dict[str, WikiIssue]
    agenda_to_issue: dict[str, str]
    changed_issue_ids: list[str]
    review_items: list[str] = Field(default_factory=list)


IssueSuggestionFn = Callable[[dict[str, Any]], IssueSuggestion]


def normalize_subject(value: str) -> str:
    return re.sub(r"^(?:\s*(?:re|fw|fwd)\s*:\s*)+", "", value, flags=re.I).strip().casefold()


def category_tokens(agenda: dict[str, Any]) -> list[str]:
    return sorted(
        "/".join(
            value
            for value in (path["domain"], path.get("tech"), path.get("lotcd"))
            if value
        ).casefold()
        for path in agenda.get("target_paths", [])
    )


def provisional_issue_id(agenda: dict[str, Any]) -> str:
    payload = "|".join(
        [
            *category_tokens(agenda),
            normalize_subject(str(agenda.get("subject", ""))),
            str(agenda["agenda_id"]),
        ]
    )
    return "issue:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def resolve_issue_ledger(
    agendas: list[dict[str, Any]],
    existing: dict[str, WikiIssue],
    *,
    as_of_week: str,
    suggest: IssueSuggestionFn | None = None,
) -> IssueLedgerResult:
    issues = {
        issue_id: WikiIssue.model_validate(issue.model_dump())
        for issue_id, issue in existing.items()
    }
    agenda_to_issue = {
        agenda_id: issue_id
        for issue_id, issue in issues.items()
        for agenda_id in issue.agenda_ids
    }
    subject_to_issue = {
        subject_key: issue_id
        for issue_id, issue in issues.items()
        for subject_key in issue.subject_keys
    }
    changed: set[str] = set()
    review_items: list[str] = []

    for agenda in sorted(
        agendas,
        key=lambda item: (str(item["week"]), str(item["agenda_id"])),
    ):
        agenda_id = str(agenda["agenda_id"])
        if agenda_id in agenda_to_issue:
            continue
        subject_key = normalize_subject(str(agenda.get("subject", "")))
        issue_id = subject_to_issue.get(subject_key) if subject_key else None
        suggestion = None
        if issue_id is None and suggest is not None:
            suggestion = suggest(
                {
                    "agenda": agenda,
                    "candidate_issues": [
                        item.model_dump(mode="json")
                        for item in issues.values()
                        if set(item.category_paths) & set(category_tokens(agenda))
                    ],
                }
            )
            if (
                suggestion.decision == "link"
                and suggestion.confidence == "high"
                and suggestion.issue_id in issues
            ):
                issue_id = suggestion.issue_id

        review_required = bool(
            suggestion
            and (
                suggestion.decision == "review"
                or (
                    suggestion.decision == "link"
                    and suggestion.confidence != "high"
                )
            )
        )
        if issue_id is None:
            issue_id = provisional_issue_id(agenda)
            if issue_id not in issues:
                issues[issue_id] = WikiIssue(
                    issue_id=issue_id,
                    category_paths=category_tokens(agenda),
                    subject_keys=[subject_key] if subject_key else [],
                    topic=str(agenda.get("topic", "other")),
                    title=str(agenda.get("summary", agenda_id)),
                    current_status="ongoing",
                    agenda_ids=[],
                    state_history=[],
                    first_seen_week=str(agenda["week"]),
                    last_updated_week=str(agenda["week"]),
                    confidence=suggestion.confidence if suggestion else "medium",
                    review_required=review_required,
                )
        issue = issues[issue_id]
        terminal = str(agenda.get("state", "")).casefold() in TERMINAL_STATES
        reopened = issue.current_status == "resolved" and not terminal
        event_type = (
            "created"
            if not issue.state_history
            else "resolved"
            if terminal
            else "reopened"
            if reopened
            else "updated"
        )
        current_status = (
            "resolved"
            if terminal
            else "reopened"
            if reopened or issue.current_status == "reopened"
            else "ongoing"
        )
        paths = sorted({*issue.category_paths, *category_tokens(agenda)})
        issues[issue_id] = issue.model_copy(
            update={
                "category_paths": paths,
                "subject_keys": sorted(
                    {
                        *issue.subject_keys,
                        *([subject_key] if subject_key else []),
                    }
                ),
                "current_status": current_status,
                "agenda_ids": [*issue.agenda_ids, agenda_id],
                "state_history": [
                    *issue.state_history,
                    IssueStateEvent(
                        week=str(agenda["week"]),
                        agenda_id=agenda_id,
                        state=str(agenda.get("state", "")),
                        event_type=event_type,
                    ),
                ],
                "last_updated_week": str(agenda.get("updated_week") or agenda["week"]),
                "resolved_week": str(agenda["week"]) if terminal else issue.resolved_week,
                "reopened_count": issue.reopened_count + int(reopened),
                "review_required": issue.review_required or review_required,
            }
        )
        agenda_to_issue[agenda_id] = issue_id
        if subject_key:
            subject_to_issue[subject_key] = issue_id
        changed.add(issue_id)
        if review_required and suggestion is not None:
            review_items.append(agenda_id + ": " + suggestion.reason)

    return IssueLedgerResult(
        issues=issues,
        agenda_to_issue=agenda_to_issue,
        changed_issue_ids=sorted(changed),
        review_items=review_items,
    )


def issue_index_definition() -> dict[str, Any]:
    return {
        "mappings": {
            "dynamic": "strict",
            "properties": {
                "issue_id": {"type": "keyword"},
                "category_paths": {"type": "keyword"},
                "subject_keys": {"type": "keyword"},
                "topic": {"type": "keyword"},
                "title": {"type": "text"},
                "current_status": {"type": "keyword"},
                "agenda_ids": {"type": "keyword"},
                "state_history": {
                    "type": "nested",
                    "properties": {
                        "week": {"type": "keyword"},
                        "agenda_id": {"type": "keyword"},
                        "state": {"type": "keyword"},
                        "event_type": {"type": "keyword"},
                    },
                },
                "first_seen_week": {"type": "keyword"},
                "last_updated_week": {"type": "keyword"},
                "resolved_week": {"type": "keyword"},
                "reopened_count": {"type": "integer"},
                "confidence": {"type": "keyword"},
                "review_required": {"type": "boolean"},
            },
        }
    }


def fetch_issue_ledger(
    client: OpenSearch,
    *,
    page_size: int = 500,
) -> dict[str, WikiIssue]:
    after = None
    issues: dict[str, WikiIssue] = {}
    while True:
        body: dict[str, Any] = {
            "size": page_size,
            "query": {"match_all": {}},
            "sort": [{"_id": "asc"}],
        }
        if after is not None:
            body["search_after"] = after
        response = client.search(index=ISSUE_INDEX, body=body)
        hits = response.get("hits", {}).get("hits", [])
        for hit in hits:
            issue = WikiIssue.model_validate(hit["_source"])
            issues[issue.issue_id] = issue
        if len(hits) < page_size:
            return issues
        after = hits[-1]["sort"]


def save_issue_ledger(
    client: OpenSearch,
    issues: dict[str, WikiIssue],
) -> int:
    actions = [
        {
            "_index": ISSUE_INDEX,
            "_id": issue_id,
            "_source": issues[issue_id].model_dump(mode="json"),
        }
        for issue_id in sorted(issues)
    ]
    if actions:
        helpers.bulk(client, actions)
        client.indices.refresh(index=ISSUE_INDEX)
    return len(actions)
