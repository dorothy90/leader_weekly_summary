"""
Wiki Builder: OpenSearch wiki_summaries 인덱스 생성 및 백필
- 기존 weekly_mail 인덱스의 raw chunk를 읽어 LLM으로 요약 생성
- 요약 타입: team-week, topic-timeline, weekly-overview
- OpenSearch wiki_summaries 인덱스에 저장
"""

import os
import json
import re
import argparse
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Literal
from datetime import datetime, date, timedelta

from opensearchpy import OpenSearch, helpers
from openai import OpenAI
from dotenv import load_dotenv
from pydantic import BaseModel, Field

try:
    from langchain_openai import ChatOpenAI
    _LANGCHAIN_AVAILABLE = True
except ImportError:
    _LANGCHAIN_AVAILABLE = False

load_dotenv()


# ========== Pydantic 모델 (Phase 1: 엔티티/토픽) ==========
class WikiEntity(BaseModel):
    name: str = Field(
        description="정규화된 엔티티명 (예: 'Procyon P6', 'Edge 수율'). 동일 사안이면 기존 카탈로그의 이름과 정확히 일치."
    )
    kind: Literal["project", "equipment", "metric", "risk", "process"] = Field(
        description="엔티티 종류"
    )
    mention_excerpt: str = Field(
        description="원본/요약에서 이 엔티티가 등장한 핵심 한 줄 인용 (수치/기간 포함이면 더 좋음)"
    )


class TeamWeekEntities(BaseModel):
    entities: List[WikiEntity] = Field(default_factory=list)


# ========== 설정 ==========
OPENSEARCH_HOST = os.getenv("OPENSEARCH_HOST", "localhost")
OPENSEARCH_PORT = int(os.getenv("OPENSEARCH_PORT", "9200"))
OPENSEARCH_USER = os.getenv("OPENSEARCH_USER", "admin")
OPENSEARCH_PASSWORD = os.getenv("OPENSEARCH_PASSWORD", "rlaeorka1!K")
OPENSEARCH_USE_SSL = os.getenv("OPENSEARCH_USE_SSL", "false").lower() == "true"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "")
EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
EMBEDDING_DIMENSION = 4096
LLM_MODEL = "z-ai/glm-4.7"
# LLM_MODEL = os.getenv("LLM_MODEL", "gpt-oss-120b")

SOURCE_INDEX = os.getenv("OPENSEARCH_INDEX", "weekly_mail")
WIKI_INDEX = "wiki_summaries"

DATA_DIR = Path("data")

TEAMS = [
    "CS팀",
    "DT팀",
    "EQUIP팀",
    "FA팀",
    "PE팀",
    "PI팀",
    "PROCESS팀",
    "QA팀",
    "TEST팀",
    "YIELD팀",
]


# ========== OpenSearch 클라이언트 ==========
def get_client() -> OpenSearch:
    return OpenSearch(
        hosts=[{"host": OPENSEARCH_HOST, "port": OPENSEARCH_PORT}],
        http_auth=(OPENSEARCH_USER, OPENSEARCH_PASSWORD),
        use_ssl=OPENSEARCH_USE_SSL,
        verify_certs=False,
        ssl_show_warn=False,
    )


def get_embedding_client() -> OpenAI:
    return OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


def get_embedding(client: OpenAI, text: str) -> List[float]:
    text = text[:8000] if len(text) > 8000 else text
    response = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return response.data[0].embedding


# ========== 인덱스 생성 ==========
def create_wiki_index(client: OpenSearch):
    """wiki_summaries 인덱스 생성 (kNN + Nori)"""
    if client.indices.exists(index=WIKI_INDEX):
        print(f"✅ 인덱스 '{WIKI_INDEX}' 이미 존재")
        return

    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "number_of_shards": 1,
                "number_of_replicas": 0,
            },
            "analysis": {
                "tokenizer": {
                    "nori_tokenizer": {
                        "type": "nori_tokenizer",
                        "decompound_mode": "mixed",
                    }
                },
                "analyzer": {
                    "korean": {
                        "type": "custom",
                        "tokenizer": "nori_tokenizer",
                        "filter": ["nori_readingform", "lowercase"],
                    }
                },
            },
        },
        "mappings": {
            "properties": {
                "embedding": {
                    "type": "knn_vector",
                    "dimension": EMBEDDING_DIMENSION,
                    "method": {
                        "name": "hnsw",
                        "space_type": "cosinesimil",
                        "engine": "faiss",
                        "parameters": {"ef_construction": 128, "m": 16},
                    },
                },
                "text": {
                    "type": "text",
                    "analyzer": "korean",
                    "search_analyzer": "korean",
                },
                "title": {
                    "type": "text",
                    "analyzer": "korean",
                    "fields": {"keyword": {"type": "keyword"}},
                },
                # 메타데이터
                "summary_type": {
                    "type": "keyword"
                },  # team-week / topic / overview / query_synthesis
                "team": {"type": "keyword"},
                "week": {"type": "keyword"},
                "topic": {"type": "keyword"},  # topic-timeline용
                "source_doc_ids": {"type": "keyword"},  # 원본 문서 ID 참조
                "created_at": {"type": "date"},
                "updated_at": {"type": "date"},
                # Phase 1 — 엔티티/토픽 레이어
                "entities": {
                    "type": "nested",
                    "properties": {
                        "name": {"type": "keyword"},
                        "kind": {"type": "keyword"},
                        "mention_excerpt": {"type": "text", "analyzer": "korean"},
                    },
                },
                "topic_keys": {"type": "keyword"},
            }
        },
    }

    client.indices.create(index=WIKI_INDEX, body=index_body)
    print(f"✅ 인덱스 '{WIKI_INDEX}' 생성 완료")


def ensure_entity_mapping(client: OpenSearch):
    """기존 wiki_summaries 인덱스에 entities/topic_keys 매핑이 없으면 추가.
    keyword/nested 추가는 매핑 진화로 안전하게 가능."""
    if not client.indices.exists(index=WIKI_INDEX):
        return
    current = client.indices.get_mapping(index=WIKI_INDEX)
    props = (
        current.get(WIKI_INDEX, {})
        .get("mappings", {})
        .get("properties", {})
    )
    to_add: Dict = {}
    if "entities" not in props:
        to_add["entities"] = {
            "type": "nested",
            "properties": {
                "name": {"type": "keyword"},
                "kind": {"type": "keyword"},
                "mention_excerpt": {"type": "text", "analyzer": "korean"},
            },
        }
    if "topic_keys" not in props:
        to_add["topic_keys"] = {"type": "keyword"}
    if not to_add:
        return
    client.indices.put_mapping(index=WIKI_INDEX, body={"properties": to_add})
    print(f"✅ 인덱스 매핑 진화: {', '.join(to_add.keys())}")


def delete_wiki_index(client: OpenSearch):
    if client.indices.exists(index=WIKI_INDEX):
        client.indices.delete(index=WIKI_INDEX)
        print(f"🗑️ 인덱스 '{WIKI_INDEX}' 삭제 완료")


# ========== 소스 데이터 조회 ==========
def get_available_weeks(client: OpenSearch) -> List[str]:
    """weekly_mail 인덱스에서 사용 가능한 주차 목록 조회"""
    body = {
        "size": 0,
        "aggs": {
            "weeks": {"terms": {"field": "week", "size": 100, "order": {"_key": "asc"}}}
        },
    }
    resp = client.search(index=SOURCE_INDEX, body=body)
    return [b["key"] for b in resp["aggregations"]["weeks"]["buckets"]]


def get_teams_for_week(client: OpenSearch, week: str) -> List[str]:
    """특정 주차에 데이터가 있는 팀 목록"""
    body = {
        "size": 0,
        "query": {"term": {"week": week}},
        "aggs": {"teams": {"terms": {"field": "team", "size": 20}}},
    }
    resp = client.search(index=SOURCE_INDEX, body=body)
    return [b["key"] for b in resp["aggregations"]["teams"]["buckets"]]


def fetch_chunks_for_team_week(
    client: OpenSearch, team: str, week: str, limit: int = 100
) -> List[Dict]:
    """특정 팀-주차의 raw chunk 조회"""
    body = {
        "size": limit,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"team": team}},
                    {"term": {"week": week}},
                ]
            }
        },
        "sort": [{"part_index": {"order": "asc"}}],
    }
    resp = client.search(index=SOURCE_INDEX, body=body)
    results = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        results.append(
            {
                "id": hit["_id"],
                "text": src.get("text", ""),
                "team": src.get("team", ""),
                "week": src.get("week", ""),
                "mail_id": src.get("mail_id", ""),
                "part_index": src.get("part_index", 0),
            }
        )
    return results


def fetch_all_chunks_for_week(
    client: OpenSearch, week: str, limit: int = 500
) -> List[Dict]:
    """특정 주차의 전체 chunk 조회 (모든 팀)"""
    body = {
        "size": limit,
        "query": {"term": {"week": week}},
        "sort": [{"team": {"order": "asc"}}, {"part_index": {"order": "asc"}}],
    }
    resp = client.search(index=SOURCE_INDEX, body=body)
    results = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        results.append(
            {
                "id": hit["_id"],
                "text": src.get("text", ""),
                "team": src.get("team", ""),
                "week": src.get("week", ""),
            }
        )
    return results


# ========== LLM 요약 생성 ==========
MAX_CHARS_PER_CALL = 150000
CHUNK_CHAR_SIZE = 30000
CHUNK_OVERLAP = 2000

TEAM_WEEK_SYSTEM_PROMPT = """당신은 반도체 주간 업무 보고서 요약 전문가입니다.

주차 형식: YYYY-WW (예: 2026-02 = 2026년 제2주차, 월이 아님)

작성 원칙:
1. 원본 메일 내용만 기반으로 요약 (추측 금지)
2. 핵심 이슈, 진행 상황, 수치 중심
3. 기술 용어는 그대로 유지
4. 간결하고 구조화된 형태

출력 형식 — 아래 예시의 구조를 한 글자도 바꾸지 말고 그대로 복제하세요.
섹션 헤더는 반드시 `**숫자. 제목**` 형식이며, `#`, `##`, `###` 같은 markdown heading을 사용해서는 안 됩니다.
불릿은 `- ` 로만 시작합니다. 단 섹션 3은 예시의 마크다운 표 형식 그대로 출력합니다.

1. 핵심 요약 (3줄 이내)
- 이번 주 가장 중요한 내용을 압축

2. 주요 업무
- 진행 중인 업무/프로젝트별로 정리
- 수치, 결과가 있으면 반드시 포함

3. 이슈 & 리스크
| 항목 | 상세내용 | 영향도 | 대응방안 |
|------|---------|--------|---------|
| Procyon P6 일정 지연 | 완료 일정 26년 2월 → 7월로 5개월 지연 확정, CS 일정 26년 12월 | 양산 이관 전체 일정 영향 | EPM PCSA 개선, Edge 수율 개선 가속화 |
(반드시 위 4개 컬럼 유지. **영향도는 '상/중/하' 같은 레이블이 아니라 어떤 대상(일정·양산·수율·품질 등)에 어떻게 영향을 주는지 구체적으로 서술**, 숫자/기간/대상 포함. 원본에 없는 값은 `—`로. 리스크가 없으면 표 없이 `- 해당 없음` 한 줄.)

4. 핵심 키워드
- 이 주차의 핵심 기술 프로젝트 키워드 5~10개 (쉼표 구분)"""


def _sliding_split(text: str, size: int, overlap: int) -> List[str]:
    step = size - overlap
    parts: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        parts.append(text[start:start + size])
        if start + size >= n:
            break
        start += step
    return parts


def _call_team_week_llm(team: str, week: str, text: str) -> str:
    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout=120.0,
    )

    user_prompt = f"""[{team}] {week} 주차 보고서를 요약해주세요.

--- 원본 내용 ---
{text}
--- 끝 ---

위 내용을 바탕으로 구조화된 요약을 작성하세요."""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": TEAM_WEEK_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=4000,
        )
    except Exception as exc:
        print(f"   [LLM error] team={team} chars={len(text)}: {type(exc).__name__}: {exc}")
        return ""

    content = response.choices[0].message.content or ""
    if not content.strip():
        finish = getattr(response.choices[0], "finish_reason", "?")
        print(f"   [empty LLM response] team={team} chars={len(text)} finish_reason={finish}")
    return content


def generate_team_week_summary(team: str, week: str, chunks: List[Dict]) -> str:
    """팀-주차 wiki 요약 생성"""
    if not chunks:
        return ""

    combined_text = "\n\n---\n\n".join([c["text"] for c in chunks])

    if len(combined_text) <= MAX_CHARS_PER_CALL:
        return _call_team_week_llm(team, week, combined_text)

    parts = _sliding_split(combined_text, CHUNK_CHAR_SIZE, CHUNK_OVERLAP)
    print(
        f"   [sliding split] team={team}, chunks={len(chunks)}, "
        f"combined_chars={len(combined_text)}, parts={len(parts)}"
    )
    summaries = [_call_team_week_llm(team, week, part) for part in parts]
    return "\n\n".join(summaries)


def generate_weekly_overview(week: str, team_summaries: Dict[str, str]) -> str:
    """주차별 전체 팀 크로스 요약 생성"""
    if not team_summaries:
        return ""

    summaries_text = ""
    for team, summary in team_summaries.items():
        summaries_text += f"\n=== {team} ===\n{summary}\n"

    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout=120.0,
    )

    system_prompt = """당신은 반도체 조직의 주간 업무 현황을 종합하는 전문가입니다.

작성 원칙:
1. 각 팀 요약을 바탕으로 조직 전체 관점에서 종합
2. 팀 간 연관 이슈가 있으면 크로스 레퍼런스, 여러 팀에 걸친 공통 공통 이슈나 연관 사항
3. 조직 차원의 핵심 이슈와 리스크 도출

출력 형식 — 아래 예시의 구조를 한 글자도 바꾸지 말고 그대로 복제하세요.
섹션 헤더는 반드시 `**숫자. 제목**` 형식이며, `#`, `##`, `###` 같은 markdown heading을 사용해서는 안 됩니다.
불릿은 `- ` 로만 시작하고, 제목과 설명 사이 구분자는 `: ` 를 사용합니다. 단 섹션 4는 예시의 마크다운 표 형식 그대로 출력합니다.
팀명/이슈명/리스크명은 반드시 `**...**` 로 볼드 처리합니다.

예시:
**1. 이번 주 조직 핵심 (3줄)**
- 가장 중요한 조직 차원 이슈

**2. 팀별 하이라이트**
- 팀명: 한 줄 핵심 (팀당 1줄)

**3. 크로스팀 이슈**
### 이슈 제목 (관련팀A <-> 관련팀B <-> 관련팀C)
- 관련팀A: 해당 팀 관점의 상세 내용 (수치/기간 포함)
- 관련팀B: 해당 팀 관점의 상세 내용
- 관련팀C: 해당 팀 관점의 상세 내용
- 종합: 조직 차원의 해석과 필요 액션 (개별 팀 bullet에 이미 있는 문장을 반복하지 말 것)

(여러 이슈가 있으면 `###` 블록을 반복. 각 블록은 반드시 `### 소제목 (팀 <-> 팀)` + 관련 팀별 bullet + 마지막에 `- 종합: ...` bullet 순서.)

**4. 주요 리스크 항목**
| 항목 | 상세내용 | 영향도 | 대응방안 |
|------|---------|--------|---------|
| Procyon P6 일정 지연 | 완료 일정 26년 2월 → 7월로 5개월 지연 확정, CS 일정 26년 12월 | 양산 이관 전체 일정 영향 | EPM PCSA 개선, Edge 수율 개선 가속화 |
(반드시 위 4개 컬럼을 그대로 유지. **영향도는 '상/중/하' 같은 레이블이 아니라 해당 리스크가 실제로 어디에 어떤 영향을 주는지를 구체적으로 서술**. 숫자/기간/대상(양산·일정·품질 등)을 포함. 모르면 `—`로.)

**5. 조직 차원 권고사항**
- 권고명: 이번 주차 데이터에 근거한 조직 차원 실행 권고 (리스크 대응과 구분되는 상위 차원)"""

    user_prompt = f"""{week} 주차 전체 팀 현황을 종합해주세요.

{summaries_text}

위 각 팀 요약을 바탕으로 조직 전체 관점의 종합 요약을 작성하세요."""

    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=8000,
        )
    except Exception as exc:
        print(
            f"   [overview LLM error] week={week} chars={len(summaries_text)}: "
            f"{type(exc).__name__}: {exc}"
        )
        return ""

    choice = response.choices[0]
    content = choice.message.content or ""
    finish = getattr(choice, "finish_reason", "?")
    if not content.strip():
        print(
            f"   [overview empty] week={week} chars={len(summaries_text)} "
            f"finish_reason={finish}"
        )
    elif finish != "stop":
        print(
            f"   [overview truncated] week={week} chars={len(summaries_text)} "
            f"finish_reason={finish} output_chars={len(content)}"
        )
    return content


# ========== 엔티티 추출 (Phase 1) ==========
ENTITY_EXTRACTION_SYSTEM_PROMPT = """당신은 반도체 주간 보고서에서 핵심 엔티티를 추출하는 전문가입니다.

추출 대상:
- project: 프로젝트 코드/제품명 (예: Procyon P6, Edge program)
- equipment: 장비/시스템 (예: EPM, PCSA, 특정 챔버 모델)
- metric: 정량 지표 (예: Edge 수율, B/B/H 두께, ECC 카운트)
- risk: 명시적 리스크 항목
- process: 공정/단계 (예: ALD, Etch, EPM PCSA 단계)

원칙:
1. 일반 명사·팀명·일반 부서명은 제외. 고유명/프로젝트성/지표성 명칭만.
2. 기존 카탈로그에 동일/유사 항목이 있으면 그 이름을 그대로 사용 (LLM-only 정규화).
3. mention_excerpt 는 한 줄, 수치·기간·대상이 있으면 포함.
4. 한 보고서당 최대 10개. 중복 금지.
5. 핵심 엔티티가 없으면 빈 배열을 반환."""


def fetch_entity_catalog(client: OpenSearch, size: int = 200) -> List[str]:
    """기존 wiki_summaries 에서 등장 빈도 높은 엔티티명 목록 (LLM 정규화 컨텍스트용)."""
    if not client.indices.exists(index=WIKI_INDEX):
        return []
    try:
        body = {
            "size": 0,
            "aggs": {"top": {"terms": {"field": "topic_keys", "size": size}}},
        }
        resp = client.search(index=WIKI_INDEX, body=body)
        return [b["key"] for b in resp["aggregations"]["top"]["buckets"]]
    except Exception as exc:
        print(f"   [entity catalog fetch error] {type(exc).__name__}: {exc}")
        return []


def extract_team_week_entities(
    team: str,
    week: str,
    summary: str,
    original_excerpt: str,
    existing_catalog: List[str],
) -> List[WikiEntity]:
    """team-week 요약에서 엔티티 추출. langchain 미설치 시 graceful degrade."""
    if not _LANGCHAIN_AVAILABLE:
        return []
    if not summary.strip():
        return []

    catalog_text = (
        "\n".join(f"- {n}" for n in existing_catalog[:120])
        if existing_catalog
        else "(없음 — 새로 부여)"
    )
    user = (
        f"[팀] {team}  [주차] {week}\n\n"
        f"--- 요약 ---\n{summary[:8000]}\n\n"
        f"--- 원본 발췌 ---\n{original_excerpt[:6000]}\n\n"
        f"--- 기존 엔티티 카탈로그 ---\n{catalog_text}\n\n"
        "위 보고서에서 핵심 엔티티만 추출. 카탈로그와 같은 사안이면 동일 name 사용."
    )

    def _invoke(method: Optional[str]) -> Optional[TeamWeekEntities]:
        try:
            llm = ChatOpenAI(
                api_key=OPENROUTER_API_KEY,
                base_url=OPENROUTER_BASE_URL,
                model=LLM_MODEL,
                temperature=0,
                timeout=60.0,
            )
            structured = (
                llm.with_structured_output(TeamWeekEntities)
                if method is None
                else llm.with_structured_output(TeamWeekEntities, method=method)
            )
            return structured.invoke(
                [
                    {"role": "system", "content": ENTITY_EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ]
            )
        except Exception as exc:
            print(
                f"   [entity extract error method={method}] team={team} week={week}: "
                f"{type(exc).__name__}: {exc}"
            )
            return None

    result = _invoke(None) or _invoke("json_mode")
    if result is None:
        return []

    seen: set = set()
    deduped: List[WikiEntity] = []
    for e in result.entities:
        key = e.name.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return deduped


# ========== 크로스팀 이슈 상태 어노테이션 ==========
# canonical regex copy: generate_outlook_report.py:302, generate_stitch_report.py:72
LOOKBACK_WEEKS = 4
SIMILARITY_FLOOR = 0.7

_SECTION_RE = re.compile(
    r"\*\*(\d+)\.\s*([^\*]+?)\*\*\s*(.*?)(?=\n\*\*\d+\.|\Z)",
    re.DOTALL,
)
_CROSS_GROUP_HEADER_RE = re.compile(
    r"^\s*#{2,4}\s*"
    r"(?:[①②③④⑤⑥⑦⑧⑨⑩]\s*)?"
    r"\**\s*"
    r"(?P<title>[^(\n#][^(\n]*?)"
    r"\s*\**\s*"
    r"(?:\(\s*(?P<teams>[^)]+?)\s*\))?\s*$"
)
_CROSS_TEAM_SPLIT_RE = re.compile(r"\s*(?:<->|↔|⇄|⟷|,)\s*")
_BULLET_RE = re.compile(r"^\s*-\s+(.*)$")
_STATUS_CONT_RE = re.compile(
    r"(?:지속|계속)\s*\(\s*(?P<first>\d{4}-\d{2})\s*부터\s*(?P<n>\d+)\s*주\s*연속\s*\)"
)

_past_issue_embedding_cache: Dict[Tuple[str, str], List[float]] = {}


class IssueMatchVerdict(BaseModel):
    is_continuation: bool = Field(
        description="두 이슈가 동일한 사안이 주차를 넘어 이어지는 경우만 true. 주제가 겹쳐도 원인/범위가 다르면 false."
    )
    reasoning: str = Field(description="한 문장 근거")


def prev_iso_weeks(week: str, n: int) -> List[str]:
    """`2026-11` → [`2026-10`, `2026-09`, ...] (최근→과거 순). 연도 경계 처리 포함."""
    year_s, wk_s = week.split("-")
    year, wk = int(year_s), int(wk_s)
    monday = date.fromisocalendar(year, wk, 1)
    out: List[str] = []
    for i in range(1, n + 1):
        d = monday - timedelta(weeks=i)
        iso = d.isocalendar()
        out.append(f"{iso.year:04d}-{iso.week:02d}")
    return out


def _extract_section_3(overview_md: str) -> Optional[str]:
    for m in _SECTION_RE.finditer(overview_md):
        if m.group(1) == "3":
            return m.group(3)
    return None


def _parse_existing_status(bullets: List[str]) -> Optional[Dict]:
    """블록 내 `- 상태: ...` bullet 을 파싱. None | {"kind":"신규"} | {"kind":"계속","first":...,"n":...}"""
    for b in bullets:
        stripped = b.strip()
        if stripped.startswith("상태"):
            body = stripped[len("상태"):].lstrip(" :：").strip()
            if body.startswith("신규"):
                return {"kind": "신규"}
            m = _STATUS_CONT_RE.search(body)
            if m:
                return {
                    "kind": "지속",
                    "first": m.group("first"),
                    "n": int(m.group("n")),
                }
    return None


def _parse_cross_team_issues(overview_md: str) -> List[Dict]:
    """section 3 의 `###` 블록을 파싱. 반환: 각 블록 dict (title, teams, bullets, summary, existing_status, block_start, block_end)"""
    section = _extract_section_3(overview_md)
    if not section:
        return []
    lines = section.split("\n")
    issues: List[Dict] = []
    current: Optional[Dict] = None
    for idx, raw in enumerate(lines):
        line = raw.rstrip()
        stripped = line.lstrip()
        if stripped.startswith("#"):
            header_match = _CROSS_GROUP_HEADER_RE.match(line)
            if header_match:
                if current is not None:
                    current["block_end"] = idx
                    current["existing_status"] = _parse_existing_status(current["bullets"])
                    issues.append(current)
                teams_raw = header_match.group("teams") or ""
                teams = [
                    t.strip().strip("*").strip()
                    for t in _CROSS_TEAM_SPLIT_RE.split(teams_raw)
                    if t.strip()
                ]
                current = {
                    "title": header_match.group("title").strip().strip("*").strip(),
                    "teams": teams,
                    "bullets": [],
                    "summary": "",
                    "block_start": idx,
                    "block_end": len(lines),
                }
            continue
        bm = _BULLET_RE.match(line)
        if bm and current is not None:
            content = bm.group(1).strip()
            current["bullets"].append(content)
            # summary 캡처
            for key in ("종합", "요약", "Summary", "summary", "정리"):
                if content.startswith(key):
                    rest = content[len(key):].lstrip(" :：—–-").strip()
                    if rest:
                        current["summary"] = rest
                    break
    if current is not None:
        current["block_end"] = len(lines)
        current["existing_status"] = _parse_existing_status(current["bullets"])
        issues.append(current)
    return issues


def _fetch_past_overview_texts(
    os_client: OpenSearch, weeks: List[str]
) -> Dict[str, str]:
    """각 주차의 overview 문서를 조회. 없는 주차는 조용히 스킵."""
    out: Dict[str, str] = {}
    for w in weeks:
        try:
            doc = os_client.get(index=WIKI_INDEX, id=f"overview_{w}")
            text = doc.get("_source", {}).get("text", "")
            if text:
                out[w] = text
        except Exception:
            continue
    return out


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _issue_embed_text(issue: Dict) -> str:
    teams = ", ".join(issue.get("teams", []))
    summary = issue.get("summary", "") or " | ".join(issue.get("bullets", [])[:3])
    return f"{issue['title']} | teams: {teams} | {summary}"


def _get_issue_embedding(
    embed_client: OpenAI, issue: Dict, week: Optional[str] = None
) -> List[float]:
    if week is not None:
        cache_key = (week, issue["title"])
        if cache_key in _past_issue_embedding_cache:
            return _past_issue_embedding_cache[cache_key]
    text = _issue_embed_text(issue)
    emb = get_embedding(embed_client, text)
    if week is not None:
        _past_issue_embedding_cache[(week, issue["title"])] = emb
    return emb


def _verify_continuation(current_issue: Dict, past_issue: Dict, past_week: str) -> bool:
    """LangChain ChatOpenAI + Pydantic structured output 으로 동일 사안 여부 검증."""
    if not _LANGCHAIN_AVAILABLE:
        return False

    system = (
        "당신은 주간 업무 보고서의 크로스팀 이슈가 이전 주차의 이슈와 "
        "동일한 사안의 연속인지 판단하는 전문가입니다. "
        "주제 키워드가 겹치더라도 원인·범위·대응이 다르면 별개 이슈입니다."
    )
    user = (
        f"[이번 주 이슈]\n"
        f"제목: {current_issue['title']}\n"
        f"관련팀: {', '.join(current_issue.get('teams', []))}\n"
        f"요약: {current_issue.get('summary', '')}\n"
        f"세부: " + " / ".join(current_issue.get("bullets", [])[:4]) + "\n\n"
        f"[{past_week} 과거 이슈]\n"
        f"제목: {past_issue['title']}\n"
        f"관련팀: {', '.join(past_issue.get('teams', []))}\n"
        f"요약: {past_issue.get('summary', '')}\n"
        f"세부: " + " / ".join(past_issue.get("bullets", [])[:4]) + "\n\n"
        "동일 사안의 연속이면 is_continuation=true, 아니면 false."
    )

    def _invoke(method: Optional[str]) -> Optional[IssueMatchVerdict]:
        kwargs = dict(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
            model=LLM_MODEL,
            temperature=0,
            timeout=60.0,
        )
        llm = ChatOpenAI(**kwargs)
        try:
            if method is None:
                structured = llm.with_structured_output(IssueMatchVerdict)
            else:
                structured = llm.with_structured_output(IssueMatchVerdict, method=method)
            return structured.invoke(
                [{"role": "system", "content": system}, {"role": "user", "content": user}]
            )
        except Exception as exc:
            print(f"   [verifier error method={method}] {type(exc).__name__}: {exc}")
            return None

    verdict = _invoke(None)
    if verdict is None:
        verdict = _invoke("json_mode")
    if verdict is None:
        return False
    return bool(verdict.is_continuation)


def _format_status_bullet(status: Dict) -> str:
    if status.get("kind") == "지속":
        return f"- 상태: 지속 ({status['first']}부터 {status['n']}주 연속)"
    return "- 상태: 신규"


def _chain_from_past(past_status: Optional[Dict], past_week: str) -> Dict:
    if past_status and past_status.get("kind") == "지속":
        return {"kind": "지속", "first": past_status["first"], "n": past_status["n"] + 1}
    return {"kind": "지속", "first": past_week, "n": 2}


def _inject_status_bullet(
    overview_md: str, issue: Dict, section3_line_offset: int, new_status: Dict
) -> str:
    """overview_md 의 해당 issue 블록에 상태 bullet 을 삽입/교체. idempotent."""
    lines = overview_md.split("\n")
    block_start = section3_line_offset + issue["block_start"]
    block_end = section3_line_offset + issue["block_end"]
    new_bullet = _format_status_bullet(new_status)

    existing_idx: Optional[int] = None
    summary_idx: Optional[int] = None
    for i in range(block_start, min(block_end, len(lines))):
        line = lines[i]
        m = _BULLET_RE.match(line)
        if not m:
            continue
        content = m.group(1).strip()
        if content.startswith("상태") and existing_idx is None:
            existing_idx = i
        # 종합 bullet 탐지
        for key in ("종합", "요약", "Summary", "summary", "정리"):
            if content.startswith(key) and summary_idx is None:
                summary_idx = i
                break

    if existing_idx is not None:
        # idempotent replace — 들여쓰기 유지
        orig = lines[existing_idx]
        indent_match = re.match(r"^(\s*)", orig)
        indent = indent_match.group(1) if indent_match else ""
        lines[existing_idx] = indent + new_bullet.lstrip()
    elif summary_idx is not None:
        indent_match = re.match(r"^(\s*)", lines[summary_idx])
        indent = indent_match.group(1) if indent_match else ""
        lines.insert(summary_idx, indent + new_bullet.lstrip())
    else:
        # 종합이 없으면 블록 끝(공백 line 이 나오는 지점 직전)에 append
        insert_at = block_end
        # 블록 끝 이전의 빈 line 들을 건너뛰고 삽입
        while insert_at > block_start and insert_at - 1 < len(lines) and not lines[insert_at - 1].strip():
            insert_at -= 1
        lines.insert(insert_at, new_bullet)

    return "\n".join(lines)


def _section3_line_offset(overview_md: str) -> int:
    """overview_md 에서 section 3 내부 본문의 시작 line 번호를 반환."""
    m = None
    for match in _SECTION_RE.finditer(overview_md):
        if match.group(1) == "3":
            m = match
            break
    if m is None:
        return 0
    # section 3 의 body 는 match.start(3) 위치부터
    body_start_char = m.start(3)
    return overview_md[:body_start_char].count("\n")


def annotate_cross_team_status(
    overview_md: str,
    week: str,
    os_client: OpenSearch,
    embed_client: OpenAI,
) -> str:
    """section 3 크로스팀 이슈 블록에 `- 상태: 신규 | 계속 (...)` bullet 을 삽입."""
    current_issues = _parse_cross_team_issues(overview_md)
    if not current_issues:
        return overview_md

    prev_weeks = prev_iso_weeks(week, LOOKBACK_WEEKS)
    past_texts = _fetch_past_overview_texts(os_client, prev_weeks)
    if not past_texts:
        # 과거 주차 없음 → 모두 신규
        result = overview_md
        line_offset = _section3_line_offset(overview_md)
        # 역순 삽입 (뒤에서부터 line idx 가 변하지 않도록)
        for issue in sorted(current_issues, key=lambda x: x["block_start"], reverse=True):
            result = _inject_status_bullet(result, issue, line_offset, {"kind": "신규"})
        print(f"   [annotate] {week}: 과거 overview 없음 → {len(current_issues)}개 이슈 모두 신규")
        return result

    past_issues_by_week: Dict[str, List[Dict]] = {}
    for w, text in past_texts.items():
        past_issues_by_week[w] = _parse_cross_team_issues(text)

    # 현재 issue 별 상태 결정
    statuses: List[Dict] = []
    immediate_prev = prev_weeks[0]  # 직전 주
    for issue in current_issues:
        try:
            cur_emb = _get_issue_embedding(embed_client, issue)
        except Exception as exc:
            print(f"   [annotate embed error] {issue['title']}: {exc}")
            statuses.append({"kind": "신규"})
            continue

        # 직전 주 후보만 체인 대상
        prev_pool = past_issues_by_week.get(immediate_prev, [])
        scored: List[Tuple[float, Dict]] = []
        for past in prev_pool:
            try:
                past_emb = _get_issue_embedding(embed_client, past, week=immediate_prev)
                sim = _cosine(cur_emb, past_emb)
                scored.append((sim, past))
            except Exception:
                continue
        scored.sort(key=lambda x: x[0], reverse=True)

        matched = None
        for sim, past in scored[:3]:
            if sim < SIMILARITY_FLOOR:
                break
            if _verify_continuation(issue, past, immediate_prev):
                matched = past
                break
        if matched is not None:
            statuses.append(_chain_from_past(matched.get("existing_status"), immediate_prev))
        else:
            statuses.append({"kind": "신규"})

    # 역순으로 주입 (앞쪽 인덱스 보존)
    result = overview_md
    line_offset = _section3_line_offset(overview_md)
    for issue, status in sorted(
        zip(current_issues, statuses),
        key=lambda x: x[0]["block_start"],
        reverse=True,
    ):
        result = _inject_status_bullet(result, issue, line_offset, status)

    summary = ", ".join(
        f"{i['title'][:20]}→{s['kind']}" for i, s in zip(current_issues, statuses)
    )
    print(f"   [annotate] {week}: {summary}")
    return result


# ========== Wiki 문서 저장 ==========
def save_wiki_doc(
    os_client: OpenSearch,
    embed_client: OpenAI,
    text: str,
    title: str,
    summary_type: str,
    team: Optional[str] = None,
    week: Optional[str] = None,
    topic: Optional[str] = None,
    source_doc_ids: Optional[List[str]] = None,
    doc_id: Optional[str] = None,
    entities: Optional[List[WikiEntity]] = None,
):
    """wiki 문서를 OpenSearch에 저장"""
    if not text.strip():
        return

    embedding = get_embedding(embed_client, text)
    now = datetime.now(tz=__import__("datetime").timezone.utc).isoformat()

    entity_dicts = (
        [{"name": e.name, "kind": e.kind, "mention_excerpt": e.mention_excerpt} for e in entities]
        if entities
        else []
    )
    topic_keys = [e["name"] for e in entity_dicts]

    doc = {
        "embedding": embedding,
        "text": text,
        "title": title,
        "summary_type": summary_type,
        "team": team,
        "week": week,
        "topic": topic,
        "source_doc_ids": source_doc_ids or [],
        "entities": entity_dicts,
        "topic_keys": topic_keys,
        "created_at": now,
        "updated_at": now,
    }

    # doc_id 지정 시 upsert (같은 팀-주차 요약은 덮어쓰기)
    if doc_id:
        # 기존 문서가 있으면 created_at 유지
        try:
            existing = os_client.get(index=WIKI_INDEX, id=doc_id)
            doc["created_at"] = existing["_source"].get("created_at", now)
        except Exception:
            pass
        os_client.index(index=WIKI_INDEX, id=doc_id, body=doc)
    else:
        os_client.index(index=WIKI_INDEX, body=doc)

    print(f"  💾 저장: {title}")


# ========== 백필 ==========
def backfill_team_week(
    os_client: OpenSearch,
    embed_client: OpenAI,
    team: str,
    week: str,
    entity_catalog: Optional[List[str]] = None,
):
    """특정 팀-주차의 wiki 요약 생성 및 저장 (엔티티 추출 포함)"""
    print(f"\n📝 [{team}] {week} 요약 생성 중...")

    chunks = fetch_chunks_for_team_week(os_client, team, week)
    if not chunks:
        print(f"  ⚠️ 데이터 없음: {team} {week}")
        return None

    summary = generate_team_week_summary(team, week, chunks)
    if not summary:
        print(f"  ⚠️ 요약 생성 실패: {team} {week}")
        return None

    doc_id = f"team-week_{week}_{team}"
    title = f"{week} {team} 주간 요약"
    source_ids = [c["id"] for c in chunks]

    catalog = entity_catalog if entity_catalog is not None else fetch_entity_catalog(os_client)
    original_excerpt = "\n".join(c["text"] for c in chunks[:6])
    entities = extract_team_week_entities(team, week, summary, original_excerpt, catalog)
    if entities:
        print(f"  🔖 엔티티 {len(entities)}개: {', '.join(e.name for e in entities[:8])}")

    save_wiki_doc(
        os_client,
        embed_client,
        text=summary,
        title=title,
        summary_type="team-week",
        team=team,
        week=week,
        source_doc_ids=source_ids,
        doc_id=doc_id,
        entities=entities,
    )

    return summary


def backfill_weekly_overview(
    os_client: OpenSearch,
    embed_client: OpenAI,
    week: str,
    team_summaries: Dict[str, str],
):
    """주차별 전체 팀 크로스 요약 생성 및 저장"""
    print(f"\n📋 {week} 전체 요약 생성 중...")

    overview = generate_weekly_overview(week, team_summaries)
    if not overview:
        print(f"  ⚠️ 전체 요약 생성 실패: {week}")
        return

    try:
        overview = annotate_cross_team_status(overview, week, os_client, embed_client)
    except Exception as exc:
        print(f"  ⚠️ 상태 어노테이션 실패(원본 저장): {week} - {type(exc).__name__}: {exc}")

    doc_id = f"overview_{week}"
    title = f"{week} 전체 팀 종합 요약"

    save_wiki_doc(
        os_client,
        embed_client,
        text=overview,
        title=title,
        summary_type="overview",
        week=week,
        doc_id=doc_id,
    )


def backfill_all(
    weeks: Optional[List[str]] = None,
    teams: Optional[List[str]] = None,
    skip_overview: bool = False,
    recreate: bool = False,
):
    """전체 백필 실행"""
    os_client = get_client()
    embed_client = get_embedding_client()

    if recreate:
        delete_wiki_index(os_client)
    create_wiki_index(os_client)
    ensure_entity_mapping(os_client)

    # 대상 주차
    available_weeks = get_available_weeks(os_client)
    target_weeks = weeks if weeks else available_weeks
    target_teams = teams if teams else TEAMS

    print(f"🚀 백필 시작: {len(target_weeks)}개 주차 x {len(target_teams)}개 팀")
    print(f"   주차: {', '.join(target_weeks)}")
    print(f"   팀: {', '.join(target_teams)}")

    total = 0
    entity_catalog = fetch_entity_catalog(os_client)
    if entity_catalog:
        print(f"   기존 엔티티 카탈로그: {len(entity_catalog)}개 로드")

    for week in target_weeks:
        week_summaries = {}

        for team in target_teams:
            try:
                summary = backfill_team_week(
                    os_client, embed_client, team, week, entity_catalog=entity_catalog
                )
                if summary:
                    week_summaries[team] = summary
                    total += 1
                # API rate limit 방지
                time.sleep(1)
            except Exception as e:
                print(f"  ❌ 실패: {team} {week} - {e}")

        # 주차별 전체 요약
        if not skip_overview and week_summaries:
            try:
                backfill_weekly_overview(os_client, embed_client, week, week_summaries)
                total += 1
                time.sleep(1)
            except Exception as e:
                print(f"  ❌ 전체 요약 실패: {week} - {e}")

    print(f"\n✅ 백필 완료: {total}개 wiki 문서 생성")


# ========== Knowledge Accumulation ==========
def verify_answer_grounding(question: str, answer: str, source_chunks: str) -> bool:
    """LLM으로 답변이 소스 문서에 근거하는지 검증"""
    if not source_chunks or not source_chunks.strip():
        return False

    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {
                    "role": "system",
                    "content": "당신은 답변의 사실 근거를 검증하는 전문가입니다. 답변이 소스 문서에 정확히 근거하는지 판단하세요.",
                },
                {
                    "role": "user",
                    "content": f"""아래 답변이 소스 문서에 근거하는지 판단하세요.

소스 문서:
{source_chunks[:15000]}

질문: {question}
답변: {answer[:5000]}

판단 기준:
- 답변의 핵심 내용이 소스 문서에서 확인 가능하면 APPROVE
- 소스 문서에 없는 내용을 만들어냈거나, 핵심 사실이 틀리면 REJECT

APPROVE 또는 REJECT 한 단어만 출력하세요.""",
                },
            ],
            temperature=0,
            max_tokens=10,
        )
        result = resp.choices[0].message.content.strip().upper()
        approved = "APPROVE" in result
        print(f"  {'✅' if approved else '❌'} [Wiki 검증] {result}")
        return approved
    except Exception as e:
        print(f"  ⚠️ [Wiki 검증] LLM 호출 실패, 저장 건너뜀: {e}")
        return False


def accumulate_query_result(
    os_client: OpenSearch,
    embed_client: OpenAI,
    question: str,
    answer: str,
    source_teams: Optional[List[str]] = None,
    source_weeks: Optional[List[str]] = None,
    source_chunks: Optional[str] = None,
):
    """쿼리 결과를 wiki에 축적 (knowledge evaporation 방지)"""
    # 저장 전 LLM 검증
    if not verify_answer_grounding(question, answer, source_chunks or ""):
        print("  🚫 [Knowledge Accumulation] 근거 검증 실패, 저장하지 않음")
        return
    text = f"질문: {question}\n\n답변:\n{answer}"
    title = f"Q&A: {question[:80]}"

    # 중복 체크: 유사 질문이 이미 있는지 확인
    query_embedding = get_embedding(embed_client, question)
    search_body = {
        "size": 1,
        "query": {
            "bool": {
                "must": [
                    {"term": {"summary_type": "query_synthesis"}},
                    {
                        "knn": {
                            "embedding": {
                                "vector": query_embedding,
                                "k": 1,
                            }
                        }
                    },
                ],
            }
        },
    }

    try:
        resp = os_client.search(index=WIKI_INDEX, body=search_body)
        if resp["hits"]["hits"]:
            top_score = resp["hits"]["hits"][0]["_score"]
            # cosine similarity > 0.92 이면 기존 문서 업데이트
            if top_score > 9.2:  # OpenSearch kNN score는 10 * cosine
                existing_id = resp["hits"]["hits"][0]["_id"]
                print(
                    f"  🔄 기존 문서 업데이트: {existing_id} (score: {top_score:.2f})"
                )
                save_wiki_doc(
                    os_client,
                    embed_client,
                    text=text,
                    title=title,
                    summary_type="query_synthesis",
                    team=source_teams[0] if source_teams else None,
                    week=source_weeks[0] if source_weeks else None,
                    doc_id=existing_id,
                )
                return
    except Exception:
        pass

    # 새 문서 저장
    save_wiki_doc(
        os_client,
        embed_client,
        text=text,
        title=title,
        summary_type="query_synthesis",
        team=source_teams[0] if source_teams else None,
        week=source_weeks[0] if source_weeks else None,
    )


# ========== Topic Timeline (Phase 1) ==========
TOPIC_TIMELINE_SYSTEM_PROMPT = """당신은 반도체 조직의 주간 보고서를 종단 분석해 한 토픽(프로젝트/장비/지표/리스크 등)의 시계열 변화를 정리하는 전문가입니다.

작성 원칙:
1. 입력으로 주어진 주차별 발췌만 사용 (추측·외부 정보 금지).
2. 시간 순서대로 변화·진행을 서술. 수치/기간/대상은 그대로 유지.
3. 팀 간 관점 차이가 있으면 명시.

출력 형식 — 정확히 아래 구조로:

**1. 토픽 개요**
- 토픽이 무엇이며 왜 추적되는지 1~2줄

**2. 주차별 진행 (오래된 → 최신)**
- 2026-XX (관련팀): 핵심 변화/이벤트 한 줄 (수치/기간 포함)
- 2026-XX (관련팀): ...

**3. 현재 상태 & 핵심 이슈**
- 가장 최근 주차 기준 상태, 미해결 이슈, 임박한 일정

**4. 관련 키워드**
- 함께 자주 등장하는 엔티티/팀 (쉼표 구분)"""

_TOPIC_SLUG_RE = re.compile(r"[^A-Za-z0-9가-힣]+")


def _slugify_topic(name: str) -> str:
    s = _TOPIC_SLUG_RE.sub("_", name).strip("_")
    return s or "topic"


def fetch_topic_frequencies(
    client: OpenSearch, min_weeks: int = 2, size: int = 500
) -> List[Tuple[str, int]]:
    """topic_keys 별 등장 *주차 수* 집계. (name, distinct_week_count) 목록 반환."""
    if not client.indices.exists(index=WIKI_INDEX):
        return []
    body = {
        "size": 0,
        "query": {"term": {"summary_type": "team-week"}},
        "aggs": {
            "topics": {
                "terms": {"field": "topic_keys", "size": size},
                "aggs": {"weeks": {"cardinality": {"field": "week"}}},
            }
        },
    }
    resp = client.search(index=WIKI_INDEX, body=body)
    out: List[Tuple[str, int]] = []
    for b in resp["aggregations"]["topics"]["buckets"]:
        wks = int(b["weeks"]["value"])
        if wks >= min_weeks:
            out.append((b["key"], wks))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def fetch_topic_mentions(client: OpenSearch, topic_key: str) -> List[Dict]:
    """특정 topic_key 가 들어있는 team-week 문서들을 week 오름차순으로 반환."""
    body = {
        "size": 200,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"summary_type": "team-week"}},
                    {"term": {"topic_keys": topic_key}},
                ]
            }
        },
        "sort": [{"week": {"order": "asc"}}, {"team": {"order": "asc"}}],
    }
    resp = client.search(index=WIKI_INDEX, body=body)
    out: List[Dict] = []
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        mention = ""
        for e in src.get("entities") or []:
            if e.get("name") == topic_key:
                mention = e.get("mention_excerpt", "")
                break
        out.append(
            {
                "id": hit["_id"],
                "team": src.get("team", ""),
                "week": src.get("week", ""),
                "summary_text": src.get("text", ""),
                "mention_excerpt": mention,
            }
        )
    return out


def generate_topic_timeline(topic_key: str, mentions: List[Dict]) -> str:
    """주차별 mention 들을 모아 LLM 으로 timeline 요약 생성."""
    if not mentions:
        return ""

    blocks: List[str] = []
    for m in mentions:
        snippet = m["mention_excerpt"] or m["summary_text"][:600]
        blocks.append(f"=== {m['week']} / {m['team']} ===\n{snippet}")
    context = "\n\n".join(blocks)
    if len(context) > MAX_CHARS_PER_CALL:
        context = context[:MAX_CHARS_PER_CALL]

    user = (
        f"[토픽] {topic_key}\n\n"
        f"--- 주차별 발췌 ---\n{context}\n--- 끝 ---\n\n"
        "위 발췌만 근거로 시계열 요약을 작성하세요."
    )

    client = OpenAI(
        api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL, timeout=120.0
    )
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": TOPIC_TIMELINE_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=3000,
        )
    except Exception as exc:
        print(
            f"   [topic LLM error] topic={topic_key} chars={len(context)}: "
            f"{type(exc).__name__}: {exc}"
        )
        return ""

    content = response.choices[0].message.content or ""
    if not content.strip():
        finish = getattr(response.choices[0], "finish_reason", "?")
        print(f"   [topic empty] topic={topic_key} finish_reason={finish}")
    return content


def backfill_topic_timeline(
    os_client: OpenSearch, embed_client: OpenAI, topic_key: str
) -> bool:
    """단일 topic timeline 생성 → grounding 검증 → 저장. 성공 여부 반환."""
    print(f"\n🧵 토픽 timeline: {topic_key}")
    mentions = fetch_topic_mentions(os_client, topic_key)
    if len(mentions) < 2:
        print(f"  ⚠️ 출현 주차 부족({len(mentions)}). 스킵")
        return False

    timeline = generate_topic_timeline(topic_key, mentions)
    if not timeline:
        return False

    source_text = "\n\n".join(
        f"[{m['week']} {m['team']}]\n{m['mention_excerpt'] or m['summary_text'][:1500]}"
        for m in mentions
    )
    if not verify_answer_grounding(
        question=f"{topic_key} 의 진행 경과", answer=timeline, source_chunks=source_text
    ):
        print(f"  🚫 grounding 검증 실패: {topic_key} (저장 스킵)")
        return False

    weeks_seen = sorted({m["week"] for m in mentions})
    teams_seen = sorted({m["team"] for m in mentions})
    title = f"토픽 타임라인: {topic_key}"
    doc_id = f"topic_{_slugify_topic(topic_key)}"

    save_wiki_doc(
        os_client,
        embed_client,
        text=timeline,
        title=title,
        summary_type="topic",
        topic=topic_key,
        week=weeks_seen[-1] if weeks_seen else None,
        source_doc_ids=[m["id"] for m in mentions],
        doc_id=doc_id,
    )
    print(f"  ✅ 저장 ({len(weeks_seen)}주, 팀 {len(teams_seen)}개)")
    return True


def build_topic_timelines(
    topic: Optional[str] = None, min_weeks: int = 2
):
    """CLI 진입점: 단일 topic 또는 빈도 threshold 이상의 모든 topic timeline 생성."""
    os_client = get_client()
    embed_client = get_embedding_client()

    if not os_client.indices.exists(index=WIKI_INDEX):
        print(f"❌ 인덱스 '{WIKI_INDEX}' 없음 — 먼저 backfill 실행 필요")
        return

    ensure_entity_mapping(os_client)

    if topic:
        targets = [(topic, 0)]
    else:
        targets = fetch_topic_frequencies(os_client, min_weeks=min_weeks)
        if not targets:
            print(f"⚠️ {min_weeks}주 이상 등장한 topic 없음")
            return
        print(f"🚀 topic timeline: {len(targets)}개 (min_weeks={min_weeks})")

    ok, fail = 0, 0
    for name, _ in targets:
        try:
            success = backfill_topic_timeline(os_client, embed_client, name)
            if success:
                ok += 1
            else:
                fail += 1
            time.sleep(0.5)
        except Exception as exc:
            fail += 1
            print(f"  ❌ {name} 실패: {type(exc).__name__}: {exc}")

    print(f"\n✅ topic timeline 완료: 성공 {ok}개, 실패/스킵 {fail}개")


def reannotate_existing_overviews(weeks: Optional[List[str]] = None):
    """기존 overview_{week} 문서에 상태 어노테이션을 소급 적용. LLM 재생성 없음."""
    os_client = get_client()
    embed_client = get_embedding_client()

    if weeks:
        target_weeks = sorted(set(weeks))
    else:
        body = {
            "size": 500,
            "query": {"term": {"summary_type": "overview"}},
            "_source": ["week"],
            "sort": [{"week": {"order": "asc"}}],
        }
        resp = os_client.search(index=WIKI_INDEX, body=body)
        target_weeks = sorted({h["_source"]["week"] for h in resp["hits"]["hits"] if h["_source"].get("week")})

    if not target_weeks:
        print("⚠️ 어노테이션 대상 overview 없음")
        return

    print(f"🔁 상태 재어노테이션: {len(target_weeks)}개 주차 (오래된 순)")
    print(f"   주차: {', '.join(target_weeks)}")

    for week in target_weeks:
        doc_id = f"overview_{week}"
        try:
            doc = os_client.get(index=WIKI_INDEX, id=doc_id)
        except Exception as exc:
            print(f"  ⚠️ {doc_id} 조회 실패: {exc}")
            continue

        src = doc.get("_source", {})
        original = src.get("text", "")
        if not original:
            print(f"  ⚠️ {doc_id} text 비어있음")
            continue

        try:
            updated = annotate_cross_team_status(original, week, os_client, embed_client)
        except Exception as exc:
            print(f"  ❌ {week} 어노테이션 실패: {type(exc).__name__}: {exc}")
            continue

        if updated == original:
            print(f"  ⏭️ {week}: 변경 없음")
            continue

        title = src.get("title") or f"{week} 전체 팀 종합 요약"
        save_wiki_doc(
            os_client,
            embed_client,
            text=updated,
            title=title,
            summary_type="overview",
            week=week,
            doc_id=doc_id,
        )
        time.sleep(0.5)

    print("✅ 재어노테이션 완료")


# ========== CLI ==========
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Wiki Builder - OpenSearch wiki 요약 생성"
    )
    parser.add_argument(
        "--week", type=str, nargs="*", help="대상 주차 (예: 2025-48 2025-49)"
    )
    parser.add_argument(
        "--team", type=str, nargs="*", help="대상 팀 (예: YIELD팀 DT팀)"
    )
    parser.add_argument(
        "--skip-overview", action="store_true", help="전체 요약 생성 건너뛰기"
    )
    parser.add_argument(
        "--recreate", action="store_true", help="인덱스 재생성 (기존 데이터 삭제)"
    )
    parser.add_argument(
        "--list-weeks", action="store_true", help="사용 가능한 주차 목록 출력"
    )
    parser.add_argument(
        "--reannotate-status",
        action="store_true",
        help="기존 overview 에 크로스팀 이슈 상태(신규/계속) 어노테이션만 재적용 (LLM 재생성 없음)",
    )
    parser.add_argument(
        "--build-topics",
        action="store_true",
        help="기존 wiki 의 엔티티 빈도를 집계해 topic timeline 문서 생성/갱신",
    )
    parser.add_argument(
        "--topic",
        type=str,
        default=None,
        help="단일 topic 만 timeline 생성 (예: 'Procyon P6')",
    )
    parser.add_argument(
        "--min-weeks",
        type=int,
        default=2,
        help="--build-topics 시 timeline 생성 임계값 (출현 주차 수, 기본 2)",
    )

    args = parser.parse_args()

    if args.list_weeks:
        client = get_client()
        weeks = get_available_weeks(client)
        print(f"사용 가능한 주차 ({len(weeks)}개):")
        for w in weeks:
            teams = get_teams_for_week(client, w)
            print(f"  {w}: {', '.join(teams)}")
    elif args.reannotate_status:
        reannotate_existing_overviews(weeks=args.week)
    elif args.build_topics or args.topic:
        build_topic_timelines(topic=args.topic, min_weeks=args.min_weeks)
    else:
        backfill_all(
            weeks=args.week,
            teams=args.team,
            skip_overview=args.skip_overview,
            recreate=args.recreate,
        )
