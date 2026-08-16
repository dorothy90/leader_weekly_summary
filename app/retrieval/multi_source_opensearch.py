import asyncio
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from app.domain.agentic import (
    MAX_METADATA_KEY_LENGTH,
    MAX_METADATA_KEYS,
    MAX_METADATA_LIST_ITEM_LENGTH,
    MAX_METADATA_LIST_LENGTH,
    MAX_METADATA_NUMBER_MAGNITUDE,
    MAX_METADATA_SERIALIZED_BYTES,
    MAX_METADATA_STRING_LENGTH,
    QueryAnalysis,
    SearchDocument,
    SearchResult,
    ToolAction,
    normalize_stable_event_id,
    search_document_identity,
)
from app.domain.chat import BM25_FALLBACK_DISCLOSURE
from app.domain.evidence import RetrievalFilters
from app.domain.policy import PolicyContext
from app.retrieval.fusion import RankedHit, reciprocal_rank_fusion
from app.retrieval.source_registry import SourceRegistry


SAFE_METADATA = {
    "received_at",
    "sent_at",
    "modified_at",
    "start_at_utc",
    "end_at_utc",
    "timezone",
    "organizer_email",
    "attendee_emails",
    "attachment_name",
    "calendar_item_id",
    "attachment_id",
    "occurrence_id",
    "series_master_id",
    "chunk_index",
}

SOURCE_FIELDS = [
    "employee_id",
    "is_active",
    "is_cancelled",
    "source_id",
    "parent_event_id",
    "content_kind",
    "subject",
    "title",
    "text",
    *sorted(SAFE_METADATA),
]

MAX_DOCUMENT_ID_LENGTH = 256
MAX_TITLE_LENGTH = 500
MAX_TEXT_LENGTH = 8000
class OpenSearchMultiSourceSearch:
    def __init__(self, backend, embeddings, registry: SourceRegistry):
        self.backend = backend
        self.embeddings = embeddings
        self.registry = registry

    async def execute(
        self,
        action: ToolAction,
        policy: PolicyContext,
        analysis: QueryAnalysis,
        request_filters: RetrievalFilters | None = None,
    ) -> SearchResult:
        if action.tool == "expand_calendar_event":
            return await self._expand_calendar(
                action,
                policy,
                analysis,
                request_filters,
            )

        index = self.registry.index_for(action.tool)
        filters = self._mandatory_filters(
            action,
            policy,
            analysis,
            request_filters,
        )
        bm25 = self._bm25_body(action, filters)
        disclosures = []
        try:
            vector = await self.embeddings.embed(action.query)
        except Exception:
            responses = [await self.backend.search(index, bm25)]
            mode = "bm25"
            disclosures = [BM25_FALLBACK_DISCLOSURE]
        else:
            bm25_task = asyncio.create_task(self.backend.search(index, bm25))
            vector_task = asyncio.create_task(
                self.backend.search(
                    index,
                    self._vector_body(action, filters, vector),
                )
            )
            responses = list(await asyncio.gather(bm25_task, vector_task))
            mode = "hybrid"

        hits = self._fuse(responses)
        documents = self._normalize_hits(hits, action, policy)
        if action.tool == "search_mail":
            documents = self._reconstruct_mail(documents, action.top_k)
        else:
            documents = documents[: action.top_k]
        return SearchResult(
            tool=action.tool,
            query=action.query,
            documents=documents,
            total_hits=len(documents),
            retrieval_mode=mode,
            disclosures=disclosures,
        )

    def _mandatory_filters(
        self,
        action: ToolAction,
        policy: PolicyContext,
        analysis: QueryAnalysis,
        request_filters: RetrievalFilters | None = None,
    ) -> list[dict[str, Any]]:
        source = self.registry.source_for(action.tool)
        filters: list[dict[str, Any]] = []
        if source in {"mail", "calendar"}:
            filters.append({"term": {"employee_id": policy.user_id}})
        filters.append({"term": {"is_active": True}})
        if source == "calendar":
            filters.append({"term": {"is_cancelled": False}})
        if source == "mail" and request_filters is not None:
            if request_filters.teams:
                filters.append({"terms": {"team": request_filters.teams}})
            if request_filters.weeks:
                filters.append({"terms": {"week": request_filters.weeks}})
            if request_filters.mail_type:
                filters.append(
                    {"term": {"mail_type": request_filters.mail_type}}
                )
        if (
            source in {"mail", "calendar"}
            and analysis.start_at_utc
            and analysis.end_at_utc
        ):
            field = "start_at_utc" if source == "calendar" else "received_at"
            filters.append(
                {
                    "range": {
                        field: {
                            "gte": analysis.start_at_utc.isoformat(),
                            "lt": analysis.end_at_utc.isoformat(),
                        }
                    }
                }
            )
        if action.content_kinds:
            filters.append({"terms": {"content_kind": action.content_kinds}})
        if action.attachment_name:
            filters.append(
                {"wildcard": {"attachment_name": f"*{action.attachment_name}*"}}
            )
        if action.organizer_email:
            filters.append({"term": {"organizer_email": action.organizer_email}})
        if action.attendee_emails:
            filters.append({"terms": {"attendee_emails": action.attendee_emails}})
        return filters

    @staticmethod
    def _bm25_body(
        action: ToolAction,
        filters: list[dict[str, Any]],
    ) -> dict[str, Any]:
        fields = (
            [
                "subject^4",
                "location^2",
                "text^3",
                "organizer_email",
                "attendee_emails",
            ]
            if action.tool == "search_calendar"
            else ["text^3"]
        )
        return {
            "size": action.top_k * 3,
            "_source": SOURCE_FIELDS,
            "query": {
                "bool": {
                    "filter": filters,
                    "must": [
                        {"multi_match": {"query": action.query, "fields": fields}}
                    ],
                }
            },
        }

    @staticmethod
    def _vector_body(
        action: ToolAction,
        filters: list[dict[str, Any]],
        vector: Sequence[float],
    ) -> dict[str, Any]:
        return {
            "size": action.top_k * 3,
            "_source": SOURCE_FIELDS,
            "query": {
                "bool": {
                    "filter": filters,
                    "must": [
                        {
                            "knn": {
                                "embedding": {
                                    "vector": vector,
                                    "k": action.top_k * 3,
                                }
                            }
                        }
                    ],
                }
            },
        }

    @staticmethod
    def _fuse(responses: Sequence[object]) -> list[dict[str, Any]]:
        rankings = []
        for response in responses:
            ranking = []
            seen = set()
            if isinstance(response, Mapping):
                hits_block = response.get("hits")
                raw_hits = (
                    hits_block.get("hits", [])
                    if isinstance(hits_block, Mapping)
                    else []
                )
            else:
                raw_hits = []
            if not isinstance(raw_hits, Sequence) or isinstance(raw_hits, (str, bytes)):
                raw_hits = []
            for raw_hit in raw_hits:
                if not isinstance(raw_hit, Mapping):
                    continue
                document_id = raw_hit.get("_id")
                if not OpenSearchMultiSourceSearch._valid_document_id(document_id):
                    continue
                if document_id in seen:
                    continue
                seen.add(document_id)
                ranking.append(RankedHit(document_id, len(ranking) + 1, dict(raw_hit)))
            rankings.append(ranking)
        return [
            {**item.raw, "_rrf_score": item.score}
            for item in reciprocal_rank_fusion(rankings)
        ]

    @staticmethod
    def _valid_document_id(value: object) -> bool:
        return (
            isinstance(value, str)
            and bool(value.strip())
            and len(value.strip()) <= MAX_DOCUMENT_ID_LENGTH
        )

    @staticmethod
    def _optional_id(source: Mapping, key: str) -> tuple[str | None, bool]:
        value = source.get(key)
        if value is None:
            return None, True
        if not OpenSearchMultiSourceSearch._valid_document_id(value):
            return None, False
        return value.strip(), True

    @staticmethod
    def _title(source: Mapping) -> tuple[str, bool]:
        for key in ("subject", "title"):
            value = source.get(key)
            if value is None:
                continue
            if not isinstance(value, str):
                return "", False
            normalized = value.strip()
            if not normalized:
                continue
            if len(normalized) > MAX_TITLE_LENGTH:
                return "", False
            return normalized, True
        return "", True

    @staticmethod
    def _metadata_value(value: object) -> object:
        if value is None or isinstance(value, bool):
            return value
        if isinstance(value, str):
            if len(value) > MAX_METADATA_STRING_LENGTH:
                raise ValueError("metadata string exceeds limit")
            return value
        if isinstance(value, int):
            if abs(value) > MAX_METADATA_NUMBER_MAGNITUDE:
                raise ValueError("metadata integer exceeds limit")
            return value
        if isinstance(value, float):
            if not math.isfinite(value) or abs(value) > MAX_METADATA_NUMBER_MAGNITUDE:
                raise ValueError("metadata float exceeds limit")
            return value
        if isinstance(value, list):
            if len(value) > MAX_METADATA_LIST_LENGTH:
                raise ValueError("metadata list exceeds limit")
            result = []
            for item in value:
                if not isinstance(item, str):
                    raise ValueError("metadata lists must contain strings")
                if len(item) > MAX_METADATA_LIST_ITEM_LENGTH:
                    raise ValueError("metadata list item exceeds limit")
                result.append(item)
            return result
        raise ValueError("unsupported metadata value")

    @classmethod
    def _safe_metadata(cls, source: Mapping) -> dict[str, object] | None:
        keys = sorted(key for key in SAFE_METADATA if key in source)
        if len(keys) > MAX_METADATA_KEYS or any(
            len(key) > MAX_METADATA_KEY_LENGTH for key in keys
        ):
            return None
        try:
            metadata = {key: cls._metadata_value(source[key]) for key in keys}
            serialized = json.dumps(
                metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            return None
        if len(serialized) > MAX_METADATA_SERIALIZED_BYTES:
            return None
        return metadata

    def _normalize_hits(
        self,
        hits: Sequence[object],
        action: ToolAction,
        policy: PolicyContext,
    ) -> list[SearchDocument]:
        source_type = self.registry.source_for(action.tool)
        documents = []
        for hit in hits:
            if not isinstance(hit, Mapping):
                continue
            source = hit.get("_source")
            if not isinstance(source, Mapping):
                continue
            if source_type in {"mail", "calendar"}:
                if source.get("employee_id") != policy.user_id:
                    continue
            if source.get("is_active") is not True:
                continue
            if source_type == "calendar" and source.get("is_cancelled") is not False:
                continue
            if (
                action.content_kinds
                and source.get("content_kind") not in action.content_kinds
            ):
                continue
            if action.attachment_name:
                attachment_name = str(source.get("attachment_name") or "")
                if action.attachment_name.casefold() not in attachment_name.casefold():
                    continue
            document_id = hit.get("_id")
            if not self._valid_document_id(document_id):
                continue
            text_value = source.get("text")
            if not isinstance(text_value, str):
                continue
            text = text_value.strip()
            if not text or len(text) > MAX_TEXT_LENGTH:
                continue
            content_kind = source.get("content_kind")
            source_id, source_id_valid = self._optional_id(source, "source_id")
            raw_parent_event_id = source.get("parent_event_id")
            parent_event_id, parent_event_id_valid = self._optional_id(
                source, "parent_event_id"
            )
            if source_type == "calendar" and content_kind == "event":
                stable_event_id = normalize_stable_event_id(
                    source.get("calendar_item_id")
                )
                if stable_event_id is None:
                    continue
                source_id = stable_event_id
                parent_event_id = stable_event_id
                source_id_valid = parent_event_id_valid = True
            elif source_type == "calendar" and content_kind == "attachment":
                stable_parent_id = normalize_stable_event_id(raw_parent_event_id)
                if stable_parent_id is None:
                    continue
                parent_event_id = stable_parent_id
            title, title_valid = self._title(source)
            metadata = self._safe_metadata(source)
            if not (
                source_id_valid
                and parent_event_id_valid
                and title_valid
                and metadata is not None
            ):
                continue
            raw_score = hit.get("_rrf_score") or hit.get("_score") or 0
            try:
                score = float(raw_score)
                if not math.isfinite(score):
                    continue
                document = SearchDocument(
                    source_type=source_type,
                    document_id=document_id.strip(),
                    source_id=source_id,
                    parent_event_id=parent_event_id,
                    content_kind=content_kind,
                    title=title,
                    text=text,
                    score=score,
                    metadata=metadata,
                )
            except (TypeError, ValueError, ValidationError):
                continue
            documents.append(document)
        return documents

    @staticmethod
    def _chunk_index(document: SearchDocument) -> int:
        try:
            return int(document.metadata.get("chunk_index") or 0)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _reconstruct_mail(
        cls,
        documents: Sequence[SearchDocument],
        top_k: int,
    ) -> list[SearchDocument]:
        grouped: dict[tuple[str, str | None], list[SearchDocument]] = {}
        for item in documents:
            key = (item.source_id or item.document_id, item.content_kind)
            grouped.setdefault(key, []).append(item)
        result = []
        for parts in grouped.values():
            ordered = sorted(
                parts,
                key=lambda item: (cls._chunk_index(item), item.document_id),
            )
            base = max(ordered, key=lambda item: item.score)
            text = "\n\n".join(dict.fromkeys(item.text for item in ordered))[:8000]
            result.append(base.model_copy(update={"text": text}))
        return sorted(result, key=lambda item: (-item.score, item.document_id))[:top_k]

    @staticmethod
    def _deduplicate_documents(
        documents: Sequence[SearchDocument],
    ) -> list[SearchDocument]:
        unique = {}
        for item in documents:
            key = search_document_identity(item)
            current = unique.get(key)
            if current is None or item.score > current.score:
                unique[key] = item
        return sorted(unique.values(), key=lambda item: (-item.score, item.document_id))

    async def _expand_calendar(
        self,
        action: ToolAction,
        policy: PolicyContext,
        analysis: QueryAnalysis,
        request_filters: RetrievalFilters | None = None,
    ) -> SearchResult:
        event_id = action.event_id or ""
        index = self.registry.index_for("expand_calendar_event")
        parent_action = ToolAction(
            tool="expand_calendar_event",
            event_id=event_id,
            reason="authorize calendar parent",
            top_k=1,
        )
        parent_filters = self._mandatory_filters(
            parent_action,
            policy,
            analysis,
            request_filters,
        )
        parent_filters.extend(
            [
                {"term": {"calendar_item_id": event_id}},
                {"term": {"content_kind": "event"}},
            ]
        )
        parent_response = await self.backend.search(
            index,
            {
                "size": 1,
                "_source": SOURCE_FIELDS,
                "query": {"bool": {"filter": parent_filters}},
            },
        )

        def response_hits(response: object) -> list[Mapping]:
            if not isinstance(response, Mapping):
                return []
            hits_block = response.get("hits")
            raw_hits = (
                hits_block.get("hits", [])
                if isinstance(hits_block, Mapping)
                else []
            )
            if not isinstance(raw_hits, Sequence) or isinstance(
                raw_hits, (str, bytes)
            ):
                return []
            return [item for item in raw_hits if isinstance(item, Mapping)]

        parent_hits = []
        for raw_hit in response_hits(parent_response):
            source = raw_hit.get("_source")
            if not isinstance(source, Mapping):
                continue
            if not (
                source.get("calendar_item_id") == event_id
                and source.get("content_kind") == "event"
            ):
                continue
            try:
                score = float(raw_hit.get("_score") or 1)
            except (TypeError, ValueError):
                continue
            if math.isfinite(score):
                parent_hits.append({**raw_hit, "_rrf_score": score})
        authorized_parent = any(
            item.content_kind == "event" and item.source_id == event_id
            for item in self._normalize_hits(
                parent_hits,
                parent_action,
                policy,
            )
        )
        if not authorized_parent:
            return SearchResult(
                tool=action.tool,
                query="",
                documents=[],
                total_hits=0,
                retrieval_mode="bm25",
            )

        filters = self._mandatory_filters(
            action,
            policy,
            analysis,
            request_filters,
        )
        filters.append(
            {
                "bool": {
                    "should": [
                        {"term": {"calendar_item_id": event_id}},
                        {"term": {"parent_event_id": event_id}},
                    ],
                    "minimum_should_match": 1,
                }
            }
        )
        body = {
            "size": 50,
            "_source": SOURCE_FIELDS,
            "query": {"bool": {"filter": filters}},
        }
        response = await self.backend.search(index, body)
        hits = []
        for raw_hit in response_hits(response):
            source = raw_hit.get("_source")
            if not isinstance(source, Mapping):
                continue
            if not (
                source.get("calendar_item_id") == event_id
                or source.get("parent_event_id") == event_id
            ):
                continue
            try:
                score = float(raw_hit.get("_score") or 1)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(score):
                continue
            hits.append({**raw_hit, "_rrf_score": score})
        documents = self._deduplicate_documents(
            self._normalize_hits(hits, action, policy)
        )
        documents.sort(
            key=lambda item: (
                0
                if item.content_kind == "event" and item.source_id == event_id
                else 1,
                -item.score,
                item.document_id,
            )
        )
        documents = documents[: action.top_k]
        return SearchResult(
            tool=action.tool,
            query="",
            documents=documents,
            total_hits=len(documents),
            retrieval_mode="bm25",
        )
