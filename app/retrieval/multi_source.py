import json
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.domain.agentic import (
    QueryAnalysis,
    SearchDocument,
    SearchResult,
    ToolAction,
)
from app.domain.policy import PolicyContext
from app.retrieval.source_registry import SourceRegistry


class MultiSourceSearch(Protocol):
    async def execute(
        self,
        action: ToolAction,
        policy: PolicyContext,
        analysis: QueryAnalysis,
    ) -> SearchResult:
        raise NotImplementedError


class StoredDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: str
    document_id: str
    source_type: str
    content_kind: str | None = None
    source_id: str | None = None
    parent_event_id: str | None = None
    employee_id: str | None = None
    is_active: bool = True
    is_cancelled: bool = False
    title: str = ""
    text: str = Field(min_length=1, max_length=8000)
    occurred_at: str | None = None
    metadata: dict = Field(default_factory=dict)


def _tokens(text: str) -> set[str]:
    return {
        item.casefold()
        for item in re.findall(r"[A-Za-z0-9가-힣]+", text)
        if len(item) > 1
    }


class InMemoryMultiSourceSearch:
    def __init__(self, documents: list[StoredDocument], registry: SourceRegistry):
        self.documents = documents
        self.registry = registry
        self.calls: list[tuple[ToolAction, str]] = []

    @classmethod
    def from_path(
        cls, path: Path, registry: SourceRegistry
    ) -> "InMemoryMultiSourceSearch":
        payload = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            [StoredDocument.model_validate(item) for item in payload],
            registry,
        )

    def _authorized(
        self,
        item: StoredDocument,
        action: ToolAction,
        owner: str,
        analysis: QueryAnalysis,
    ) -> bool:
        if item.index != self.registry.index_for(action.tool):
            return False

        expected_source = self.registry.source_for(action.tool)
        if item.source_type != expected_source:
            return False
        if expected_source == "domain_knowledge":
            return item.is_active

        if item.employee_id != owner or not item.is_active:
            return False
        if expected_source == "calendar" and item.is_cancelled:
            return False

        if analysis.start_at_utc and analysis.end_at_utc:
            if not item.occurred_at:
                return False
            occurred_at = datetime.fromisoformat(
                item.occurred_at.replace("Z", "+00:00")
            )
            if not analysis.start_at_utc <= occurred_at < analysis.end_at_utc:
                return False

        return True

    @staticmethod
    def _matches_action_filters(
        item: StoredDocument, action: ToolAction
    ) -> bool:
        if action.content_kinds and item.content_kind not in action.content_kinds:
            return False
        if action.attachment_name:
            actual = str(item.metadata.get("attachment_name") or "")
            if action.attachment_name.casefold() not in actual.casefold():
                return False
        return True

    def _allowed(
        self,
        item: StoredDocument,
        action: ToolAction,
        owner: str,
        analysis: QueryAnalysis,
    ) -> bool:
        return self._authorized(item, action, owner, analysis) and (
            self._matches_action_filters(item, action)
        )

    @staticmethod
    def _document(item: StoredDocument, score: float) -> SearchDocument:
        return SearchDocument(
            source_type=item.source_type,
            document_id=item.document_id,
            source_id=item.source_id,
            parent_event_id=item.parent_event_id,
            content_kind=item.content_kind,
            title=item.title,
            text=item.text,
            score=score,
            metadata=item.metadata,
        )

    @staticmethod
    def _reconstruct_mail(
        documents: list[SearchDocument], top_k: int
    ) -> list[SearchDocument]:
        grouped: dict[tuple[str, str | None], list[SearchDocument]] = defaultdict(
            list
        )
        for item in documents:
            grouped[(item.source_id or item.document_id, item.content_kind)].append(
                item
            )

        reconstructed = []
        for parts in grouped.values():
            ordered = sorted(
                parts,
                key=lambda item: int(item.metadata.get("chunk_index") or 0),
            )
            unique_text = list(dict.fromkeys(item.text for item in ordered))
            base = max(ordered, key=lambda item: item.score)
            reconstructed.append(
                base.model_copy(update={"text": "\n\n".join(unique_text)[:8000]})
            )
        reconstructed.sort(key=lambda item: (-item.score, item.document_id))
        return reconstructed[:top_k]

    @staticmethod
    def _expand_event(
        allowed: list[StoredDocument], event_id: str
    ) -> list[StoredDocument]:
        parents = [
            item
            for item in allowed
            if item.document_id == event_id and item.content_kind == "event"
        ]
        if not parents:
            return []

        parent = min(parents, key=lambda item: item.document_id)
        children = sorted(
            (
                item
                for item in allowed
                if item.document_id != event_id
                and item.parent_event_id == event_id
            ),
            key=lambda item: item.document_id,
        )
        return [parent, *children]

    async def execute(
        self,
        action: ToolAction,
        policy: PolicyContext,
        analysis: QueryAnalysis,
    ) -> SearchResult:
        self.calls.append((action, policy.user_id))

        if action.tool == "expand_calendar_event":
            authorized = [
                item
                for item in self.documents
                if self._authorized(item, action, policy.user_id, analysis)
            ]
            visible_bundle = self._expand_event(
                authorized, action.event_id or ""
            )
            related = [
                item
                for item in visible_bundle
                if self._matches_action_filters(item, action)
            ]
            documents = [self._document(item, 1.0) for item in related]
        else:
            allowed = [
                item
                for item in self.documents
                if self._allowed(item, action, policy.user_id, analysis)
            ]
            query_tokens = _tokens(action.query)
            ranked = []
            for item in allowed:
                haystack = _tokens(f"{item.title} {item.text}")
                overlap = len(query_tokens & haystack)
                if overlap:
                    ranked.append((overlap / max(1, len(query_tokens)), item))
            ranked.sort(key=lambda pair: (-pair[0], pair[1].document_id))
            documents = [
                self._document(item, score)
                for score, item in ranked[: action.top_k]
            ]

        if action.tool == "search_mail":
            documents = self._reconstruct_mail(documents, action.top_k)

        return SearchResult(
            tool=action.tool,
            query=action.query,
            documents=documents,
            total_hits=len(documents),
            retrieval_mode="deterministic",
        )
