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
from typing import List, Dict, Optional, Tuple, Literal, Set
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


# ========== Pydantic 모델 (Phase 2: 월간 cross-team chain) ==========
class WeeklyCrossIssue(BaseModel):
    title: str = Field(
        description="이 주차에 두 팀 이상 함께 등장한 cross-team 이슈의 짧은 제목 (예: 'Edge Particle 확산')"
    )
    teams: List[str] = Field(
        default_factory=list,
        description="이슈에 함께 언급된 팀 이름 리스트 (입력 team-week에 등장한 팀명 그대로 사용)"
    )
    summary: str = Field(
        default="",
        description="이슈 한 줄 요약 (조직 차원 영향 또는 공통 패턴)"
    )
    bullets: List[str] = Field(
        default_factory=list,
        description="팀별 관점 1줄씩 (예: ['Spica수율: D0 ↑', 'HBM수율: ECC fail 증가']). 최대 5개."
    )


class WeeklyCrossExtraction(BaseModel):
    issues: List[WeeklyCrossIssue] = Field(
        default_factory=list,
        description="이번 주차에서 식별한 cross-team 이슈 0~6건. 단일 팀 이슈는 포함하지 말 것."
    )


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

from team_dict import teams_by_group

# 주간 backfill 대상 팀 (신규 16팀 taxonomy, team_dict 기준)
TEAMS = [t for members in teams_by_group.values() for t in members]


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

    # 조직 핵심 3분류 그룹 + 주간보고 발송/미발송 현황
    direct = teams_by_group.get("우시 PTE", [])
    dram = teams_by_group.get("DRAM PTE", []) + teams_by_group.get("DRAM SRT", [])
    nand = teams_by_group.get("NAND PTE", []) + teams_by_group.get("NAND SRT", [])
    roster = direct + dram + nand
    sent = set(team_summaries.keys())
    missing = [t for t in roster if t not in sent]
    group_text = (
        f"- 직속: {', '.join(direct)}\n"
        f"- DRAM SRT + DRAM PTE: {', '.join(dram)}\n"
        f"- NAND SRT + NAND PTE: {', '.join(nand)}"
    )
    missing_text = ", ".join(missing) if missing else "없음"

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
- 직속 그룹 팀들의 이번 주 핵심 내용 한 줄
- DRAM SRT + DRAM PTE 그룹 팀들의 이번 주 핵심 내용 한 줄
- NAND SRT + NAND PTE 그룹 팀들의 이번 주 핵심 내용 한 줄
(섹션 1은 정확히 3줄. 위 순서대로 각 그룹 팀들의 내용을 한 줄로 종합하되, **그룹명/라벨(`직속`, `DRAM SRT + DRAM PTE` 등)은 출력하지 말고 내용만** 작성. 즉 `- **직속**: ...` 형태가 아니라 `- 내용...` 형태로만.)

**2. 팀별 하이라이트**
- 팀명: 한 줄 핵심 (팀당 1줄)
(전체 팀을 빠짐없이 나열하되, 주간보고 미발송 팀은 반드시 `- 팀명: 금주 주간보고 미발송` 으로 표기.)

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

[조직 핵심 3분류 그룹 구성]
{group_text}

[주간보고 미발송 팀]
{missing_text}
→ 섹션 2 팀별 하이라이트에서 위 미발송 팀은 반드시 `- 팀명: 금주 주간보고 미발송` 으로 표기하세요.

{summaries_text}

위 각 팀 요약을 바탕으로 조직 전체 관점의 종합 요약을 작성하세요.
섹션 1은 직속 / DRAM SRT + DRAM PTE / NAND SRT + NAND PTE 순서로 3줄을 작성하되, 그룹명 라벨은 빼고 내용만 작성하세요."""

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
    """team-week 요약에서 엔티티 추출. raw OpenAI tool_calls (LangChain GLM 호환 우회)."""
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

    data = _call_structured_tool(
        system=ENTITY_EXTRACTION_SYSTEM_PROMPT,
        user=user,
        schema_name="extract_team_week_entities",
        schema_description="team-week 보고서에서 핵심 엔티티 추출",
        schema_cls=TeamWeekEntities,
        timeout=60.0,
        max_tokens=2000,
    )
    if data is None:
        return []
    try:
        result = TeamWeekEntities(**data)
    except Exception as exc:
        print(
            f"   [entity extract validate error] team={team} week={week}: "
            f"{type(exc).__name__}: {exc}"
        )
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
MAX_CROSS_TEAM_COUNT = 4  # 섹션 6 cross-team 블록 협업팀 수 상한 (2~4팀만 노출)
MAX_CROSS_BLOCKS = 5      # 섹션 6 최종 `###` 블록 상한 (월 3~5개 목표)
MIN_CROSS_BLOCKS = 3      # 가급적 채울 하한 (참고용)
DEDUP_TEAM_JACCARD = 0.5  # 두 블록 병합 임계값 (팀 집합 Jaccard)

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


def _extract_section(overview_md: str, num: int = 3) -> Optional[str]:
    target = str(num)
    for m in _SECTION_RE.finditer(overview_md):
        if m.group(1) == target:
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


def _parse_cross_team_issues(overview_md: str, section_num: int = 3) -> List[Dict]:
    """주어진 섹션 번호의 `###` 블록을 파싱. 반환: 각 블록 dict (title, teams, bullets, summary, existing_status, block_start, block_end).

    weekly overview 는 section_num=3, monthly overview 는 section_num=6 사용.
    """
    section = _extract_section(overview_md, section_num)
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


def _call_structured_tool(
    system: str,
    user: str,
    schema_name: str,
    schema_description: str,
    schema_cls,
    *,
    timeout: float = 60.0,
    max_tokens: int = 2000,
) -> Optional[Dict]:
    """raw OpenAI SDK + tools 로 structured output 호출. 성공 시 dict, 실패 시 None.

    LangChain `with_structured_output` 가 OpenRouter+GLM-4.7 조합에서 `tool_calls` 를
    `message.parsed` 로 매핑하지 못해 깨지는 문제를 우회 (직접 tool_calls 파싱).
    """
    client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL, timeout=timeout)
    tool_def = {
        "type": "function",
        "function": {
            "name": schema_name,
            "description": schema_description,
            "parameters": schema_cls.model_json_schema(),
        },
    }
    try:
        resp = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            tools=[tool_def],
            tool_choice={"type": "function", "function": {"name": schema_name}},
            temperature=0,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        print(f"   [{schema_name} api error] {type(exc).__name__}: {exc}")
        return None

    choice = resp.choices[0]
    msg = choice.message
    if not msg.tool_calls:
        print(f"   [{schema_name} empty tool_calls] finish={choice.finish_reason}")
        return None
    args = msg.tool_calls[0].function.arguments
    try:
        return json.loads(args)
    except json.JSONDecodeError as exc:
        print(f"   [{schema_name} json parse error] {exc}: {args[:200]}")
        return None


def _verify_continuation(current_issue: Dict, past_issue: Dict, past_week: str) -> bool:
    """동일 사안 여부 검증 (raw OpenAI tools)."""
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
    parsed = _call_structured_tool(
        system,
        user,
        schema_name="IssueMatchVerdict",
        schema_description="두 이슈가 동일 사안의 연속인지 판단",
        schema_cls=IssueMatchVerdict,
        max_tokens=2000,  # GLM-4.7 reasoning 토큰 고려
    )
    if not parsed:
        return False
    return bool(parsed.get("is_continuation", False))


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


def _section_line_offset(overview_md: str, num: int = 3) -> int:
    """overview_md 에서 지정 섹션 내부 본문의 시작 line 번호를 반환."""
    target = str(num)
    m = None
    for match in _SECTION_RE.finditer(overview_md):
        if match.group(1) == target:
            m = match
            break
    if m is None:
        return 0
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
        line_offset = _section_line_offset(overview_md, 3)
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
    line_offset = _section_line_offset(overview_md, 3)
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


# ========== Monthly Overview ==========
try:
    from team_dict import teams_by_group as _teams_by_group
except ImportError:
    _teams_by_group = {}

CROSS_GROUPS_PROMPT_FRAGMENT = """### 이슈 제목 (관련팀A <-> 관련팀B <-> 관련팀C)
- 관련팀A: 해당 팀 관점의 상세 내용 (수치/기간 포함)
- 관련팀B: 해당 팀 관점의 상세 내용
- 관련팀C: 해당 팀 관점의 상세 내용
- 종합: 조직 차원의 해석과 필요 액션 (개별 팀 bullet에 이미 있는 문장을 반복하지 말 것)

라벨 규칙(엄수):
- 헤더 괄호 안과 bullet 라벨은 반드시 입력 team-week에 등장한 **팀명**만 사용. 그룹명(DRAM PTE / NAND PTE / DRAM SRT / NAND SRT / 우시 PTE) 절대 사용 금지.
- 한 그룹 전체에 걸친 이슈도 그룹명 대신 해당 그룹 소속 팀 중 실제 입력에 데이터가 있는 팀명으로 나열.
- **헤더 괄호 안 팀 개수는 2~4개로 제한 (5팀 이상 금지).** 광범위한 공통 사안은 가장 핵심적인 2~4팀으로 좁혀 표현하고, 나머지 팀의 동일 사안은 별도 블록으로 쪼개거나 섹션 1~5 한 줄에서만 다룬다.

(여러 이슈가 있으면 `###` 블록을 반복. 각 블록은 반드시 `### 소제목 (팀 <-> 팀)` + 관련 팀별 bullet + 마지막에 `- 종합: ...` bullet 순서.)"""


def month_to_weeks(month: str) -> List[str]:
    """ISO 목요일 규칙: 해당 월에 Thursday가 속하는 ISO 주차 목록.

    예: '2026-04' -> ['2026-14', '2026-15', '2026-16', '2026-17', '2026-18']
    """
    year_str, month_str = month.split("-")
    y, m = int(year_str), int(month_str)
    first = date(y, m, 1)
    if m == 12:
        last = date(y + 1, 1, 1) - timedelta(days=1)
    else:
        last = date(y, m + 1, 1) - timedelta(days=1)

    seen: List[Tuple[int, int]] = []
    cur = first - timedelta(days=7)
    while cur <= last + timedelta(days=7):
        thursday = cur + timedelta(days=(3 - cur.weekday()) % 7)
        if first <= thursday <= last:
            iso_year, iso_week, _ = thursday.isocalendar()
            key = (iso_year, iso_week)
            if key not in seen:
                seen.append(key)
        cur += timedelta(days=1)
    return [f"{y_:04d}-{w_:02d}" for y_, w_ in sorted(seen)]


def fetch_team_week_summaries_for_month(
    os_client: OpenSearch, month: str
) -> Dict[str, str]:
    """OpenSearch wiki_summaries 에서 해당 월의 모든 team-week 요약 조회.

    Returns: {"YYYY-WW__팀명": text}. 더미 격리를 위해 doc_id가 'dummy_'로 시작하는
    문서도 그대로 포함 (정합 데이터와 더미를 같이 본 채로 검증할 때 유용).
    """
    weeks = month_to_weeks(month)
    if not weeks:
        return {}
    body = {
        "size": 500,
        "query": {
            "bool": {
                "filter": [
                    {"term": {"summary_type": "team-week"}},
                    {"terms": {"week": weeks}},
                ]
            }
        },
        "_source": ["team", "week", "text"],
    }
    resp = os_client.search(index=WIKI_INDEX, body=body)
    out: Dict[str, str] = {}
    for hit in resp["hits"]["hits"]:
        src = hit["_source"]
        team = src.get("team") or ""
        week = src.get("week") or ""
        text = src.get("text") or ""
        if not (team and week and text.strip()):
            continue
        out[f"{week}__{team}"] = text
    print(f"   📚 team-week 로드: {len(out)}건 (대상 주차 {len(weeks)}개)")
    return out


def _format_group_mapping(teams_by_group: Dict[str, List[str]]) -> str:
    """team_dict.teams_by_group 를 LLM 프롬프트용 자연어 markdown 블록으로 변환."""
    lines: List[str] = []
    for group, members in teams_by_group.items():
        joined = ", ".join(members)
        lines.append(f"- **{group}**: {joined}")
    return "\n".join(lines)


TEAM_MONTH_COMPRESS_SYSTEM_PROMPT = """당신은 반도체 한 팀의 한 달치 주간 보고를 압축하는 전문가입니다.

작성 원칙:
1. 입력은 동일 팀의 N주(통상 4~5주) 분량의 weekly 요약입니다.
2. 한 달 흐름의 핵심을 5~10개 불릿으로 압축. 시간순 흐름·수치 변화·이슈 발전을 보존.
3. 추측 금지. 원본에 없는 수치/사건은 만들지 말 것.
4. 출력 형식:
   - 1줄당 하나의 불릿. 형식: `- <한 달 핵심 한 줄>`
   - 수치는 `XX.X%` 처럼 단위 포함, 기간은 `2026-14~17` 형식.
   - 이슈/리스크는 `[리스크]` prefix 권장.
"""


def _call_team_month_compress_llm(team: str, month: str, weeks_text: str) -> str:
    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout=120.0,
    )
    user_prompt = f"""[{team}] {month} 한 달치 주간 요약을 5~10불릿으로 압축해주세요.

--- 입력 (주차별 요약) ---
{weeks_text}
--- 끝 ---

위 내용을 시간순 흐름과 수치를 보존하며 압축하세요."""
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": TEAM_MONTH_COMPRESS_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
    except Exception as exc:
        print(f"   [stage1 LLM error] team={team} month={month} chars={len(weeks_text)}: {type(exc).__name__}: {exc}")
        return ""
    content = response.choices[0].message.content or ""
    if not content.strip():
        finish = getattr(response.choices[0], "finish_reason", "?")
        print(f"   [stage1 empty] team={team} month={month} finish={finish}")
    return content


# ========== Monthly cross-team chain (Stage 1A/1B/4) ==========
WEEKLY_CROSS_EXTRACT_SYSTEM_PROMPT = """당신은 주간 보고에서 cross-team 이슈를 식별하는 전문가입니다.

입력은 한 주차의 N개 팀별 weekly 요약입니다.
서로 다른 팀 두 개 이상의 weekly 요약에서 **공통으로 등장하는 사안 (cross-team issue)** 만 추출합니다.

규칙:
1. 한 팀에서만 언급된 단일 팀 이슈는 추출하지 말 것.
2. 두 팀 이상이 같은 키워드/현상/공정/제품에 대해 언급하면 cross-team 이슈로 간주.
3. 제목은 짧고 행동지향적으로 (예: 'Etch 잔류물 확산', 'Particle 영향 증가').
4. teams 필드에는 입력 weekly에 등장한 **팀명을 그대로** 사용 (그룹명 금지: DRAM PTE/NAND PTE/DRAM SRT/NAND SRT/우시 PTE).
5. summary 는 조직 차원 영향 1줄.
6. bullets 는 팀별 1줄씩 (관점 차이가 명확할 때).
7. 0~6 건. 너무 많이 만들지 말 것.

출력은 structured JSON (WeeklyCrossExtraction).
"""


def _format_team_summaries_for_extract(team_summaries: Dict[str, str]) -> str:
    parts: List[str] = []
    for team in sorted(team_summaries.keys()):
        body = (team_summaries[team] or "").strip()
        if not body:
            continue
        parts.append(f"=== {team} ===\n{body}")
    return "\n\n".join(parts)


def _call_weekly_cross_extract_llm(week: str, team_summaries: Dict[str, str]) -> Optional["WeeklyCrossExtraction"]:
    """Stage 1A: raw OpenAI tools 로 주차 단위 cross-team 이슈 추출."""
    if not team_summaries:
        return WeeklyCrossExtraction(issues=[])

    user = (
        f"[{week}] 주차의 팀별 weekly 요약 {len(team_summaries)}건 입니다. "
        "두 팀 이상에 공통으로 등장한 cross-team 이슈만 추출하세요.\n\n"
        f"{_format_team_summaries_for_extract(team_summaries)}\n\n"
        "출력은 WeeklyCrossExtraction JSON."
    )
    parsed = _call_structured_tool(
        WEEKLY_CROSS_EXTRACT_SYSTEM_PROMPT,
        user,
        schema_name="WeeklyCrossExtraction",
        schema_description="이번 주차의 cross-team 이슈 목록",
        schema_cls=WeeklyCrossExtraction,
        timeout=120.0,
        max_tokens=4000,
    )
    if parsed is None:
        return None
    try:
        return WeeklyCrossExtraction.model_validate(parsed)
    except Exception as exc:
        print(f"   [stage1a validation error] {week}: {type(exc).__name__}: {exc}")
        return None


def _weekly_cross_doc_id(week: str) -> str:
    return f"weekly-cross_{week}"


def extract_weekly_cross_team_issues(
    week: str,
    team_summaries: Dict[str, str],
    os_client: OpenSearch,
    embed_client: OpenAI,
    *,
    use_cache: bool = True,
) -> WeeklyCrossExtraction:
    """Stage 1A 진입점. OpenSearch 캐시 hit 시 LLM 호출 생략."""
    doc_id = _weekly_cross_doc_id(week)
    if use_cache:
        try:
            doc = os_client.get(index=WIKI_INDEX, id=doc_id)
            cached_text = doc.get("_source", {}).get("text", "")
            if cached_text.strip():
                return WeeklyCrossExtraction.model_validate_json(cached_text)
        except Exception:
            pass

    extraction = _call_weekly_cross_extract_llm(week, team_summaries)
    if extraction is None:
        return WeeklyCrossExtraction(issues=[])

    payload = extraction.model_dump_json()
    try:
        save_wiki_doc(
            os_client,
            embed_client,
            text=payload,
            title=f"{week} 주차 cross-team 이슈 추출 (Stage 1A)",
            summary_type="weekly-cross-issues",
            week=week,
            doc_id=doc_id,
        )
    except Exception as exc:
        print(f"   [stage1a cache write error] {week}: {type(exc).__name__}: {exc}")
    return extraction


def _team_summaries_for_week(team_week_summaries: Dict[str, str], week: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for key, text in team_week_summaries.items():
        if "__" not in key:
            continue
        w, team = key.split("__", 1)
        if w == week:
            out[team] = text
    return out


def _format_monthly_cross_candidates(
    weekly_issues_by_week: Dict[str, List[Dict]]
) -> str:
    """Stage 1A 결과(주차→이슈 dict 리스트)를 Stage 2 LLM 입력용 markdown 으로 직렬화.
    teams 2개 미만/MAX_CROSS_TEAM_COUNT 초과 또는 title 누락은 제외."""
    lines: List[str] = []
    for week in sorted(weekly_issues_by_week.keys()):
        issues = weekly_issues_by_week[week]
        rendered: List[str] = []
        for iss in issues:
            title = (iss.get("title") or "").strip()
            teams = [t.strip() for t in (iss.get("teams") or []) if t and t.strip()]
            if not title or len(teams) < 2 or len(teams) > MAX_CROSS_TEAM_COUNT:
                continue
            block = [f"- 제목: {title}", f"  팀: {' <-> '.join(teams)}"]
            summary = (iss.get("summary") or "").strip()
            if summary:
                block.append(f"  요약: {summary}")
            for b in (iss.get("bullets") or [])[:5]:
                b = (b or "").strip()
                if b:
                    block.append(f"  - {b}")
            rendered.append("\n".join(block))
        if rendered:
            lines.append(f"### {week}")
            lines.extend(rendered)
            lines.append("")
    return "\n".join(lines).strip()


def _format_monthly_chain_candidates(chains: List[Dict]) -> str:
    """chain 리스트를 Stage 2 LLM 용 markdown 으로 직렬화 (주차별 raw 후보 대체).

    주차별 후보를 그대로 보내면 동일 사안이 4~5주에 걸쳐 중복 노출되어 LLM 이 별개 블록으로
    출력하기 쉬움. chain 단위(1A→1B 임베딩+팀overlap+LLM verify 로 이미 dedup)로 변환해
    Stage 2 가 보는 후보 자체를 줄인다. 등장 주차 수가 많고 팀 수가 많은 chain 을 위로 정렬.
    """
    sorted_chains = sorted(
        chains,
        key=lambda ch: (len(ch.get("weeks_present", [])), len(ch.get("teams", []))),
        reverse=True,
    )
    parts: List[str] = []
    for ch in sorted_chains:
        title = (ch.get("title") or "").strip()
        teams = [t for t in ch.get("teams", []) if t]
        weeks = ch.get("weeks_present") or []
        if not title or len(teams) < 2 or len(teams) > MAX_CROSS_TEAM_COUNT:
            continue
        block = [
            f"- 제목: {title}",
            f"  팀: {' <-> '.join(teams)}",
            f"  등장 주차: {len(weeks)}주 ({', '.join(weeks)})",
        ]
        weekly = ch.get("weekly_summaries") or {}
        if weekly:
            recent_week = sorted(weekly.keys())[-1]
            recent_summary = (weekly.get(recent_week) or "").strip()
            if recent_summary:
                block.append(f"  요약(최근): {recent_summary}")
        parts.append("\n".join(block))
    return "\n\n".join(parts).strip()


def compute_monthly_issue_chains(
    month: str,
    team_week_summaries: Dict[str, str],
    os_client: OpenSearch,
    embed_client: OpenAI,
    *,
    use_cache: bool = True,
    weekly_issues_by_week: Optional[Dict[str, List[Dict]]] = None,
) -> List[Dict]:
    """Stage 1A + 1B 오케스트레이션. 월의 모든 주차 cross-team 이슈를 추출하고 체인 통합.

    weekly_issues_by_week 가 주어지면 Stage 1A 호출을 생략하고 1B 만 실행 (호출자에서
    이미 1A 결과를 보유하고 있을 때 LLM 중복 호출 방지)."""
    weeks = month_to_weeks(month)
    if not weeks:
        return []
    if weekly_issues_by_week is None:
        weekly_issues_by_week = {}
        for w in weeks:
            ts = _team_summaries_for_week(team_week_summaries, w)
            if not ts:
                print(f"   [stage1a] {w}: team-week 데이터 없음 — 건너뜀")
                weekly_issues_by_week[w] = []
                continue
            extraction = extract_weekly_cross_team_issues(w, ts, os_client, embed_client, use_cache=use_cache)
            weekly_issues_by_week[w] = [iss.model_dump() for iss in extraction.issues]
            print(f"   [stage1a] {w}: cross-team 이슈 {len(extraction.issues)}건")
    return _assemble_monthly_issue_chain(month, weekly_issues_by_week, embed_client)


def _assemble_monthly_issue_chain(
    month: str,
    weekly_issues_by_week: Dict[str, List[Dict]],
    embed_client: OpenAI,
) -> List[Dict]:
    """Stage 1B: 주차별 이슈를 시간순으로 매칭해 chain 생성."""
    weeks_in_month = month_to_weeks(month)
    if not weeks_in_month:
        return []

    chains: List[Dict] = []
    chain_embeddings: List[Optional[List[float]]] = []

    for w in weeks_in_month:
        for iss in weekly_issues_by_week.get(w, []):
            iss_view = {
                "title": iss.get("title", "").strip(),
                "teams": [t.strip() for t in iss.get("teams", []) if t and t.strip()],
                "summary": iss.get("summary", ""),
                "bullets": iss.get("bullets", []),
            }
            if not iss_view["title"] or len(iss_view["teams"]) < 2:
                continue

            try:
                iss_emb = _get_issue_embedding(embed_client, iss_view)
            except Exception:
                iss_emb = None

            best_idx = -1
            best_sim = 0.0
            for ci, ch in enumerate(chains):
                if not (set(iss_view["teams"]) & set(ch["teams"])):
                    continue
                ch_emb = chain_embeddings[ci]
                if iss_emb and ch_emb:
                    sim = _cosine(iss_emb, ch_emb)
                else:
                    a = iss_view["title"].lower()
                    b = ch["title"].lower()
                    sim = 0.7 if (a and b and (a in b or b in a)) else 0.0
                if sim > best_sim:
                    best_sim = sim
                    best_idx = ci

            matched_idx = -1
            if best_idx >= 0 and best_sim >= SIMILARITY_FLOOR:
                ch = chains[best_idx]
                ch_proxy = {
                    "title": ch["title"],
                    "teams": ch["teams"],
                    "summary": ch["weekly_summaries"].get(ch["last_week"], ""),
                    "bullets": [],
                }
                if _verify_continuation(iss_view, ch_proxy, ch["last_week"]):
                    matched_idx = best_idx

            week_summary = (iss_view["summary"] or iss_view["title"]).strip()
            if matched_idx >= 0:
                ch = chains[matched_idx]
                if w not in ch["weeks_present"]:
                    ch["weeks_present"].append(w)
                ch["weeks_present"].sort()
                ch["last_week"] = ch["weeks_present"][-1]
                ch["teams"] = list(dict.fromkeys(list(ch["teams"]) + list(iss_view["teams"])))
                # 같은 주차 내 중복 등장은 더 긴 summary 로 갱신
                prior = ch["weekly_summaries"].get(w, "")
                if not prior or len(week_summary) > len(prior):
                    ch["weekly_summaries"][w] = week_summary
            else:
                chains.append(
                    {
                        "title": iss_view["title"],
                        "teams": list(iss_view["teams"]),
                        "weeks_present": [w],
                        "last_week": w,
                        "weekly_summaries": {w: week_summary},
                    }
                )
                chain_embeddings.append(iss_emb)

    filtered = [ch for ch in chains if 2 <= len(ch["teams"]) <= MAX_CROSS_TEAM_COUNT]
    dropped = len(chains) - len(filtered)
    if dropped:
        print(f"   [stage1b filter] 협업팀 {MAX_CROSS_TEAM_COUNT}개 초과 chain {dropped}건 제외")
    return filtered


def _match_block_to_chain(block: Dict, chains: List[Dict]) -> Optional[Dict]:
    """team Jaccard + title 부분일치로 가장 적합한 chain 매칭. threshold 미달이면 None."""
    block_teams = {t for t in block.get("teams", []) if t}
    if not block_teams:
        return None
    block_title = (block.get("title") or "").lower()
    best: Optional[Dict] = None
    best_score = 0.0
    for ch in chains:
        ch_teams = {t for t in ch.get("teams", []) if t}
        if not ch_teams:
            continue
        inter = block_teams & ch_teams
        if not inter:
            continue
        union = block_teams | ch_teams
        jac = len(inter) / len(union)
        title_bonus = 0.0
        ch_title = (ch.get("title") or "").lower()
        if block_title and ch_title:
            tokens = [w for w in re.split(r"[\s,/()]+", ch_title) if len(w) > 1]
            if any(tok in block_title for tok in tokens):
                title_bonus = 0.15
        score = jac + title_bonus
        if score > best_score:
            best_score = score
            best = ch
    return best if best_score >= 0.3 else None


_WEEK_LABEL_RE = re.compile(r"^\d{4}-\d{2}$")


def _inject_weekly_timeline_bullets(
    overview_md: str,
    issue: Dict,
    section_offset: int,
    weekly_summaries: Dict[str, str],
) -> str:
    """블록에 `- {YYYY-WW}: {summary}` bullet 들을 idempotent 하게 주입.

    weekly_summaries: 주차→그 주의 cross-team 요약. 정렬된 주차 순으로 종합 bullet 직전에
    (없으면 블록 끝에) 일괄 삽입. 기존 동일 패턴 bullet 들은 먼저 모두 제거 후 재삽입.
    """
    if not weekly_summaries:
        return overview_md
    lines = overview_md.split("\n")
    block_start = section_offset + issue["block_start"]
    block_end = min(section_offset + issue["block_end"], len(lines))

    summary_idx: Optional[int] = None
    week_bullet_idxs: List[int] = []
    for i in range(block_start, block_end):
        m = _BULLET_RE.match(lines[i])
        if not m:
            continue
        content = m.group(1).strip()
        head = content.split(":", 1)[0].strip()
        if _WEEK_LABEL_RE.match(head):
            week_bullet_idxs.append(i)
            continue
        for key in ("종합", "요약", "Summary", "summary", "정리"):
            if content.startswith(key) and summary_idx is None:
                summary_idx = i
                break

    # 기존 주차 bullet 들 제거 (역순)
    for i in sorted(week_bullet_idxs, reverse=True):
        del lines[i]
        if summary_idx is not None and i < summary_idx:
            summary_idx -= 1
        block_end -= 1

    indent = ""
    if summary_idx is not None:
        indent_m = re.match(r"^(\s*)", lines[summary_idx])
        indent = indent_m.group(1) if indent_m else ""

    new_bullets = [
        f"{indent}- {w}: {weekly_summaries[w]}"
        for w in sorted(weekly_summaries.keys())
        if weekly_summaries[w]
    ]
    if not new_bullets:
        return "\n".join(lines)

    if summary_idx is not None:
        for offset, b in enumerate(new_bullets):
            lines.insert(summary_idx + offset, b)
    else:
        insert_at = block_end
        while (
            insert_at > block_start
            and insert_at - 1 < len(lines)
            and not lines[insert_at - 1].strip()
        ):
            insert_at -= 1
        for offset, b in enumerate(new_bullets):
            lines.insert(insert_at + offset, b)

    return "\n".join(lines)


def _strip_oversized_cross_blocks(
    overview_md: str,
    *,
    max_teams: int = MAX_CROSS_TEAM_COUNT,
    section_num: int = 6,
) -> str:
    """섹션 N 의 `###` 크로스팀 블록 중 협업팀 수가 max_teams 초과인 것을 제거.

    LLM 이 프롬프트의 팀 수 캡(2~max_teams)을 어기고 5팀+ 블록을 만들었을 때 최종 마크다운에서
    안전망으로 잘라낸다. 잘려나간 블록 수/제목은 콘솔에 기록.
    """
    issues = _parse_cross_team_issues(overview_md, section_num=section_num)
    if not issues:
        return overview_md

    section_offset = _section_line_offset(overview_md, section_num)
    if section_offset < 0:
        return overview_md

    lines = overview_md.split("\n")
    dropped: List[str] = []
    for issue in sorted(issues, key=lambda x: x["block_start"], reverse=True):
        if len(issue.get("teams", [])) <= max_teams:
            continue
        start = section_offset + issue["block_start"]
        end = min(section_offset + issue["block_end"], len(lines))
        while end < len(lines) and not lines[end].strip():
            end += 1
        del lines[start:end]
        dropped.append(issue.get("title", "?"))

    if dropped:
        preview = ", ".join(dropped[:3]) + (" ..." if len(dropped) > 3 else "")
        print(f"   [stage2 후처리] 협업팀 {max_teams}개 초과 cross-team 블록 {len(dropped)}건 제거: {preview}")
    return "\n".join(lines)


_TITLE_TOKEN_RE = re.compile(r"[A-Za-z0-9가-힣]{2,}")


def _title_tokens(title: str) -> set:
    return {t.lower() for t in _TITLE_TOKEN_RE.findall(title or "")}


def _block_weight(issue: Dict) -> Tuple[int, int, int]:
    """블록 중요도 가중치: (timeline bullet 수, 팀 수, 전체 bullet 수). 큰 게 더 중요."""
    bullets = issue.get("bullets") or []
    timeline_count = 0
    for b in bullets:
        head = (b or "").split(":", 1)[0].strip()
        if _WEEK_LABEL_RE.match(head):
            timeline_count += 1
    return (timeline_count, len(issue.get("teams") or []), len(bullets))


def _dedup_cross_blocks(
    overview_md: str,
    *,
    section_num: int = 6,
    max_blocks: int = MAX_CROSS_BLOCKS,
    team_jaccard: float = DEDUP_TEAM_JACCARD,
) -> str:
    """섹션 N 의 `###` 블록 중 의미가 유사한 쌍을 병합(약한 쪽 drop). 잔여 블록도 max_blocks 로 제한.

    유사 판정: 팀 집합 Jaccard ≥ team_jaccard AND 제목 토큰 1개 이상 공유.
    survivor 선정: _block_weight 가 더 큰 쪽.
    """
    issues = _parse_cross_team_issues(overview_md, section_num=section_num)
    if not issues:
        return overview_md
    section_offset = _section_line_offset(overview_md, section_num)

    # 1) 의미 유사 블록 dedup
    keep_idx = list(range(len(issues)))
    drop_set: set = set()
    for i in range(len(issues)):
        if i in drop_set:
            continue
        for j in range(i + 1, len(issues)):
            if j in drop_set:
                continue
            ti = set(issues[i].get("teams") or [])
            tj = set(issues[j].get("teams") or [])
            if not ti or not tj:
                continue
            inter = ti & tj
            union = ti | tj
            jac = len(inter) / len(union) if union else 0.0
            if jac < team_jaccard:
                continue
            tok_i = _title_tokens(issues[i].get("title", ""))
            tok_j = _title_tokens(issues[j].get("title", ""))
            if not (tok_i & tok_j):
                continue
            wi = _block_weight(issues[i])
            wj = _block_weight(issues[j])
            loser = j if wi >= wj else i
            drop_set.add(loser)
            if loser == i:
                break
    keep_idx = [k for k in keep_idx if k not in drop_set]

    # 2) 블록 수 캡: 약한 가중치 순으로 추가 drop
    if len(keep_idx) > max_blocks:
        keep_idx_sorted = sorted(keep_idx, key=lambda k: _block_weight(issues[k]), reverse=True)
        kept = set(keep_idx_sorted[:max_blocks])
        extra_drop = [k for k in keep_idx if k not in kept]
        for k in extra_drop:
            drop_set.add(k)
        keep_idx = [k for k in keep_idx if k in kept]

    if not drop_set:
        return overview_md

    lines = overview_md.split("\n")
    dropped_titles: List[str] = []
    for k in sorted(drop_set, key=lambda x: issues[x]["block_start"], reverse=True):
        issue = issues[k]
        start = section_offset + issue["block_start"]
        end = min(section_offset + issue["block_end"], len(lines))
        while end < len(lines) and not lines[end].strip():
            end += 1
        del lines[start:end]
        dropped_titles.append(issue.get("title", "?"))

    if dropped_titles:
        preview = ", ".join(dropped_titles[:3]) + (" ..." if len(dropped_titles) > 3 else "")
        print(
            f"   [dedup] 유사/초과 cross-team 블록 {len(dropped_titles)}건 제거, "
            f"잔여 {len(keep_idx)}건: {preview}"
        )
    return "\n".join(lines)


def inject_monthly_chain_timeline(
    overview_md: str,
    chains: List[Dict],
    *,
    section_num: int = 6,
) -> str:
    """월간 overview md 의 cross-team 섹션 블록에 주차별 timeline bullet 주입. idempotent."""
    if not chains:
        return overview_md
    issues = _parse_cross_team_issues(overview_md, section_num=section_num)
    if not issues:
        return overview_md
    line_offset = _section_line_offset(overview_md, section_num)

    matched_count = 0
    bullet_total = 0
    result = overview_md
    for issue in sorted(issues, key=lambda x: x["block_start"], reverse=True):
        chain = _match_block_to_chain(issue, chains)
        if chain is None:
            continue
        weekly = chain.get("weekly_summaries") or {}
        if not weekly:
            continue
        result = _inject_weekly_timeline_bullets(result, issue, line_offset, weekly)
        matched_count += 1
        bullet_total += len(weekly)
    print(
        f"   [stage4 inject] {matched_count}/{len(issues)} 블록에 timeline 주입 "
        f"(주차 bullet 총 {bullet_total}건, chain 총 {len(chains)}건)"
    )
    return result


def annotate_monthly_chain_status(
    overview_md: str,
    month: str,
    team_week_summaries: Dict[str, str],
    os_client: OpenSearch,
    embed_client: OpenAI,
    *,
    use_cache: bool = True,
    weekly_issues_by_week: Optional[Dict[str, List[Dict]]] = None,
) -> str:
    """편의 래퍼: chain 계산 + injection 한 번에. weekly_issues_by_week 가 있으면 1A 재사용."""
    chains = compute_monthly_issue_chains(
        month, team_week_summaries, os_client, embed_client,
        use_cache=use_cache, weekly_issues_by_week=weekly_issues_by_week,
    )
    if not chains:
        print(f"   [stage4 skip] {month}: chain 0건")
        return overview_md
    summary = ", ".join(
        f"{ch['title'][:14]}({len(ch['weeks_present'])}주)" for ch in chains[:6]
    )
    print(f"   [stage4] chain 산출 {len(chains)}건 — {summary}")
    return inject_monthly_chain_timeline(overview_md, chains, section_num=6)


def _build_monthly_system_prompt(month: str, weeks: List[str], teams_by_group: Dict[str, List[str]]) -> str:
    group_mapping = _format_group_mapping(teams_by_group)
    weeks_str = ", ".join(weeks)
    return f"""당신은 반도체 조직의 월간 종합 리포트를 작성하는 전문가입니다.

이번 달은 ISO {len(weeks)}주({weeks_str})에 걸쳐 있습니다. 모든 주차 데이터를 빠짐없이 종합하세요.

## 그룹-팀 매핑 (ground truth)
{group_mapping}

**위 매핑은 섹션 1~5의 라벨링 전용입니다.**
입력 team-week 에는 위 매핑에 등록되지 않은 팀들도 포함됩니다 (운영 환경 기준 매핑 16팀 + 비매핑 약 14팀 = 약 30팀).
- 매핑에 없는 팀은 섹션 1~5 어디에도 등장시키지 말 것 (섹션 2~5의 verbatim 라벨만 사용, 섹션 1의 5개 그룹 한 줄 요약에도 비매핑 팀의 데이터를 반영하지 말 것).
- 섹션 6 cross-team 분석에서는 입력에 등장한 **모든 팀**(매핑 유무 무관)을 빠짐없이 검토 대상으로 삼을 것. 매핑 16팀끼리만이 아니라 매핑 없는 14팀도 cross-team 패턴(공통 이슈/리스크/장애)에 등장한다면 그대로 헤더에 포함.

## 출력 형식 — 아래 6개 섹션 구조를 한 글자도 바꾸지 말고 그대로 복제하세요.

섹션 헤더는 반드시 `**숫자. 제목**` 형식이며, `#`/`##`/`###` 는 섹션 6의 토픽 헤더 외에는 사용 금지.
불릿은 `- ` 로만 시작합니다.
섹션 1~5는 `- **라벨**: 한 줄 핵심` 형식이며, 라벨은 반드시 아래 verbatim 라벨을 그대로 사용하세요(공백/표기 변경 금지).

**1. 그룹별 핵심 (5)**
- **DRAM PTE**: 한 달 핵심 한 줄 (DRAM PTE 그룹 소속 팀 내용을 종합)
- **NAND PTE**: ...
- **DRAM SRT**: ...
- **NAND SRT**: ...
- **우시 PTE**: ...

**2. 수율 주요내용 (6)**
- **Spica수율**: 핵심 1~2개 (최대 2). 한달 핵심 수치 1개 + 가장 큰 이슈/리스크 1개.
- **HBM수율**: 핵심 1~2개 (최대 2).
- **LC_CP수율**: 핵심 1~2개 (최대 2).
- **Olympus수율**: 핵심 1~2개 (최대 2).
- **CL_PE수율**: 핵심 1~2개 (최대 2).
- **우시수율PTE**: 핵심 1~2개 (최대 2).

**3. 품질 주요내용 (2)**
- **DRAM품질PTE**: 핵심 1~2개 (최대 2).
- **NAND품질PTE**: 핵심 1~2개 (최대 2).

**4. 증산TF수율분과 (4)**
- **DRAM수율전략**: 증산TF(증산 목표·라인 확장·신규 라인 셋업·증산 ramp) 관련 핵심 1~2개 (최대 2).
- **DRAM FA PTE**: 증산TF 관련 핵심 1~2개 (최대 2) (증산 라인 FA 지원·증산 ramp 관련 분석 등).
- **NAND수율전략**: 증산TF 관련 핵심 1~2개 (최대 2).
- **NAND FA PTE**: 증산TF 관련 핵심 1~2개 (최대 2).

**5. 개발제품수율 및 양산성 (4)**
- **DRAM SRT 개발공정**: HBM4E 관련 핵심 1~2개 (최대 2).
- **Heraion양산수율**: 핵심 1~2개 (최대 2).
- **Procyon양산수율**: 핵심 1~2개 (최대 2).
- **Robson양산수율**: 핵심 1~2개 (최대 2).

**6. 크로스팀이슈 (한달치)**
{CROSS_GROUPS_PROMPT_FRAGMENT}

작성 원칙:
1. 섹션 1의 그룹 1줄은 섹션 2~5의 그룹 소속 팀 내용을 종합해 도출.
2. 라벨 표기는 위 verbatim 그대로. 임의 변형/축약 금지.
3. 데이터가 부족한 라벨은 1줄을 비워두지 말고 `데이터 부족` 으로 명시.
4. 섹션 6 각 블록의 `- 2026-WW: ...` 형식 timeline bullet 은 후처리 단계에서 자동 주입됩니다. LLM 은 절대 만들지 말고 팀별 bullet 과 마지막 종합 bullet 만 작성하세요.
5. 섹션 6 헤더의 팀 목록과 본문 bullet 라벨은 반드시 **입력에 등장한 팀명**(예: Spica수율, NAND FA PTE, ...)만 사용. 그룹명(DRAM PTE, NAND PTE, DRAM SRT, NAND SRT, 우시 PTE) 절대 금지. 또한 입력에 없는 팀명을 새로 만들어내는 것도 금지(있는 그대로의 팀명만 인용).
6. 섹션 6 토픽 헤더(`### 제목 (팀 <-> 팀)`)의 **제목 부분에는 절대 괄호 ( ) 를 사용하지 마세요.** 괄호는 팀 목록 표기 한 곳에만 사용. 예) ❌ `### 공정 이슈(Etch) (Spica <-> HBM)` → ✅ `### Etch 공정 이슈 (Spica <-> HBM)`. 괄호가 제목에 들어가면 파서가 토픽을 누락합니다.
7. 섹션 4 의 4개 팀(DRAM수율전략·DRAM FA PTE·NAND수율전략·NAND FA PTE) 한 줄은 **증산TF 관련 활동**(증산 목표 달성률, 신규 라인 셋업·라인 확장 일정, 증산 ramp, 증산 라인 FA 지원 등 증산 일정·물량 확장에 직접 연관된 사안)만 다룬다.
   - 해당 팀의 일반 FA / 수율 / 품질 / 분석 활동(예: 일반 8D Report, FA 분석 백로그, Reliability margin 등)은 섹션 4 에서 제외하고, 섹션 1 그룹 한 줄·섹션 6 크로스팀이슈로만 노출.
   - 팀의 한 달 요약에 증산TF 관련 활동 데이터가 없으면 해당 팀 한 줄을 `데이터 부족` 으로 표기 (3번 원칙 그대로 적용).
8. 섹션 2~5 의 각 팀 한 줄에는 **가장 중요한 1~2 핵심**(최대 2개)만 담는다.
   - 우선순위: (i) 한달 핵심 수치/지표 1개, (ii) 가장 큰 이슈/리스크 1개.
   - 부수 활동 나열·복수 사안 병기 금지. 두 핵심은 `;` 또는 `,` 한 번으로만 연결.
   - 한 줄 길이는 한국어 100자 이내 권장.
9. 섹션 6 각 `###` 블록의 협업팀 수는 **2~4개**. 5팀 이상 묶지 말 것 (필요하면 핵심 팀만 추려 별개 블록으로 분리). 단일 팀 블록도 금지.
10. 섹션 6 전체 `###` 블록 개수는 **3~5개**. 5개 초과 금지. 후보가 많아도 가장 중요한 3~5개로 통합한다.
11. 의미적으로 유사한 사안(동일 결함 유형이 여러 라인에서 반복, 동일 백로그/일정 리스크가 부서별로 재진술 등)은 **반드시 하나의 `###` 블록으로 병합**.
    - 별도 블록으로 쪼개지 말 것.
    - 병합 판정: 제목 핵심 키워드 공유 + 팀 집합 overlap.
    - 표현만 다르고 본질이 같으면 1개 블록으로 통합하고 팀 라인업·timeline 으로 차이를 표현.
12. 사소한 부수 이슈·단발성 사안은 섹션 6 에서 제외하고 섹션 1~5 한 줄에서만 다룬다.
"""


def generate_monthly_overview(
    month: str,
    team_week_summaries: Dict[str, str],
    teams_by_group: Optional[Dict[str, List[str]]] = None,
    *,
    cross_candidates_md: str = "",
) -> str:
    """2-stage 월간 종합 요약 생성.

    Stage 1 (압축): 팀별로 N주치 요약을 한 번에 LLM에 보내 5~10 불릿 압축. 입력 팀 수만큼 호출(운영 환경 ≈30회).
    Stage 2 (종합): Stage 1 결과(팀 수만큼) + 그룹 매핑 가이드 → 6섹션 월간 markdown. 1번 호출.

    Fallback: 입력이 짧으면 Stage 1 생략하고 Stage 2 직접 호출.

    cross_candidates_md: Stage 1A 에서 사전 추출한 주차별 cross-team 후보 markdown
    (`_format_monthly_cross_candidates` 출력). Stage 2 LLM 이 섹션 6 작성 시 보수적
    재발견 대신 후보를 통합/확장하도록 user_prompt 에 주입.
    """
    if not team_week_summaries:
        return ""
    tbg = teams_by_group if teams_by_group is not None else _teams_by_group
    if not tbg:
        print("⚠️ team_dict.teams_by_group 비어있음 — 매핑 가이드 없이 진행")
    weeks = month_to_weeks(month)

    # Stage 1: 팀별 압축
    by_team: Dict[str, Dict[str, str]] = {}
    for key, text in team_week_summaries.items():
        if "__" not in key:
            continue
        week, team = key.split("__", 1)
        by_team.setdefault(team, {})[week] = text

    total_input_chars = sum(len(v) for v in team_week_summaries.values())
    print(f"   ✏️  월간 요약 입력: {total_input_chars:,}자, {len(by_team)}팀, {len(weeks)}주차")

    SINGLE_CALL_THRESHOLD = 60_000
    use_two_stage = total_input_chars > SINGLE_CALL_THRESHOLD

    if use_two_stage:
        compressed: Dict[str, str] = {}
        for team, weeks_map in sorted(by_team.items()):
            joined = "\n\n".join(
                f"=== {w} ===\n{weeks_map[w]}"
                for w in sorted(weeks_map.keys())
            )
            print(f"   [stage1] {team}: {len(joined):,}자 → 압축 호출")
            comp = _call_team_month_compress_llm(team, month, joined)
            if comp.strip():
                compressed[team] = comp
            time.sleep(0.5)
        if not compressed:
            print("⚠️ Stage 1 결과 전부 비어있음")
            return ""
        stage2_input = "\n\n".join(
            f"=== {team} ===\n{summary}"
            for team, summary in sorted(compressed.items())
        )
    else:
        print(f"   [single-call] 입력이 {SINGLE_CALL_THRESHOLD:,}자 이하 — Stage 1 생략")
        stage2_input = "\n\n".join(
            f"=== {team} ===\n"
            + "\n\n".join(
                f"--- {w} ---\n{by_team[team][w]}"
                for w in sorted(by_team[team].keys())
            )
            for team in sorted(by_team.keys())
        )

    # Stage 2: 종합
    system_prompt = _build_monthly_system_prompt(month, weeks, tbg)
    candidates_block = cross_candidates_md or "(후보 없음 — 팀별 요약에서 직접 도출)"
    user_prompt = f"""{month} 월간 종합 요약을 작성해주세요.

--- 섹션 6용 cross-team 후보 (Stage 1A, 우선 근거) ---
{candidates_block}
--- 끝 ---

--- 팀별 한달 요약 ---
{stage2_input}
--- 끝 ---

중요:
- 위 cross-team 후보는 이미 한 달치 chain 으로 dedup 된 근거이므로, 섹션 6에서는 보수적으로 재탐색하지 말고 이 후보들을 그대로 통합/요약하세요.
- 섹션 6 최종 `###` 블록은 **3~5개**. 후보가 많아도 가장 중요한 3~5개로 통합 (5개 초과 금지).
- 의미적으로 같은 사안(제목·표현만 다른 경우 포함)은 반드시 하나의 블록으로 병합.
- 후보에 없는 내용은 팀별 한달 요약에 명확한 근거가 있을 때만 추가.

위 입력을 바탕으로 6섹션 월간 markdown을 작성하세요."""

    print(f"   [stage2] 종합 입력 {len(stage2_input):,}자 → LLM 호출")
    client = OpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        timeout=180.0,
    )
    try:
        response = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=16000,
        )
    except Exception as exc:
        print(f"   [stage2 LLM error] month={month}: {type(exc).__name__}: {exc}")
        return ""

    choice = response.choices[0]
    content = choice.message.content or ""
    finish = getattr(choice, "finish_reason", "?")
    if not content.strip():
        print(f"   [stage2 empty] month={month} finish_reason={finish}")
    elif finish != "stop":
        print(f"   [stage2 truncated] month={month} finish_reason={finish} output_chars={len(content)}")
    else:
        print(f"   ✅ stage2 완료: 출력 {len(content):,}자")

    if content.strip():
        try:
            parsed_blocks = _parse_cross_team_issues(content, section_num=6)
            candidate_total = candidates_block.count("- 제목:") if cross_candidates_md else 0
            print(
                f"   [stage2 섹션6] LLM 작성 cross-team 블록 {len(parsed_blocks)}건 "
                f"(1A 후보 {candidate_total}건 대비)"
            )
        except Exception as exc:
            print(f"   [stage2 섹션6 파싱 실패] {type(exc).__name__}: {exc}")
        content = _strip_oversized_cross_blocks(content, max_teams=MAX_CROSS_TEAM_COUNT)
        content = _dedup_cross_blocks(content, max_blocks=MAX_CROSS_BLOCKS)
    return content


def backfill_monthly_overview(
    os_client: OpenSearch,
    embed_client: OpenAI,
    month: str,
    team_week_summaries: Dict[str, str],
    teams_by_group: Optional[Dict[str, List[str]]] = None,
    force: bool = False,
):
    """월간 종합 요약 생성 + Stage 1A/1B/4 (주차별 timeline 주입) + OpenSearch 저장 + wiki/monthly md 사이드카.

    - annotate_monthly_chain_status 가 섹션 6 cross-team 블록에 `- 2026-WW: ...` 주차별 bullet 을 후처리 주입.
    - ENV `MONTHLY_CHAIN_ANNOTATE=0` 으로 우회 가능 (롤백 토글).
    - save_wiki_doc 의 week 필드는 month 값으로 재사용 (summary_type='monthly').
    - force=False(기본) 이고 monthly_{month} 가 이미 존재하면 skip (resume).
    """
    doc_id = f"monthly_{month}"
    if not force:
        try:
            if os_client.exists(index=WIKI_INDEX, id=doc_id):
                print(f"⏭️ skip monthly (resume): {month} (--force 로 재생성)")
                return
        except Exception as e:
            print(f"  ⚠️ monthly 존재 확인 실패, 진행: {month} - {e}")
    print(f"\n📋 {month} 월간 요약 생성 중...")

    # Stage 1A 를 먼저 1회 실행 → (a) Stage 2 후보 주입, (b) chain 어노테이션 양쪽에 재사용
    weeks_in_month = month_to_weeks(month)
    weekly_issues_by_week: Dict[str, List[Dict]] = {}
    for w in weeks_in_month:
        ts = _team_summaries_for_week(team_week_summaries, w)
        if not ts:
            print(f"   [stage1a] {w}: team-week 데이터 없음 — 건너뜀")
            weekly_issues_by_week[w] = []
            continue
        extraction = extract_weekly_cross_team_issues(
            w, ts, os_client, embed_client, use_cache=True
        )
        weekly_issues_by_week[w] = [iss.model_dump() for iss in extraction.issues]
        print(f"   [stage1a] {w}: cross-team 이슈 {len(extraction.issues)}건")

    # Stage 1B chain 사전 빌드 — Stage 2 입력과 Stage 4 timeline 주입에 공통 재사용
    total_1a = sum(len(v) for v in weekly_issues_by_week.values())
    chains = compute_monthly_issue_chains(
        month, team_week_summaries, os_client, embed_client,
        use_cache=True, weekly_issues_by_week=weekly_issues_by_week,
    )
    if chains:
        candidates_md = _format_monthly_chain_candidates(chains)
        print(
            f"   [stage1b→stage2] chain {len(chains)}건 → Stage 2 입력 "
            f"({len(candidates_md):,}자, 1A 원본 {total_1a}건)"
        )
    else:
        candidates_md = _format_monthly_cross_candidates(weekly_issues_by_week)
        print(
            f"   [stage1a→stage2] chain 0건, 폴백: 주차별 후보 {total_1a}건 → Stage 2 "
            f"({len(candidates_md):,}자)"
        )

    overview = generate_monthly_overview(
        month, team_week_summaries, teams_by_group,
        cross_candidates_md=candidates_md,
    )
    if not overview:
        print(f"  ⚠️ 월간 요약 생성 실패: {month}")
        return

    if chains and os.getenv("MONTHLY_CHAIN_ANNOTATE", "1") != "0":
        try:
            overview = inject_monthly_chain_timeline(overview, chains, section_num=6)
        except Exception as exc:
            print(f"  ⚠️ timeline 주입 실패(원본 저장): {month} - {type(exc).__name__}: {exc}")

    title = f"{month} 전체 팀 월간 종합 요약"

    save_wiki_doc(
        os_client,
        embed_client,
        text=overview,
        title=title,
        summary_type="monthly",
        week=month,
        doc_id=doc_id,
    )

    out_dir = Path("wiki/monthly")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{month}_월간요약.md"
    now = datetime.now(tz=__import__("datetime").timezone.utc).isoformat()
    frontmatter = (
        "---\n"
        f"title: \"{month} 전체 팀 월간 종합 요약\"\n"
        f"summary_type: monthly\n"
        f"month: {month}\n"
        f"created_at: {now}\n"
        f"updated_at: {now}\n"
        "---\n\n"
    )
    out_path.write_text(frontmatter + overview, encoding="utf-8")
    print(f"  💾 사이드카 작성: {out_path}")


def backfill_all(
    weeks: Optional[List[str]] = None,
    teams: Optional[List[str]] = None,
    skip_overview: bool = False,
    recreate: bool = False,
    force: bool = False,
):
    """전체 백필 실행. force=False(기본) 면 OpenSearch 에 이미 있는 (team,week)/overview 는 skip (resume)."""
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

    # resume: 이미 완료된 doc_id 사전 조회 (mget 1회)
    existing_doc_ids: Set[str] = set()
    if not force:
        expected_ids = [f"team-week_{w}_{t}" for w in target_weeks for t in target_teams]
        if not skip_overview:
            expected_ids += [f"overview_{w}" for w in target_weeks]
        if expected_ids:
            try:
                resp = os_client.mget(index=WIKI_INDEX, body={"ids": expected_ids})
                existing_doc_ids = {d["_id"] for d in resp.get("docs", []) if d.get("found")}
            except Exception as e:
                print(f"   ⚠️ 기존 doc 조회 실패 (전체 재생성): {e}")
                existing_doc_ids = set()
        tw_done = sum(1 for i in existing_doc_ids if i.startswith("team-week_"))
        ov_done = sum(1 for i in existing_doc_ids if i.startswith("overview_"))
        if tw_done or ov_done:
            print(f"   ⏭️ resume: 이미 완료 team-week {tw_done}개 / overview {ov_done}개 → 건너뜀 (--force 로 재생성)")

    total = 0
    entity_catalog = fetch_entity_catalog(os_client)
    if entity_catalog:
        print(f"   기존 엔티티 카탈로그: {len(entity_catalog)}개 로드")

    for week in target_weeks:
        week_summaries = {}
        new_in_week = 0

        for team in target_teams:
            doc_id = f"team-week_{week}_{team}"
            if doc_id in existing_doc_ids:
                try:
                    existing = os_client.get(index=WIKI_INDEX, id=doc_id)
                    week_summaries[team] = existing["_source"]["text"]
                    print(f"  ⏭️ skip (resume): {team} {week}")
                    continue
                except Exception as e:
                    print(f"  ⚠️ skip 후 재조회 실패, 재생성: {team} {week} - {e}")
                    # fall through → 재생성

            try:
                summary = backfill_team_week(
                    os_client, embed_client, team, week, entity_catalog=entity_catalog
                )
                if summary:
                    week_summaries[team] = summary
                    total += 1
                    new_in_week += 1
                # API rate limit 방지
                time.sleep(1)
            except Exception as e:
                print(f"  ❌ 실패: {team} {week} - {e}")

        # 주차별 전체 요약: 신규 team-week 가 하나라도 있거나 overview 자체가 없으면 (재)생성
        if not skip_overview and week_summaries:
            overview_id = f"overview_{week}"
            if overview_id in existing_doc_ids and new_in_week == 0:
                print(f"  ⏭️ skip overview (resume): {week}")
            else:
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
    parser.add_argument(
        "--monthly",
        type=str,
        default=None,
        help="월간 종합 요약 생성 (예: --monthly 2026-04). team_dict 매핑 + 2-stage LLM 요약.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="이미 완료된 (team, week)/overview/monthly 도 강제로 재생성 (기본은 resume)",
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
    elif args.monthly:
        os_client = get_client()
        embed_client = get_embedding_client()
        if not os_client.indices.exists(index=WIKI_INDEX):
            print(f"❌ 인덱스 '{WIKI_INDEX}' 없음 — 먼저 backfill 또는 더미 시드 실행 필요")
            raise SystemExit(1)
        summaries = fetch_team_week_summaries_for_month(os_client, args.monthly)
        if not summaries:
            print(f"⚠️ team-week 데이터 없음: {args.monthly} (해당 월 ISO 주차에 색인된 문서 없음)")
            raise SystemExit(1)
        backfill_monthly_overview(
            os_client, embed_client, args.monthly, summaries, force=args.force
        )
    else:
        backfill_all(
            weeks=args.week,
            teams=args.team,
            skip_overview=args.skip_overview,
            recreate=args.recreate,
            force=args.force,
        )
