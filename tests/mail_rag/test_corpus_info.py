import asyncio

from app.domain.policy import PolicyContext
from app.retrieval.corpus_info import CorpusInfoService


class RecordingSearch:
    def __init__(self):
        self.calls = []

    async def search(self, index, body):
        self.calls.append((index, body))
        return {
            "hits": {"total": {"value": 7}},
            "aggregations": {
                "teams": {"buckets": [{"key": "etch", "doc_count": 4}]},
                "weeks": {
                    "buckets": [
                        {"key": "2026-30", "doc_count": 2},
                        {"key": "2026-31", "doc_count": 5},
                    ]
                },
                "mail_types": {"buckets": [{"key": "weekly", "doc_count": 7}]},
                "embedding_models": {
                    "buckets": [{"key": "text-embedding-3-small", "doc_count": 7}]
                },
                "recent_documents": {
                    "hits": {"hits": [{"_source": {"subject": "최근 주간 보고"}}]}
                },
            },
        }


def test_corpus_info_is_aggregated_with_exact_owner_filter():
    search = RecordingSearch()
    result = asyncio.run(
        CorpusInfoService(search, "mail-child").inspect(
            PolicyContext.from_user_id("kim")
        )
    )

    assert search.calls[0][0] == "mail-child"
    assert search.calls[0][1]["query"] == {
        "bool": {"filter": [{"term": {"user_id": "kim"}}]}
    }
    assert result.document_count == 7
    assert result.teams == {"etch": 4}
    assert result.first_week == "2026-30"
    assert result.last_week == "2026-31"
    assert result.embedding_models == ["text-embedding-3-small"]
    assert result.recent_titles == ["최근 주간 보고"]
