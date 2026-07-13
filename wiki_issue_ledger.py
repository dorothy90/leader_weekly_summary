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
LIFECYCLE_TOPICS = {"action", "root_cause"}


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
    deleted_issue_ids: list[str] = Field(default_factory=list)


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


def _topics_compatible(issue_topic: str, agenda_topic: str) -> bool:
    return (
        issue_topic.casefold() == agenda_topic.casefold()
        or issue_topic.casefold() in LIFECYCLE_TOPICS
        or agenda_topic.casefold() in LIFECYCLE_TOPICS
    )


def _compatible_issue(issue: WikiIssue, agenda: dict[str, Any]) -> bool:
    return bool(set(issue.category_paths) & set(category_tokens(agenda))) and _topics_compatible(
        issue.topic,
        str(agenda.get("topic", "other")),
    )


def _recompute_issue(
    issue: WikiIssue,
    events: list[IssueStateEvent],
    *,
    last_updated_week: str,
) -> WikiIssue:
    ordered = sorted(events, key=lambda event: (event.week, event.agenda_id))
    rebuilt: list[IssueStateEvent] = []
    status: Literal["ongoing", "resolved", "reopened"] = "ongoing"
    resolved_week = None
    reopened_count = 0
    for index, event in enumerate(ordered):
        terminal = event.state.casefold() in TERMINAL_STATES
        reopening = index > 0 and status == "resolved" and not terminal
        event_type: Literal["created", "updated", "resolved", "reopened"] = (
            "created"
            if index == 0
            else "resolved"
            if terminal
            else "reopened"
            if reopening
            else "updated"
        )
        rebuilt.append(event.model_copy(update={"event_type": event_type}))
        if terminal:
            status = "resolved"
            resolved_week = event.week
        elif reopening:
            status = "reopened"
            reopened_count += 1
        elif status != "reopened":
            status = "ongoing"
    return issue.model_copy(
        update={
            "current_status": status,
            "agenda_ids": [event.agenda_id for event in rebuilt],
            "state_history": rebuilt,
            "first_seen_week": rebuilt[0].week,
            "last_updated_week": last_updated_week,
            "resolved_week": resolved_week,
            "reopened_count": reopened_count,
        }
    )


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
    changed: set[str] = set()
    deleted: set[str] = set()
    review_items: list[str] = []

    for agenda in sorted(
        agendas,
        key=lambda item: (str(item["week"]), str(item["agenda_id"])),
    ):
        agenda_id = str(agenda["agenda_id"])
        exact_issue_id = agenda_to_issue.get(agenda_id)
        update_week = str(agenda.get("updated_week") or agenda["week"])
        if agenda.get("is_deleted"):
            if exact_issue_id is None:
                continue
            issue = issues[exact_issue_id]
            remaining = [
                event for event in issue.state_history if event.agenda_id != agenda_id
            ]
            agenda_to_issue.pop(agenda_id, None)
            if not remaining:
                del issues[exact_issue_id]
                deleted.add(exact_issue_id)
            else:
                issues[exact_issue_id] = _recompute_issue(
                    issue,
                    remaining,
                    last_updated_week=max(issue.last_updated_week, update_week),
                )
            changed.add(exact_issue_id)
            continue

        subject_key = normalize_subject(str(agenda.get("subject", "")))
        if exact_issue_id is not None:
            issue = issues[exact_issue_id]
            updated_issue = issue.model_copy(
                update={
                    "category_paths": sorted(
                        {*issue.category_paths, *category_tokens(agenda)}
                    ),
                    "subject_keys": sorted(
                        {
                            *issue.subject_keys,
                            *([subject_key] if subject_key else []),
                        }
                    ),
                    "title": str(agenda.get("summary", issue.title)),
                }
            )
            issues[exact_issue_id] = _recompute_issue(
                updated_issue,
                [
                    *(
                        event
                        for event in issue.state_history
                        if event.agenda_id != agenda_id
                    ),
                    IssueStateEvent(
                        week=str(agenda["week"]),
                        agenda_id=agenda_id,
                        state=str(agenda.get("state", "")),
                        event_type="updated",
                    ),
                ],
                last_updated_week=max(issue.last_updated_week, update_week),
            )
            changed.add(exact_issue_id)
            continue

        compatible_subject_issues = [
            issue_id
            for issue_id, issue in issues.items()
            if subject_key
            and subject_key in issue.subject_keys
            and _compatible_issue(issue, agenda)
        ]
        issue_id = (
            compatible_subject_issues[0]
            if len(compatible_subject_issues) == 1
            else None
        )
        ambiguous_subject = len(compatible_subject_issues) > 1
        suggestion = None
        if issue_id is None and not ambiguous_subject and suggest is not None:
            suggestion = suggest(
                {
                    "agenda": agenda,
                    "candidate_issues": [
                        item.model_dump(mode="json")
                        for item in issues.values()
                        if _compatible_issue(item, agenda)
                    ],
                }
            )
            if (
                suggestion.decision == "link"
                and suggestion.confidence == "high"
                and suggestion.issue_id in issues
                and _compatible_issue(issues[suggestion.issue_id], agenda)
            ):
                issue_id = suggestion.issue_id

        review_required = bool(
            ambiguous_subject
            or (
                suggestion
                and (
                    suggestion.decision == "review"
                    or (
                        suggestion.decision == "link"
                        and suggestion.confidence != "high"
                    )
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
        paths = sorted({*issue.category_paths, *category_tokens(agenda)})
        updated_issue = issue.model_copy(
            update={
                "category_paths": paths,
                "subject_keys": sorted(
                    {
                        *issue.subject_keys,
                        *([subject_key] if subject_key else []),
                    }
                ),
                "title": str(agenda.get("summary", issue.title)),
                "review_required": issue.review_required or review_required,
            }
        )
        issues[issue_id] = _recompute_issue(
            updated_issue,
            [
                *issue.state_history,
                IssueStateEvent(
                    week=str(agenda["week"]),
                    agenda_id=agenda_id,
                    state=str(agenda.get("state", "")),
                    event_type="updated",
                ),
            ],
            last_updated_week=max(issue.last_updated_week, update_week),
        )
        agenda_to_issue[agenda_id] = issue_id
        changed.add(issue_id)
        if ambiguous_subject:
            review_items.append(
                agenda_id + ": multiple compatible Issues share this subject"
            )
        elif review_required and suggestion is not None:
            review_items.append(agenda_id + ": " + suggestion.reason)

    return IssueLedgerResult(
        issues=issues,
        agenda_to_issue=agenda_to_issue,
        changed_issue_ids=sorted(changed),
        review_items=review_items,
        deleted_issue_ids=sorted(deleted),
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


def delete_issue_ledger(client: OpenSearch, issue_ids: list[str]) -> int:
    for issue_id in sorted(issue_ids):
        client.delete(index=ISSUE_INDEX, id=issue_id, ignore=[404])
    if issue_ids:
        client.indices.refresh(index=ISSUE_INDEX)
    return len(issue_ids)
