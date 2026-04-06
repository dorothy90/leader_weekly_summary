# Deep Mining + PPT 생성 기능 구현 계획

## Context

현재 RAG 시스템(`rag_api_opensearch_v3.py`)은 사용자 질의에 대해 OpenSearch에서 **최대 50개 문서**만 검색하여 답변을 생성함. 사용자는 **모든 문서를 탐색**하는 deep mining 모드를 원하며, 결과를 **PPT로 정리**하는 기능을 추가하고자 함.

**핵심 아이디어**: OpenSearch scroll API로 전체 문서 수집 → 구조화 추출(Map) → 증거 보존 합성(Reduce) → 카드 기반 레이아웃 → python-pptx PPT 생성

### CCG 검토 결과 (Codex + Claude 합성)

| 결정 | 선택 | 근거 |
|------|------|------|
| PPT 라이브러리 | **python-pptx 유지** | 기존 인프라(`create_native_pptx()`), Python 네이티브, 편집 가능 PPTX |
| 생성 방식 | **Deterministic + LLM outline** | LLM은 PresentationOutline까지, 좌표/레이아웃은 deterministic builder |
| 슬라이드 배분 | **카드 기반 packing** | 구간별 하드코딩(1~3/4/5~10/11~20) 대신, 중요도·공간 예산 기반 동적 할당 |
| Map 단계 | **구조화 추출 병행** | 자유 텍스트 요약만이 아닌 Fact/Metric/Risk 구조화 추출 |
| 출력 구조 | **본문 + appendix** | 본문은 의사결정용 압축, appendix는 evidence table |

## Architecture (6단 분리)

```
POST /deep-mine (query + filters)
  │
  ▼
[1. Query Analysis] ── LLM structured output으로 질의 파싱 → QueryFilters
  │
  ▼
[2. Exhaustive Retrieve] ── OpenSearch scroll API로 전체 문서 수집
  │                         mail_id 기준 그룹핑, dedupe, metadata enrichment
  │
  ▼
[3. Evidence Extraction (Map)] ── 20개 문서씩 배치 → 구조화 추출 (병렬)
  │                                Fact, Metric, Risk, TrendSignal 추출
  │                                evidence_refs(mail_id) 유지
  │
  ▼
[4. Synthesis (Reduce)] ── facts를 team/topic/week 기준 집계
  │                         규칙 기반 집계 최대화, LLM은 narrative에만
  │                         + PresentationOutline 생성
  │                         Pydantic structured output
  │
  ▼
[5. Layout (Presentation Planning)] ── 카드 분해 → packing → fit check
  │                                     overflow 감지, 분할/압축/appendix 이동
  │
  ▼
[6. Render] ── slide defs → create_native_pptx()
  │
  ▼
PPTX 파일 + 텍스트 요약 반환
```

## 구현할 파일

### 1. 새로 생성: `deep_mining_schemas.py` — Pydantic 모델

deep mining 전용 request/response 스키마 + 분석 결과 구조 + 프레젠테이션 outline:

```python
# === Request / Response ===
class DeepMineRequest(BaseModel):
    user_id: str
    query: str
    team: Optional[str] = None          # 팀 필터
    week_from: Optional[str] = None     # 주차 범위 시작 (e.g. "2025-48")
    week_to: Optional[str] = None       # 주차 범위 끝
    mail_type: Optional[str] = None     # weekly_report / daily_report
    num_slides: int = 4                 # 슬라이드 수 (사용자 지정, 기본 4장, 최대 20장)

class DeepMineResponse(BaseModel):
    job_id: str
    status: str  # "processing" | "completed" | "failed"
    text_summary: Optional[str] = None
    pptx_url: Optional[str] = None
    document_count: Optional[int] = None
    progress: Optional[float] = None    # 0.0 ~ 1.0
    error: Optional[str] = None

# === Query Analysis 노드 출력 ===
class QueryFilters(BaseModel):
    teams: List[str] = []
    week_from: Optional[str] = None
    week_to: Optional[str] = None
    mail_type: Optional[str] = None
    key_topics: List[str] = []          # 질의에서 추출한 관심 주제

# === Evidence Extraction (Map) 출력 ===
class Fact(BaseModel):
    claim: str
    team: str
    week: str
    evidence_ref: str                   # mail_id
    confidence: float = 1.0

class MetricItem(BaseModel):
    name: str
    display_value: str                  # "98.5%", "+3.2pp" 등 슬라이드 표시용
    value: Optional[float] = None
    unit: Optional[str] = None
    team: str
    week: str
    evidence_ref: str

class RiskItem(BaseModel):
    description: str
    severity: str                       # "high" | "medium" | "low"
    team: str
    evidence_ref: str

class TrendSignal(BaseModel):
    description: str
    direction: str                      # "improving" | "declining" | "stable"
    teams: List[str]
    evidence_refs: List[str]

class BatchExtraction(BaseModel):
    """Map 노드 1배치 출력"""
    facts: List[Fact]
    metrics: List[MetricItem]
    risks: List[RiskItem]
    trends: List[TrendSignal]

# === Synthesis (Reduce) 출력 ===
class TeamFindings(BaseModel):
    team: str
    headline: str                       # 15자 이내, 슬라이드 타이틀용
    key_bullets: List[str]              # 최대 5개, 각 30자 이내
    details: str                        # 상세 텍스트
    metrics: List[MetricItem]
    risks: List[RiskItem]
    evidence_refs: List[str]

class TopicFindings(BaseModel):
    topic: str
    headline: str
    key_bullets: List[str]
    evidence_refs: List[str]

class DeepMiningAnalysis(BaseModel):
    """Reduce 노드 최종 출력"""
    executive_summary: str
    findings_by_team: List[TeamFindings]
    findings_by_topic: List[TopicFindings]
    trends: List[TrendSignal]
    key_metrics: List[MetricItem]
    recommendations: List[str]
    coverage: CoverageMetadata
    document_count: int
    week_range: str

class CoverageMetadata(BaseModel):
    """정보 손실 추적용"""
    total_documents: int
    documents_by_team: Dict[str, int]
    week_range: str
    batch_count: int
    excluded_count: int = 0
    excluded_reason: Optional[str] = None

# === Presentation Planning ===
class SlideOutline(BaseModel):
    """LLM이 생성하는 슬라이드 개요 — 좌표/레이아웃은 포함하지 않음"""
    title: str
    objective: str                      # 이 슬라이드가 전달할 핵심 메시지
    key_messages: List[str]
    evidence_refs: List[str]
    preferred_visual: str               # "bullet" | "table" | "2-column" | "metric-strip"

class PresentationOutline(BaseModel):
    slides: List[SlideOutline]
    appendix_needed: bool = False
    appendix_content: Optional[str] = None  # evidence table 등
```

### 2. 수정: `rag_api_opensearch_v3.py` — scroll 검색 + 엔드포인트

**OpenSearchClient에 추가할 메서드**: `scroll_search()`
- OpenSearch scroll API (`client.search(scroll='2m')` + `client.scroll()`)로 전체 문서 순회
- team, week 범위, mail_type 필터 지원
- embedding 필드 제외하여 메모리 절약 (`_source.excludes: ["embedding"]`)
- `mail_id` + `part_index` 기준 정렬하여 문서 재구성 용이하게

**추가할 상수**:
```python
DEEP_MINING_BATCH_SIZE = int(os.getenv("DEEP_MINING_BATCH_SIZE", "20"))
DEEP_MINING_SCROLL_SIZE = int(os.getenv("DEEP_MINING_SCROLL_SIZE", "200"))
DEEP_MINING_OUTPUT_DIR = Path(os.getenv("DEEP_MINING_OUTPUT_DIR", "exports/deep_mining"))
```

**3개 엔드포인트**:
```python
POST /deep-mine          # 작업 시작 → job_id 즉시 반환 (asyncio.create_task로 백그라운드)
GET  /deep-mine/{job_id} # 상태 조회 (processing/completed/failed + progress %)
GET  /deep-mine/{job_id}/download  # PPTX 파일 다운로드 (FileResponse)
```

### 3. 새로 생성: `deep_mining.py` — LangGraph 그래프 (1~4단)

`rag_api_opensearch_v3.py`에서 재사용: `OpenSearchClient`, `count_tokens()`, `get_llm()`, `TEAMS`

**StateGraph 노드 4개**:

| 노드 | 역할 | 입력 → 출력 |
|------|------|-------------|
| `analyze_query` | LLM structured output으로 질의 파싱 | query → QueryFilters |
| `exhaustive_retrieve` | scroll API로 전체 문서 수집 + mail_id 그룹핑 | QueryFilters → documents, batches |
| `extract_evidence` | 배치별 구조화 추출 (Fact/Metric/Risk/Trend) | batches → List[BatchExtraction] |
| `synthesize` | facts 집계 + narrative + PresentationOutline | extractions → DeepMiningAnalysis + PresentationOutline |

**그래프 구조** (직선형):
```python
START → analyze_query → exhaustive_retrieve → extract_evidence → synthesize → END
```

**토큰 관리 전략**:
- 문서 청크: ~1500자 ≈ 375토큰
- 배치당 20개 = ~7,500토큰 입력 (안전)
- 500개 문서 → 25번 LLM 호출 (extract) + 1번 (synthesize)
- 배치 크기 동적 조정: `count_tokens()`로 확인 후 초과 시 분할

**Evidence-Preserving 전략**:
- extract 단계: 자유 텍스트 요약 대신 `Fact`, `Metric`, `Risk`, `TrendSignal` 구조화 추출
- synthesize 단계: 새 사실을 invent하지 않고 merge만 수행
- evidence_refs를 항상 유지하여 추적 가능
- outlier 보호: severity/novelty/delta top-k 각각 선발
- coverage 메타데이터: "500건 중 412건이 팀 A/B/C, 나머지 88건은 기타" 같은 누락 보고

### 4. 새로 생성: `deep_mining_ppt.py` — 레이아웃 + 렌더 (5~6단)

`pptdaddy/utils/export_native.py`의 `create_native_pptx()` 재사용.

**THEME 상수**:
```python
THEME = {
    "bg_dark": "#1a1a2e",
    "bg_light": "#ffffff",
    "accent": "#e94560",
    "text_primary": "#ffffff",
    "text_dark": "#2d3748",
    "header_bg": "#2d3748",
    "font_title": 36,
    "font_subtitle": 20,
    "font_body": 16,
    "font_small": 12,
}
```

**카드 기반 packing 시스템**:

```python
@dataclass
class ContentCard:
    card_type: str          # "summary" | "team" | "topic" | "metric" | "trend" | "recommendation" | "appendix"
    title: str
    content: Any            # 카드 타입별 데이터
    importance: str         # "high" | "medium" | "low"
    estimated_space: float  # 슬라이드 내 차지 비율 (0.0~1.0)
    must_include: bool
    mergeable: bool
    evidence_refs: List[str] = field(default_factory=list)

def decompose_to_cards(analysis: DeepMiningAnalysis, outline: PresentationOutline) -> List[ContentCard]:
    """분석 결과 → atomic cards 분해"""

def pack_cards(cards: List[ContentCard], num_slides: int) -> List[List[ContentCard]]:
    """num_slides 예산 내에서 카드 packing
    - 필수 카드 우선 배치
    - 남는 공간에 중요도순 추가
    - 안 들어가면 appendix 이동
    """

def check_fit(cards_per_slide: List[ContentCard]) -> bool:
    """렌더 전 fit 판정
    - bullet 수 ≤ 6
    - 문자 수 체크
    - 테이블 행 수 ≤ 10
    - 초과 시 압축/분할
    """

def cards_to_slide_defs(packed: List[List[ContentCard]], theme: dict) -> List[dict]:
    """카드 → slide definition dict 변환 (create_native_pptx() 형식)"""

def build_deep_mining_pptx(analysis, outline, num_slides, output_path) -> str:
    """전체 파이프라인: decompose → pack → fit check → slide defs → create_native_pptx()"""
```

**본문 + appendix 구조**:
- 본문 슬라이드: 의사결정용 압축 (num_slides장)
- appendix 슬라이드 (선택적): evidence table, coverage 메타데이터, 팀별 상세 백업
- appendix는 PresentationOutline.appendix_needed가 true일 때만 생성

### 5. 수정: `pptdaddy/utils/export_native.py` — 안정성 보강

Codex가 발견한 리스크 수정:

| 수정 | 내용 |
|------|------|
| silent failure → logging.warning | element 추가 실패 시 warning print → `logging.warning()` + 실패 카운트 반환 |
| vertical_alignment | 'middle' 설정 시 `tf.word_wrap = True` + `MSO_ANCHOR.MIDDLE` 설정 |
| 테이블 행 수 경고 | 10행 초과 시 logging.warning 출력 |

> 참고: 전면 리팩토링은 하지 않음. Deep Mining에 필요한 최소한의 안정성만 보강.

## 주요 기술 결정 사항

| 결정 | 선택 | 이유 |
|------|------|------|
| 기존 graph 확장 vs 별도 graph | **별도 StateGraph** | deep mining은 분 단위, 기존 Q&A는 초 단위 |
| 전체 문서 조회 | **OpenSearch scroll API** | 10K+ 문서 순회 가능 |
| Map 방식 | **구조화 추출** | 자유 텍스트 요약보다 정보 보존율 높음 |
| Reduce 방식 | **merge only** | 새 사실 invent 방지 |
| PPT 생성 | **Deterministic + LLM outline** | LLM 좌표 hallucination 방지 |
| 슬라이드 배분 | **카드 기반 packing** | 팀 수 가변성에 적응적 |
| 비동기 처리 | **asyncio.create_task + job polling** | 2-10분 소요 |

## 파일 의존 관계

```
deep_mining_schemas.py (새)              ← 독립
rag_api_opensearch_v3.py (수정)          ← deep_mining_schemas.py import
deep_mining.py (새)                      ← rag_api_opensearch_v3.py에서 OpenSearchClient/LLM 재사용
                                         ← deep_mining_schemas.py import
deep_mining_ppt.py (새)                  ← deep_mining_schemas.py import
                                         ← pptdaddy/utils/export_native.py import
pptdaddy/utils/export_native.py (수정)   ← 독립 (안정성 보강만)
```

## 구현 순서

1. `deep_mining_schemas.py` — Pydantic 모델 전체 정의
2. `pptdaddy/utils/export_native.py` — 안정성 보강 (silent failure, vertical alignment)
3. `rag_api_opensearch_v3.py` — 상수 + `scroll_search()` + 엔드포인트 3개
4. `deep_mining_ppt.py` — 카드 시스템 + 레이아웃 + 렌더
5. `deep_mining.py` — LangGraph 그래프 (4노드 + 그래프 조립)

## Verification

1. **카드 packing 테스트**: 팀 3개/num_slides=4, 팀 10개/num_slides=8 등 다양한 조합
2. **fit check 테스트**: bullet 7개 초과, 테이블 15행 초과 시 분할/압축 동작
3. **scroll 테스트**: `scroll_search()` 전체 문서 수 vs `get_stats()` 비교
4. **PPT 생성 테스트**: mock DeepMiningAnalysis → PresentationOutline → slide defs → PPTX 열어서 확인
5. **evidence 추적 테스트**: evidence_refs가 appendix까지 전달되는지 확인
6. **통합 테스트**: `/deep-mine` POST → polling → `/download` PPTX 다운로드
7. **export_native 수정 확인**: 잘못된 element 입력 시 warning 로깅 동작
