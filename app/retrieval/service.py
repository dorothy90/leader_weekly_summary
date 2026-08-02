import asyncio
import json
import re
from hashlib import sha256
from time import perf_counter
from typing import Any, Literal
import uuid
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from app.domain.chat import BM25_FALLBACK_DISCLOSURE
from app.domain.evidence import Evidence, RetrievalFilters, SearchTask
from app.domain.errors import AppError, ErrorCode
from app.domain.policy import PolicyContext
from app.retrieval.embedding import EmbeddingGateway
from app.retrieval.filters import build_owner_filters
from app.retrieval.fusion import RankedHit, reciprocal_rank_fusion
from app.retrieval.opensearch import OpenSearchGateway
from app.observability.tracing import (
    TraceEvent,
    emit_trace,
    hash_trace_value,
)


class RetrievalResult(BaseModel):
    evidence: list[Evidence] = Field(default_factory=list)
    mode: Literal["hybrid", "bm25"] = "hybrid"
    embedding_error: str | None = None
    embedding_error_class: str | None = None
    disclosures: list[str] = Field(default_factory=list)


class RetrievalService:
    def __init__(
        self,
        search: OpenSearchGateway,
        embeddings: EmbeddingGateway,
        child_index: str,
        parent_index: str | None = None,
        wiki_index: str = "wiki_summaries_v2",
        trace_sink=None,
    ):
        self.backend = search
        self.embeddings = embeddings
        self.child_index = child_index
        self.parent_index = parent_index
        self.wiki_index = wiki_index
        self.trace_sink = trace_sink

    async def search(
        self,
        task: SearchTask,
        policy: PolicyContext,
    ) -> RetrievalResult:
        started = perf_counter()
        try:
            result = await self._search(task, policy)
        except Exception as error:
            emit_trace(
                self.trace_sink,
                TraceEvent(
                    trace_id=uuid.uuid4().hex,
                    node_name="retrieval.search",
                    duration_ms=int((perf_counter() - started) * 1000),
                    status="error",
                    index_version_hash=hash_trace_value(self.child_index),
                    prompt_version_hash=hash_trace_value("retrieval-v1"),
                    model_hash=hash_trace_value("embedding"),
                    owner_hash=hash_trace_value(policy.user_id),
                    query_hash=hash_trace_value(task.query),
                    error_class=type(error).__name__,
                ),
            )
            if isinstance(error, AppError):
                raise
            raise AppError(
                ErrorCode.INDEX_UNAVAILABLE,
                "검색 인덱스를 사용할 수 없습니다.",
                retryable=True,
            ) from None
        emit_trace(
            self.trace_sink,
            TraceEvent(
                trace_id=uuid.uuid4().hex,
                node_name="retrieval.search",
                duration_ms=int((perf_counter() - started) * 1000),
                status="ok",
                index_version_hash=hash_trace_value(self.child_index),
                prompt_version_hash=hash_trace_value("retrieval-v1"),
                model_hash=hash_trace_value("embedding"),
                owner_hash=hash_trace_value(policy.user_id),
                query_hash=hash_trace_value(task.query),
                document_hashes=[
                    hash_trace_value(item.document_id) for item in result.evidence
                ],
                evidence_count=len(result.evidence),
                retrieval_mode=result.mode,
                error_class=result.embedding_error_class,
            ),
        )
        return result

    async def _search(
        self,
        task: SearchTask,
        policy: PolicyContext,
    ) -> RetrievalResult:
        if task.source == "statistics":
            return await self._statistics(task.filters, policy)

        index_name = self.wiki_index if task.source == "wiki" else self.child_index
        bm25_body = self._bm25_body(task, policy)
        embedding_error = None
        embedding_error_class = None
        disclosures: list[str] = []

        try:
            vector = await self.embeddings.embed(task.query)
        except Exception as error:
            bm25_response = await self.backend.search(index_name, bm25_body)
            rankings = [self._rank(bm25_response)]
            mode: Literal["hybrid", "bm25"] = "bm25"
            embedding_error = "EMBEDDING_UNAVAILABLE"
            embedding_error_class = type(error).__name__
            disclosures = [BM25_FALLBACK_DISCLOSURE]
        else:
            vector_body = self._vector_body(task, policy, vector)
            vector_result, bm25_result = await asyncio.gather(
                self.backend.search(index_name, vector_body),
                self.backend.search(index_name, bm25_body),
                return_exceptions=True,
            )
            if isinstance(bm25_result, Exception):
                raise bm25_result
            if isinstance(vector_result, Exception):
                rankings = [self._rank(bm25_result)]
                mode = "bm25"
                embedding_error = "EMBEDDING_UNAVAILABLE"
                embedding_error_class = type(vector_result).__name__
                disclosures = [BM25_FALLBACK_DISCLOSURE]
            else:
                rankings = [self._rank(vector_result), self._rank(bm25_result)]
                mode = "hybrid"

        fused_hits = reciprocal_rank_fusion(rankings)
        raw_hits = [
            {**item.raw, "_rrf_score": item.score} for item in fused_hits[: task.top_k]
        ]
        if task.source == "mail":
            raw_hits = await self._expand_mail_context(raw_hits, policy, task)

        evidence = self._materialize(raw_hits, task, policy)
        return RetrievalResult(
            evidence=evidence,
            mode=mode,
            embedding_error=embedding_error,
            embedding_error_class=embedding_error_class,
            disclosures=disclosures,
        )

    @staticmethod
    def _filters(policy: PolicyContext, facets: RetrievalFilters) -> list[dict]:
        return build_owner_filters(policy, facets)

    def _bm25_body(self, task: SearchTask, policy: PolicyContext) -> dict:
        return {
            "size": task.top_k * 3,
            "query": {
                "bool": {
                    "filter": self._filters(policy, task.filters),
                    "must": [
                        {
                            "match": {
                                "text": {
                                    "query": task.query,
                                    "analyzer": "korean",
                                }
                            }
                        }
                    ],
                }
            },
        }

    def _vector_body(
        self,
        task: SearchTask,
        policy: PolicyContext,
        vector: list[float],
    ) -> dict:
        return {
            "size": task.top_k * 3,
            "query": {
                "bool": {
                    "filter": self._filters(policy, task.filters),
                    "must": [
                        {
                            "knn": {
                                "embedding": {
                                    "vector": vector,
                                    "k": task.top_k * 3,
                                }
                            }
                        }
                    ],
                }
            },
        }

    @staticmethod
    def _rank(response: dict) -> list[RankedHit]:
        ranked = []
        for rank, hit in enumerate(response.get("hits", {}).get("hits", []), 1):
            document_id = hit.get("_id")
            if document_id is not None:
                ranked.append(RankedHit(str(document_id), rank, hit))
        return ranked

    async def _expand_mail_context(
        self,
        hits: list[dict[str, Any]],
        policy: PolicyContext,
        task: SearchTask,
    ) -> list[dict[str, Any]]:
        owner_hits = [
            hit
            for hit in hits
            if hit.get("_source", {}).get("user_id") == policy.user_id
        ]
        if not self.parent_index:
            return await self._expand_legacy(owner_hits, policy, task)

        parent_ids = list(
            dict.fromkeys(
                str(hit["_source"]["parent_id"])
                for hit in owner_hits
                if hit.get("_source", {}).get("parent_id")
            )
        )
        if not parent_ids:
            return await self._expand_legacy(owner_hits, policy, task)

        filters = self._filters(policy, task.filters)
        filters.append({"terms": {"parent_id": parent_ids}})
        body = {
            "size": min(100, len(parent_ids)),
            "query": {"bool": {"filter": filters}},
        }
        response = await self.backend.search(self.parent_index, body)

        parents_by_id: dict[str, dict[str, Any]] = {}
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            parent_id = str(source.get("parent_id") or "")
            if (
                source.get("user_id") != policy.user_id
                or parent_id not in parent_ids
                or not (source.get("text") or source.get("content"))
            ):
                continue
            parents_by_id.setdefault(parent_id, hit)

        parent_scores: dict[str, float] = {}
        fallback_hits = []
        for hit in owner_hits:
            parent_id = str(hit.get("_source", {}).get("parent_id") or "")
            if parent_id in parents_by_id:
                score = float(hit.get("_rrf_score") or hit.get("_score") or 0)
                parent_scores[parent_id] = max(
                    parent_scores.get(parent_id, 0),
                    score,
                )
            else:
                fallback_hits.append(hit)

        expanded_parents = [
            {
                **parents_by_id[parent_id],
                "_rrf_score": parent_scores[parent_id],
            }
            for parent_id in parent_ids
            if parent_id in parents_by_id
        ]
        if fallback_hits:
            expanded_parents.extend(
                await self._expand_legacy(fallback_hits, policy, task)
            )
        return expanded_parents

    async def _expand_legacy(
        self,
        hits: list[dict[str, Any]],
        policy: PolicyContext,
        task: SearchTask,
    ) -> list[dict[str, Any]]:
        owner_hits = [
            hit
            for hit in hits
            if hit.get("_source", {}).get("user_id") == policy.user_id
        ]
        mail_ids = list(
            dict.fromkeys(
                str(hit["_source"]["mail_id"])
                for hit in owner_hits
                if hit.get("_source", {}).get("mail_id")
            )
        )
        if not mail_ids:
            return owner_hits
        seed_by_mail = {
            mail_id: next(
                hit
                for hit in owner_hits
                if str(hit["_source"].get("mail_id")) == mail_id
            )
            for mail_id in mail_ids
        }
        neighborhoods: dict[str, set[int]] = {mail_id: set() for mail_id in mail_ids}
        for hit in owner_hits:
            source = hit["_source"]
            mail_id = str(source.get("mail_id"))
            try:
                part_index = int(source.get("part_index"))
            except (TypeError, ValueError):
                continue
            neighborhoods[mail_id].update(
                index for index in range(part_index - 1, part_index + 2) if index >= 0
            )
        fused_scores: dict[str, float] = {}
        for hit in owner_hits:
            mail_id = str(hit["_source"].get("mail_id") or "")
            score = float(hit.get("_rrf_score") or 0)
            fused_scores[mail_id] = max(fused_scores.get(mail_id, 0), score)

        filters = self._filters(policy, task.filters)
        filters.append({"terms": {"mail_id": mail_ids}})
        neighborhood_queries = [
            {
                "bool": {
                    "filter": [
                        {"term": {"mail_id": mail_id}},
                        {"terms": {"part_index": sorted(part_indexes)}},
                    ]
                }
            }
            for mail_id, part_indexes in neighborhoods.items()
            if part_indexes
        ]
        if not neighborhood_queries:
            return owner_hits
        body = {
            "size": min(100, sum(len(items) for items in neighborhoods.values())),
            "sort": [{"mail_id": "asc"}, {"part_index": "asc"}],
            "query": {
                "bool": {
                    "filter": filters,
                    "must": [
                        {
                            "bool": {
                                "should": neighborhood_queries,
                                "minimum_should_match": 1,
                            }
                        }
                    ],
                }
            },
        }
        response = await self.backend.search(self.child_index, body)

        by_mail: dict[str, list[dict[str, Any]]] = {}
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source", {})
            mail_id = str(source.get("mail_id") or "")
            try:
                part_index = int(source.get("part_index"))
            except (TypeError, ValueError):
                continue
            if (
                source.get("user_id") != policy.user_id
                or mail_id not in mail_ids
                or part_index not in neighborhoods[mail_id]
            ):
                continue
            by_mail.setdefault(mail_id, []).append(hit)

        if not by_mail:
            return owner_hits
        expanded = []
        for mail_id in mail_ids:
            if mail_id not in by_mail:
                continue
            parts = sorted(
                by_mail[mail_id],
                key=lambda item: int(item["_source"]["part_index"]),
            )
            seed_part = int(seed_by_mail[mail_id]["_source"].get("part_index") or 0)
            base = min(
                parts,
                key=lambda item: abs(int(item["_source"]["part_index"]) - seed_part),
            )
            context = "\n\n".join(
                str(item["_source"].get("text") or "")
                for item in parts
                if item["_source"].get("text")
            )
            source = {**base["_source"], "text": context}
            source.pop("content_hash", None)
            expanded.append(
                {
                    **base,
                    "_source": source,
                    "_rrf_score": fused_scores.get(mail_id, 0),
                }
            )
        return expanded

    def _materialize(
        self,
        hits: list[dict[str, Any]],
        task: SearchTask,
        policy: PolicyContext,
    ) -> list[Evidence]:
        evidence = []
        for hit in hits:
            source = hit.get("_source", {})
            if source.get("user_id") != policy.user_id:
                continue
            text = str(source.get("text") or source.get("content") or "")
            document_id = hit.get("_id")
            if not text or document_id is None:
                continue
            evidence.append(
                Evidence(
                    evidence_id=f"S{len(evidence) + 1}",
                    source_type="wiki" if task.source == "wiki" else "mail",
                    document_id=str(document_id),
                    parent_id=source.get("parent_id"),
                    title=str(source.get("subject") or source.get("title") or ""),
                    excerpt=text[:8000],
                    team=source.get("team"),
                    week=source.get("week"),
                    source_locator=self._safe_locator(source.get("document_locator")),
                    score=float(hit.get("_rrf_score") or hit.get("_score") or 0),
                    user_id=policy.user_id,
                    acl_decision_id=policy.decision_id,
                    content_hash=str(
                        source.get("content_hash") or sha256(text.encode()).hexdigest()
                    ),
                )
            )
            if len(evidence) >= task.top_k:
                break
        return evidence

    @staticmethod
    def _safe_locator(value: object) -> str | None:
        locator = str(value or "").strip()
        if re.fullmatch(r"/v1/mail-content/[0-9a-f]{64}", locator):
            return locator
        if locator.startswith(("mail:", "wiki:")):
            if re.fullmatch(
                r"(?:mail|wiki):[A-Za-z0-9_.@+-]+(?:#[A-Za-z0-9_.@+-]+)?",
                locator,
            ):
                return locator
            return None
        if locator.startswith(("https://", "http://")):
            parsed = urlsplit(locator)
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                return None
            return locator
        return None

    async def _statistics(
        self,
        facets: RetrievalFilters,
        policy: PolicyContext,
    ) -> RetrievalResult:
        body = {
            "size": 0,
            "query": {"bool": {"filter": self._filters(policy, facets)}},
            "aggs": {
                "by_team": {"terms": {"field": "team", "size": 100}},
                "by_mail_type": {"terms": {"field": "mail_type", "size": 10}},
            },
        }
        response = await self.backend.search(self.child_index, body)
        excerpt = json.dumps(
            response.get("aggregations", {}),
            ensure_ascii=False,
            sort_keys=True,
        )
        evidence = Evidence(
            evidence_id="S1",
            source_type="statistic",
            document_id=f"statistics:{policy.decision_id}",
            title="메일 통계",
            excerpt=excerpt or "{}",
            score=1,
            user_id=policy.user_id,
            acl_decision_id=policy.decision_id,
            content_hash=sha256(excerpt.encode()).hexdigest(),
        )
        return RetrievalResult(evidence=[evidence], mode="bm25")
