from pydantic import BaseModel, Field

from app.domain.policy import PolicyContext
from app.retrieval.opensearch import OpenSearchGateway
from app.security.redaction import sanitize_text


class CorpusInfo(BaseModel):
    document_count: int = Field(ge=0)
    teams: dict[str, int] = Field(default_factory=dict)
    first_week: str | None = None
    last_week: str | None = None
    mail_types: dict[str, int] = Field(default_factory=dict)
    embedding_models: list[str] = Field(default_factory=list)
    recent_titles: list[str] = Field(default_factory=list)


class CorpusInfoService:
    """Owner-scoped, aggregate-only index inventory."""

    def __init__(self, search: OpenSearchGateway, index: str):
        self.search = search
        self.index = index

    @staticmethod
    def _counts(aggregation: dict) -> dict[str, int]:
        counts = {}
        for bucket in aggregation.get("buckets", []):
            key = sanitize_text(str(bucket.get("key", "")))
            if key:
                counts[key[:100]] = int(bucket.get("doc_count", 0))
        return counts

    async def inspect(self, policy: PolicyContext) -> CorpusInfo:
        body = {
            "size": 0,
            "query": {"bool": {"filter": [{"term": {"user_id": policy.user_id}}]}},
            "aggs": {
                "teams": {"terms": {"field": "team", "size": 100}},
                "weeks": {
                    "terms": {"field": "week", "size": 100, "order": {"_key": "asc"}}
                },
                "mail_types": {"terms": {"field": "mail_type", "size": 20}},
                "embedding_models": {
                    "terms": {"field": "embedding_model", "size": 20}
                },
                "recent_documents": {
                    "top_hits": {
                        "size": 5,
                        "sort": [{"indexed_at": {"order": "desc", "unmapped_type": "date"}}],
                        "_source": ["subject", "title"],
                    }
                },
            },
        }
        response = await self.search.search(self.index, body)
        aggregations = response.get("aggregations", {})
        weeks = self._counts(aggregations.get("weeks", {}))
        total = response.get("hits", {}).get("total", 0)
        document_count = int(total.get("value", 0) if isinstance(total, dict) else total)
        recent_titles = []
        for hit in (
            aggregations.get("recent_documents", {})
            .get("hits", {})
            .get("hits", [])
        ):
            source = hit.get("_source", {})
            title = source.get("subject") or source.get("title")
            safe_title = sanitize_text(title) if isinstance(title, str) else ""
            if safe_title and safe_title not in recent_titles:
                recent_titles.append(safe_title[:500])
        return CorpusInfo(
            document_count=document_count,
            teams=self._counts(aggregations.get("teams", {})),
            first_week=next(iter(weeks), None),
            last_week=next(reversed(weeks), None) if weeks else None,
            mail_types=self._counts(aggregations.get("mail_types", {})),
            embedding_models=list(
                self._counts(aggregations.get("embedding_models", {})).keys()
            ),
            recent_titles=recent_titles[:5],
        )
