"""Server-owned four-source searches; no generated query DSL is accepted."""

import asyncio
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pydantic import Field
from app.domain.quick_rag import (
    Strict,
    QuickPlan,
    QuickResultRef,
    resolve_dates,
    normalize_content_topic,
)

CAPABILITIES = {
    "weekly_mail": ["teams", "weeks", "mail_type"],
    "wiki_summary": [
        "teams",
        "weeks",
        "topic",
        "summary_type",
        "date_start",
        "date_end",
        "date_period",
    ],
    "ews_mail": [
        "teams",
        "weeks",
        "mail_type",
        "sender_name",
        "sender_email",
        "attachment_name",
        "date_start",
        "date_end",
        "date_period",
    ],
    "ews_calendar": [
        "organizer_email",
        "attendee_email",
        "location",
        "date_start",
        "date_end",
        "date_period",
    ],
}
FIELDS = {
    "weekly_mail": [
        "title",
        "subject",
        "text",
        "page_content",
        "body",
        "team",
        "week",
        "mail_type",
        "source_id",
        "user_id",
    ],
    "wiki_summary": [
        "title",
        "text",
        "summary",
        "team",
        "week",
        "topic",
        "summary_type",
        "source_id",
        "user_id",
    ],
    "ews_mail": [
        "title",
        "subject",
        "text",
        "page_content",
        "body",
        "sender_name",
        "sender_email",
        "received_at",
        "sent_at",
        "source_id",
        "chunk_index",
        "employee_id",
    ],
    "ews_calendar": [
        "title",
        "subject",
        "text",
        "page_content",
        "body",
        "organizer_email",
        "attendee_emails",
        "start_at_utc",
        "end_at_utc",
        "location",
        "source_id",
        "employee_id",
    ],
}

FIELDS["wiki_summary"].extend(["updated_at", "created_at"])
for source_fields in FIELDS.values():
    source_fields.extend(["mail_id", "html_path", "content_id"])


class QuickDocument(Strict):
    source: str
    id: str
    original_id: str
    title: str = ""
    text: str = ""
    metadata: dict = Field(default_factory=dict)
    score: float = 0


def merge_matching_passage(existing, incoming):
    """Keep a newly matched section even when it belongs to a known parent."""
    text = incoming.text.strip()
    if not text or text in existing.text:
        return False
    # Bound context while reserving space for the new actual match.
    incoming_text = text[:3000]
    existing.text = (
        existing.text[: 6000 - len(incoming_text) - 2] + "\n\n" + incoming_text
    )
    return True


class QuickSearchOutcome(Strict):
    documents: list[QuickDocument] = Field(default_factory=list)
    disclosures: list[str] = Field(default_factory=list)
    failed_sources: list[str] = Field(default_factory=list)
    searched_sources: list[str] = Field(default_factory=list)
    vector_sources: list[str] = Field(default_factory=list)
    vector_required_failed: bool = False
    selected_passage_count: int = 0
    supplemental_batches: int = 0
    executed_queries: list[str] = Field(default_factory=list)
    investigation_stop: str = ""
    clarification: str = ""
    retrieval_mode: str = "deterministic"
    resolved_plan: QuickPlan | None = None


def source_names(scope):
    return {
        "all": list(CAPABILITIES),
        "mail": ["weekly_mail", "ews_mail"],
        "calendar": ["ews_calendar"],
        "wiki": ["wiki_summary"],
        "report": ["weekly_mail"],
    }[scope]


def normalize_name(v):
    return (
        re.sub("\\s*(?:TL|팀장|부장|과장|대리|사원|책임|수석)\\s*$", "", v, flags=re.I)
        .strip()
        .replace(" ", "")
    )


class QuickRetrieval:

    def __init__(
        self,
        backend,
        embeddings,
        *,
        weekly_index,
        mail_index,
        calendar_index,
        wiki_index,
        shared_sources=False,
        source_timeout_seconds=4,
        compatible_sources=(),
        require_vector=False,
    ):
        self.require_vector = require_vector
        self.backend = backend
        self.embeddings = embeddings
        self.shared_sources = shared_sources
        self.source_timeout_seconds = source_timeout_seconds
        self.compatible_sources = frozenset(compatible_sources)
        self.indices = dict(
            zip(CAPABILITIES, [weekly_index, wiki_index, mail_index, calendar_index])
        )

    def capabilities(self):
        return CAPABILITIES

    def acl(self, source, policy):
        if source.startswith("ews_"):
            return [
                {"term": {"employee_id": policy.user_id}},
                {"term": {"is_active": True}},
            ] + (
                [{"term": {"is_cancelled": False}}] if source == "ews_calendar" else []
            )
        return [] if self.shared_sources else [{"term": {"user_id": policy.user_id}}]

    @staticmethod
    def original_field(source):
        return (
            "mail_id"
            if source == "weekly_mail"
            else "source_id" if source.startswith("ews_") else None
        )

    async def expand_documents(self, documents, policy):
        """Read surrounding chunks only for already retrieved, ACL-authorized parents."""

        async def expand(d):
            field = self.original_field(d.source)
            if not field:
                return
            body = {
                "size": 8,
                "_source": FIELDS[d.source],
                "query": {
                    "bool": {
                        "filter": [
                            *self.acl(d.source, policy),
                            {"term": {field: d.original_id}},
                        ]
                    }
                },
            }
            if d.source == "ews_mail":
                body["sort"] = [
                    {"chunk_index": {"order": "asc", "unmapped_type": "long"}}
                ]
                chunk = d.metadata.get("chunk_index")
                if isinstance(chunk, int):
                    body["query"]["bool"]["filter"].append(
                        {
                            "range": {
                                "chunk_index": {
                                    "gte": max(0, chunk - 3),
                                    "lte": chunk + 4,
                                }
                            }
                        }
                    )
            try:
                result = await asyncio.wait_for(
                    self.backend.search(self.indices[d.source], body), 2
                )
                hits = result.get("hits", {}).get("hits", [])
                chunks = sorted(
                    (h.get("_source", {}) for h in hits),
                    key=lambda v: v.get("chunk_index", 0),
                )
                texts = list(
                    dict.fromkeys(
                        str(
                            v.get("text")
                            or v.get("page_content")
                            or v.get("body")
                            or ""
                        )
                        for v in chunks
                    )
                )
                full = "\n".join(t for t in texts if t)
                if len(full) > len(d.text):
                    # Never replace the known matched passage with unrelated parent chunks.
                    matched = d.text
                    d.text = (
                        full[:6000]
                        if matched in full[:6000]
                        else (matched + "\n" + full)[:6000]
                    )
            except Exception:
                # Retain the known original excerpt on expansion failure.
                pass

        await asyncio.gather(*(expand(d) for d in documents[:10]))

    async def _resolve_name(self, plan, policy):
        name = normalize_name(plan.conditions.sender_name)
        body = {
            "size": 0,
            "query": {
                "bool": {
                    "filter": self.acl("ews_mail", policy),
                    "must": [
                        {
                            "bool": {
                                "should": [
                                    {
                                        "term": {
                                            "sender_name.keyword": plan.conditions.sender_name
                                        }
                                    },
                                    {"term": {"sender_name.keyword": name}},
                                    {"term": {"sender_name.keyword": name + " TL"}},
                                ],
                                "minimum_should_match": 1,
                            }
                        }
                    ],
                }
            },
            "aggs": {"identities": {"terms": {"field": "sender_email", "size": 3}}},
        }
        r = await asyncio.wait_for(
            self.backend.search(self.indices["ews_mail"], body),
            self.source_timeout_seconds,
        )
        emails = {
            str(b["key"]).casefold()
            for b in r.get("aggregations", {}).get("identities", {}).get("buckets", [])
        }
        if len(emails) > 1:
            return (
                None,
                "같은 이름에 여러 이메일이 있습니다. 발신자 이메일을 지정해 주세요.",
            )
        if not emails:
            return (None, "해당 이름의 발신자를 찾지 못했습니다.")
        resolved = plan.model_copy(deep=True)
        resolved.conditions.sender_email = next(iter(emails))
        resolved.conditions.sender_name = None
        return (resolved, "")

    async def search_expanded(self, plan, policy, **kwargs):
        """Parallel hybrid base + keyword alternatives, with weighted parent-level RRF."""
        import time

        plan = normalize_content_topic(plan)
        budget = 9.0 if self.require_vector else 4.0
        deadline = time.monotonic() + budget
        if plan.intent != "answer" or (
            not plan.search_queries
            and not (
                plan.investigate
                and (plan.conditions.sender_name or plan.conditions.sender_email)
            )
        ):
            try:
                result = await asyncio.wait_for(
                    self.search(plan, policy, **kwargs), budget
                )
            except TimeoutError:
                return QuickSearchOutcome(
                    vector_required_failed=self.require_vector and bool(plan.query),
                    failed_sources=source_names(plan.source_scope),
                    disclosures=["초기 검색 시간 한도에 도달했습니다."],
                    resolved_plan=plan,
                )
            result.executed_queries = [plan.query] if plan.query else []
            return result
        # Resolve identity once before fan-out; no lane may weaken filters.
        if plan.conditions.sender_name:
            try:
                resolved, clarification = await asyncio.wait_for(
                    self._resolve_name(plan, policy), 1.5
                )
            except Exception:
                return QuickSearchOutcome(
                    failed_sources=["ews_mail"],
                    disclosures=["ews_mail: 발신자 확인 실패"],
                )
            if clarification:
                return QuickSearchOutcome(clarification=clarification)
            plan = resolved
        queries = list(
            dict.fromkeys(
                q.strip() for q in [plan.query, *plan.search_queries] if q.strip()
            )
        )[:3]
        # Unknown issue discovery: inspect a bounded sender-scoped sample alongside lexical lanes.
        # This changes only content keywords, never sender/date/ACL constraints.
        if plan.investigate and plan.scope == "new" and plan.conditions.sender_email:
            queries.append("")

        async def lane(query, index):
            candidate = plan.model_copy(deep=True)
            candidate.query = query
            options = dict(kwargs)
            if index:
                options["keyword_only"] = True
            return await asyncio.wait_for(
                self.search(candidate, policy, **options),
                max(0.1, deadline - time.monotonic()),
            )

        results = await asyncio.gather(
            *(lane(q, i) for i, q in enumerate(queries)), return_exceptions=True
        )
        base = results[0]
        if (
            self.require_vector
            and queries[0]
            and (isinstance(base, Exception) or base.vector_required_failed)
        ):
            return QuickSearchOutcome(
                resolved_plan=plan,
                vector_required_failed=True,
                failed_sources=source_names(plan.source_scope),
                executed_queries=queries,
                disclosures=[
                    "벡터 검색을 완료하지 못해 키워드 결과만으로 답변하지 않았습니다."
                ],
            )
        merged = QuickSearchOutcome(
            resolved_plan=plan,
            executed_queries=[q or "[동일 조건 메일 표본 조회]" for q in queries],
        )
        scores, documents = {}, {}
        modes = []
        for lane_index, result in enumerate(results):
            if isinstance(result, Exception):
                merged.failed_sources.extend(source_names(plan.source_scope))
                merged.disclosures.append(
                    "확장 검색 일부 실패: 다른 검색 결과로 답변합니다."
                )
                continue
            if result.clarification:
                return result
            modes.append(result.retrieval_mode)
            merged.vector_sources = list(
                dict.fromkeys([*merged.vector_sources, *result.vector_sources])
            )
            merged.searched_sources.extend(result.searched_sources)
            merged.failed_sources.extend(result.failed_sources)
            merged.disclosures.extend(result.disclosures)
            for rank, document in enumerate(result.documents, 1):
                key = (document.source, document.original_id)
                if key in documents:
                    merge_matching_passage(documents[key], document)
                else:
                    documents[key] = document
                scores[key] = scores.get(key, 0) + (1.2 if lane_index == 0 else 1.0) / (
                    60 + rank
                )
        keys = sorted(documents, key=lambda key: -scores[key])[:10]
        merged.documents = [
            documents[key].model_copy(update={"score": scores[key]}) for key in keys
        ]
        merged.disclosures = list(dict.fromkeys(merged.disclosures))
        merged.failed_sources = list(dict.fromkeys(merged.failed_sources))
        merged.retrieval_mode = (
            "hybrid" if "hybrid" in modes else modes[0] if modes else "deterministic"
        )
        return merged

    async def search(
        self,
        plan,
        policy,
        *,
        prior_ids=None,
        now=None,
        timezone_name="Asia/Seoul",
        keyword_only=False,
        embedding_timeout_seconds=None,
    ):
        plan = normalize_content_topic(plan)
        outcome = QuickSearchOutcome(resolved_plan=plan)
        prior_ids = prior_ids or []
        if plan.conditions.sender_name:
            outcome.searched_sources.append("ews_mail")
            try:
                resolved, clarification = await self._resolve_name(plan, policy)
            except Exception:
                outcome.failed_sources = ["ews_mail"]
                outcome.disclosures = ["ews_mail: 발신자 확인 실패"]
                return outcome
            if clarification:
                outcome.clarification = clarification
                return outcome
            plan = resolved
            outcome.resolved_plan = plan
        clock = now or datetime.now(timezone.utc)
        bounds = resolve_dates(plan, clock, timezone_name)

        embedding_task = None

        async def shared_embedding():
            nonlocal embedding_task
            if embedding_task is None:
                embedding_task = asyncio.create_task(self.embeddings.embed(plan.query))
            return await asyncio.wait_for(
                asyncio.shield(embedding_task),
                (
                    min(7.5, embedding_timeout_seconds or 7.5)
                    if self.require_vector
                    else min(
                        self.source_timeout_seconds * 2 / 3,
                        embedding_timeout_seconds
                        or self.source_timeout_seconds * 2 / 3,
                    )
                ),
            )

        async def run(source):
            unsupported = [
                k
                for k, v in plan.conditions.model_dump().items()
                if v and k not in CAPABILITIES[source]
            ]
            if (bounds.start or bounds.end) and "date_start" not in CAPABILITIES[
                source
            ]:
                unsupported.append("exact_date")
            if unsupported:
                return (
                    source,
                    [],
                    [
                        f"{source}: 지원하지 않는 조건({', '.join(unsupported)})으로 검색 제외"
                    ],
                    False,
                    "deterministic",
                )
            filters = self.acl(source, policy)
            c = plan.conditions
            for key, field in [
                ("teams", "team"),
                ("weeks", "week"),
                ("mail_type", "mail_type"),
                ("topic", "topic"),
                ("summary_type", "summary_type"),
                ("sender_email", "sender_email"),
                ("organizer_email", "organizer_email"),
                ("attendee_email", "attendee_emails"),
                ("attachment_name", "attachment_name"),
                ("location", "location"),
            ]:
                value = getattr(c, key)
                if value:
                    if isinstance(value, list):
                        filters.append({"terms": {field: value}})
                    elif key.endswith("email"):
                        filters.append(
                            {
                                "term": {
                                    field: {"value": value, "case_insensitive": True}
                                }
                            }
                        )
                    elif key == "location":
                        filters.append({"match_phrase": {field: value}})
                    else:
                        filters.append({"term": {field: value}})
            if bounds.start or bounds.end:
                if source == "ews_calendar":
                    if bounds.end:
                        filters.append(
                            {"range": {"start_at_utc": {"lt": bounds.end.isoformat()}}}
                        )
                    if bounds.start:
                        filters.append(
                            {"range": {"end_at_utc": {"gt": bounds.start.isoformat()}}}
                        )
                else:
                    filters.append(
                        {
                            "range": {
                                (
                                    "updated_at"
                                    if source == "wiki_summary"
                                    else "received_at"
                                ): {
                                    **(
                                        {"gte": bounds.start.isoformat()}
                                        if bounds.start
                                        else {}
                                    ),
                                    **(
                                        {"lt": bounds.end.isoformat()}
                                        if bounds.end
                                        else {}
                                    ),
                                }
                            }
                        }
                    )
            if plan.scope == "within_results":
                ids = [r.id for r in prior_ids if r.source == source]
                if not ids:
                    return (source, [], [], False, "deterministic")
                filters.append(
                    {
                        "bool": {
                            "should": [
                                {"ids": {"values": ids}},
                                {
                                    "terms": {
                                        self.original_field(source) or "content_id": ids
                                    }
                                },
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )
            metadata = plan.intent == "list" or not plan.query.strip()
            size = 10 if metadata else 5
            query = {"bool": {"filter": filters}}
            if plan.query:
                query["bool"]["must"] = [
                    {
                        "multi_match": {
                            "query": plan.query,
                            **({"operator": "and"} if keyword_only else {}),
                            "fields": [
                                "title^3",
                                "subject^3",
                                "text",
                                "page_content",
                                "body",
                                "summary",
                            ],
                        }
                    }
                ]
            upcoming = (
                plan.prefer_upcoming
                and source == "ews_calendar"
                and not (bounds.start or bounds.end)
            )
            if upcoming:
                query["bool"]["should"] = [
                    {"range": {"start_at_utc": {"gte": clock.isoformat(), "boost": 3}}}
                ]
            date_field = (
                "start_at_utc"
                if source == "ews_calendar"
                else (
                    "received_at"
                    if source == "ews_mail"
                    else "updated_at" if source == "wiki_summary" else "week"
                )
            )
            body = {"size": size, "_source": FIELDS[source], "query": query}
            if metadata:
                original_field = self.original_field(source)
                tie_sort = (
                    {original_field: {"order": "asc", "missing": "_last"}}
                    if original_field
                    else {"_doc": "asc"}
                )
                body["sort"] = [
                    {date_field: {"order": "desc", "missing": "_last"}},
                    tie_sort,
                ]
                if original_field:
                    body["collapse"] = {"field": original_field}

            async def execute():
                disclosure = []
                if source == "wiki_summary" and (bounds.start or bounds.end):
                    disclosure.append(
                        "위키 기간 조건은 문서 수정 시각(updated_at) 기준이며 사건 발생일이 아닙니다."
                    )
                mode = "deterministic" if metadata else "bm25"
                bm_task = asyncio.create_task(
                    self.backend.search(self.indices[source], body)
                )
                vector_result = None
                try:
                    if (
                        not metadata
                        and not keyword_only
                        and source not in self.compatible_sources
                    ):
                        disclosure.append(
                            f"{source}: 임베딩 모델 호환성이 확인되지 않아 키워드 검색만 사용했습니다."
                        )
                    if (
                        not metadata
                        and not keyword_only
                        and source in self.compatible_sources
                    ):
                        try:
                            vector_stage = "질문 임베딩 생성"
                            vector = await shared_embedding()
                            vector_stage = "벡터 인덱스 조회"
                            vb = {
                                **body,
                                "query": {
                                    "bool": {
                                        "filter": filters,
                                        "must": [
                                            {
                                                "knn": {
                                                    "embedding": {
                                                        "vector": vector,
                                                        "k": 5,
                                                    }
                                                }
                                            }
                                        ],
                                    }
                                },
                            }
                            vector_result = await asyncio.wait_for(
                                self.backend.search(self.indices[source], vb),
                                (
                                    2.0
                                    if self.require_vector
                                    else self.source_timeout_seconds / 3
                                ),
                            )
                            mode = "hybrid"
                        except Exception as error:
                            reason = (
                                "시간 초과"
                                if isinstance(error, TimeoutError)
                                else type(error).__name__
                            )
                            disclosure.append(
                                f"{source}: {vector_stage} {reason}로 키워드 검색만 사용했습니다."
                            )
                    if (
                        self.require_vector
                        and not metadata
                        and not keyword_only
                        and vector_result is None
                    ):
                        return (
                            source,
                            [],
                            [
                                f"{source}: 벡터 검색 미완료. 키워드 단독 답변을 중단했습니다."
                            ],
                            True,
                            "failed_vector",
                        )
                    bm = await bm_task
                finally:
                    if not bm_task.done():
                        bm_task.cancel()
                        await asyncio.gather(bm_task, return_exceptions=True)
                rankings = [bm.get("hits", {}).get("hits", [])] + (
                    [vector_result.get("hits", {}).get("hits", [])]
                    if vector_result
                    else []
                )
                unique = {}
                scores = {}
                for hits in rankings:
                    for rank, h in enumerate(hits):
                        raw = h.get("_source", {})
                        original = str(
                            raw.get(self.original_field(source) or "") or h["_id"]
                        )
                        key = original
                        if key not in unique:
                            unique[key] = h
                        scores[key] = scores.get(key, 0) + 1 / (60 + rank + 1)
                selected = (
                    list(unique)
                    if metadata
                    else sorted(unique, key=lambda x: -scores[x])
                )
                docs = []
                for key in selected[:size]:
                    h = unique[key]
                    raw = h.get("_source", {})
                    title = str(raw.get("title") or raw.get("subject") or "")[:500]
                    text = str(
                        raw.get("text")
                        or raw.get("page_content")
                        or raw.get("body")
                        or raw.get("summary")
                        or title
                        or "메타데이터"
                    )[:1800]
                    docs.append(
                        QuickDocument(
                            source=source,
                            id=str(h["_id"]),
                            original_id=key,
                            title=title,
                            text=text,
                            metadata={
                                k: v
                                for k, v in raw.items()
                                if k in FIELDS[source]
                                and k
                                not in (
                                    "text",
                                    "page_content",
                                    "body",
                                    "summary",
                                    "employee_id",
                                    "user_id",
                                )
                            },
                            score=scores[key],
                        )
                    )
                if upcoming:

                    def event_order(document):
                        try:
                            start = datetime.fromisoformat(
                                str(document.metadata.get("start_at_utc", "")).replace(
                                    "Z", "+00:00"
                                )
                            )
                            if start.tzinfo is None:
                                start = start.replace(tzinfo=timezone.utc)
                            return (
                                start < clock,
                                (
                                    start.timestamp()
                                    if start >= clock
                                    else -start.timestamp()
                                ),
                                document.original_id,
                            )
                        except ValueError:
                            return (True, float("inf"), document.original_id)

                    docs.sort(key=event_order)
                return (source, docs, disclosure, True, mode)

            try:
                return await asyncio.wait_for(
                    execute(),
                    (
                        9.0
                        if self.require_vector and not metadata and not keyword_only
                        else self.source_timeout_seconds
                    ),
                )
            except Exception:
                return (
                    source,
                    [],
                    [f"{source}: 검색 실패 또는 시간 초과"],
                    True,
                    (
                        "failed_vector"
                        if self.require_vector and plan.query and not keyword_only
                        else "failed"
                    ),
                )

        try:
            rows = await asyncio.gather(
                *(run(s) for s in source_names(plan.source_scope))
            )
        finally:
            if embedding_task is not None:
                if not embedding_task.done():
                    embedding_task.cancel()
                await asyncio.gather(embedding_task, return_exceptions=True)
        modes = []
        for source, docs, disclosures, searched, mode in rows:
            outcome.documents.extend(docs)
            outcome.disclosures.extend(disclosures)
            if searched and source not in outcome.searched_sources:
                outcome.searched_sources.append(source)
            if mode == "hybrid":
                outcome.vector_sources.append(source)
            if mode == "failed_vector":
                outcome.vector_required_failed = True
            if mode in ("failed", "failed_vector"):
                outcome.failed_sources.append(source)
            else:
                modes.append(mode)
        outcome.retrieval_mode = (
            "hybrid"
            if "hybrid" in modes
            else "bm25" if "bm25" in modes else "deterministic"
        )
        if plan.intent == "list":

            def date(document):
                raw = (
                    document.metadata.get("received_at")
                    or document.metadata.get("start_at_utc")
                    or document.metadata.get("updated_at")
                    or document.metadata.get("created_at")
                )
                try:
                    if raw:
                        stamp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                        if stamp.tzinfo is None:
                            stamp = stamp.replace(tzinfo=ZoneInfo(timezone_name))
                        return stamp.timestamp()
                    week = str(document.metadata.get("week", ""))
                    year, number = week.split("-")
                    return (
                        datetime.fromisocalendar(int(year), int(number), 1)
                        .replace(tzinfo=ZoneInfo(timezone_name))
                        .timestamp()
                    )
                except (ValueError, TypeError):
                    return float("-inf")

            def list_order(document):
                stamp = date(document)
                if (
                    plan.prefer_upcoming
                    and document.source == "ews_calendar"
                    and not (bounds.start or bounds.end)
                ):
                    future = stamp >= clock.timestamp()
                    return (
                        0 if future else 2,
                        stamp if future else -stamp,
                        document.source,
                        document.original_id,
                    )
                return (1, -stamp, document.source, document.original_id)

            outcome.documents.sort(key=list_order)
            if plan.source_scope == "all":
                represented = set()
                reserved = []
                for document in outcome.documents:
                    if document.source not in represented:
                        represented.add(document.source)
                        reserved.append(document)
                picked = {(d.source, d.original_id) for d in reserved}
                reserved.extend(
                    d
                    for d in outcome.documents
                    if (d.source, d.original_id) not in picked
                )
                outcome.documents = sorted(reserved[:10], key=list_order)
            if any(
                d.source in ("weekly_mail", "wiki_summary")
                and d.metadata.get("week")
                and not any(
                    d.metadata.get(key)
                    for key in (
                        "received_at",
                        "start_at_utc",
                        "updated_at",
                        "created_at",
                    )
                )
                for d in outcome.documents[:10]
            ):
                outcome.disclosures.append(
                    "일부 주간 자료의 최신순은 주 단위 기준이며 정확한 발송·수정 시각을 나타내지 않습니다."
                )
        if plan.intent != "list":
            groups = [
                [d for d in outcome.documents if d.source == source]
                for source in source_names(plan.source_scope)
            ]
            outcome.documents = [
                d
                for rank in range(5)
                for group in groups
                for d in group[rank : rank + 1]
            ][:8]
        else:
            outcome.documents = outcome.documents[:10]
        return outcome
