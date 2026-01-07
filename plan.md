# Weekly Tech × Team Vision Email Intelligence Agent PRD

## 1. Overview
본 문서는 주간 팀장 보고 메일(본문 + 이미지)을 Vision 모델로 해석하여 자동 수집·분류·요약하고,
**Layer1 전수 집계표**, **Layer2 팀별 스토리 요약**, 그리고 **히스토리 기반 Q&A Chatbot**을 제공하는
통합 Email Intelligence Agent의 요구사항을 정의한다.

본 시스템의 목적은:
- 메일과 이미지 속 정보를 **누락 없이 지식화**
- DRAM/NAND + Tech + Team 기준의 **구조적 가시화**
- 최근 히스토리를 반영한 **스토리형 요약**
- 누적된 주간 메일을 활용한 **대화형 질의 응답**
이다.

---

## 2. Goals
- 팀별 weekly 메일(본문 + 이미지) 자동 수집
- Vision LLM을 이용한 이미지 내 텍스트/표/수치 해석
- DRAM/NAND + Tech × Team 기준 Layer1 전수 테이블 생성
- 최근 3주 히스토리를 반영한 Layer2 팀별 요약 생성
- 모든 원문을 embedding하여 Vector DB에 저장
- Vector DB 기반 RAG Chatbot 제공
- HTML 리포트 자동 생성

---

## 4. System Architecture

### 4.1 High-Level Flow

```
Mail Ingest → Vision Parse Images → Merge Text (combined.txt)
       │
       ├──→ Tech Classify → chunks.json → Layer1 Generator → Layer2 Summary → Render
       │                                        │
       └──→ Embed (원본) → Vector DB ←──────────┘ (히스토리 조회)
                              │
                              └──→ RAG Chatbot
```

- **chunks.json**: 업무 단위 청킹 + domain/tech 분류 → Layer1/Layer2 생성용
- **Vector DB**: 원본 텍스트 임베딩 → RAG Chatbot 검색용
- 두 저장소는 `mail_id`로 상호 참조 가능

### 4.2 Components
1. **Mail Ingestor**: 메일 본문 및 첨부 이미지 수집, HTML 뷰어 파일 생성
2. **Vision Parser**: Vision LLM으로 이미지 해석
3. **Preprocessor**: combined.txt 생성 (본문 + Vision 결과 통합)
4. **Tech Classifier**: LLM 기반 업무 청킹 + domain/tech 분류 → `chunks.json` 저장
5. **Embedder**: 원본 텍스트(combined.txt) 임베딩 → Vector DB 저장
6. **Vector DB**: 원본 텍스트 embedding 저장 및 RAG 검색
7. **Layer1 Generator**: `chunks.json` 기반 전수 집계 테이블 생성
8. **Summary Agent**: chunks.json + Vector DB 히스토리 기반 Layer2 요약
9. **RAG Chatbot**: Vector DB 기반 주간 메일 히스토리 질의 응답
10. **Renderer**: HTML/Markdown 출력

---

## 5. Layer1 Specification

### 5.1 목적
- 모든 weekly 업무를 **누락 없이** 구조화한 전수 집계 로그
- DRAM/NAND + Tech 기준 Cross-team 현황 가시화

### 5.2 레이아웃
Domain/Tech별 섹션으로 구분하고, 각 섹션 내에서 Product × 팀 × 업무 구조로 표시

#### 섹션 헤더
```
DRAM / 1a
```

#### 테이블 구조
| Product | 팀 | 업무 내용 |
|---------|-----|----------|
| 12G LPDDR5 | FA팀 | 내용1 |
|            | QA팀 | 내용2 |
| 24G LPDDR  | CS팀 | 내용3 |
|            |      | 내용4 |
|            | DT팀 | 내용5 |

#### 병합 규칙
- 같은 Product끼리 rowspan 병합
- 같은 Product 내 같은 팀끼리 rowspan 병합
- Product가 없는 경우 빈칸 표시
- Product, 팀 순으로 정렬 (빈 Product는 마지막)

### 5.3 Row 관리
- Tech row는 JSON 파일로 사전 정의
- 키워드 매칭 + LLM 문맥 판단으로 분류
- 분류 불가 문장은 각 Domain 상단의 `공통` row로 배치
- 모든 row는 항상 출력

### 5.4 Cell 요약 원칙
- 수치, 이슈, Action 유지
- 불필요한 배경 제거
- Bullet 형태
- 길이 제한 없음

### 5.5 Tech ↔ Product 매핑

| Domain | Tech | Product |
|--------|------|---------|
| DRAM | 1a | 12G LPDDR5 |
| DRAM | 1b | 16G DDR5 |
| DRAM | 1c | 12G LPDDR5X |
| DRAM | 1d | 24G DDR5 |
| NAND | 256 | 512Gb TLC |
| NAND | 312 | 1Tb QLC |
| NAND | 400 | 2Tb QLC |

#### 분류 규칙
- **product만 명시된 경우**: 매핑 테이블로 tech/domain 추론
- **tech만 명시된 경우**: product는 빈칸
- **둘 다 없는 경우**: domain=COMMON, tech=공통

---

## 6. Layer2 Specification

### 6.1 목적
Layer1 전수 로그와 최근 3주 히스토리를 반영하여
팀별 **스토리형 요약 리포트** 제공

### 6.2 출력 포맷

```
[Team] Weekly Summary

1. 주요 보고
- 최근 3주 맥락을 반영한 이번 주 핵심 스토리

2. Key Actions
1) DRAM-1b: open fail FA 및 TEM 분석
2) DRAM-1a: recipe 변경 적용 및 lot monitor
3) NAND-312: 수율 TF 운영

3. Issue / Risk
- DRAM-1b: 원인 미확정 → 수율 영향 지속
- NAND-312: target gap 해소 지연

4. 향후 계획
- DRAM-1b: FA 결과 반영 recipe 검증
- NAND-312: 추가 lot 적용 테스트
```

### 6.3 생성 로직
- 입력:
  - Layer1에서 해당 팀 column
  - Vector DB에서 최근 3주(team filter) retrieve 결과
- 요약 원칙:
  - 변화/진척/지속/종결 반영
  - Action에 Tech 명시
  - 반복/미해결 이슈는 Risk로 강조

---

## 7. Vision Model Usage

### 7.1 목적
- 이미지 속 텍스트, 표, 수치, 그래프 정보를 OCR이 아닌 Vision LLM으로 해석

### 7.2 Vision Prompt 원칙
- 이미지 안의 모든 텍스트/수치/표를 빠짐없이 추출
- 원문 형태 유지, 의미 단위 줄바꿈
- 보이지 않는 내용은 추측 금지
- 그래프/표는 "항목: 값" 형태로 풀어쓰기

### 7.3 활용
- Vision 결과는 본문 텍스트와 동일하게 처리
- Layer1/Layer2 및 Vector DB embedding 소스로 사용

---

## 8. 데이터 저장 구조

### 8.1 이중 저장 전략

**용도별 분리 저장** 구조를 사용한다.

| 저장소 | 데이터 | type | 용도 |
|--------|--------|------|------|
| `chunks.json` (파일) | LLM 업무 단위 청킹 + 분류 | - | Layer1/Layer2 생성 |
| Vector DB | 5000자 오버랩 청킹 | `original_part` | RAG 검색 |

- `chunks.json`: LLM이 domain/tech 분류한 업무 단위 → 리포트 생성용
- Vector DB: 5000자 오버랩 청킹 → 문맥 유지 + 검색 최적화
- 모든 데이터는 `mail_id`로 상호 참조 가능

### 8.2 chunks.json (Layer1/Layer2용)

#### 8.2.1 저장 내용
- LLM이 업무 단위로 분리한 청크
- 각 청크에 domain/tech 분류 적용
- 메일 메타데이터 포함

#### 8.2.2 스키마
```json
{
  "text": "recipe 변경으로 수율 +1.2% 개선",
  "domain": "DRAM",
  "tech": "1a",
  "product": "12G LPDDR5",
  "team": "FA팀",
  "week": "2025-48",
  "mail_id": "mail_001",
  "html_path": "data/2025-48/FA팀/mail_001/body.html"
}
```

#### 8.2.3 메타데이터 필드
| 필드 | 설명 | 출처 |
|------|------|------|
| text | 업무 내용 | LLM 추출 |
| domain | DRAM/NAND/COMMON | LLM 분류 |
| tech | 1a, 1b, 312, 공통 등 | LLM 분류 |
| product | 12G LPDDR5, 1Tb QLC 등 | LLM 분류 (tech에서 추론 가능) |
| team | 팀명 | meta.json |
| week | YYYY-WW | meta.json |
| mail_id | 메일 식별자 | 폴더명 |
| html_path | body.html 경로 | 원본 참조용 |

### 8.3 Vector DB (RAG용)

Vector DB에는 **5000자 오버랩 청킹**된 원본 텍스트를 저장한다.

#### 8.3.1 저장 내용
- **5000자 오버랩 청킹** (`type: original_part`)
  - combined.txt를 5000자씩 분리 (1000자 오버랩)
  - 문장 경계에서 자르기 시도 (마침표, 줄바꿈)
  - 오버랩으로 문맥 유지
  - RAG 검색 최적화

#### 8.3.2 청킹 설정
```python
CHUNK_SIZE = 5000   # 5000자
CHUNK_OVERLAP = 1000  # 1000자 오버랩 (20%)
```

#### 8.3.3 스키마 (type: original_part)
```json
{
  "id": "FA팀_2025-48_mail_001_part_0",
  "text": "금주 업무 보고드립니다.\n\n1. DRAM-1a recipe 변경...",
  "embedding": [0.123, 0.456, ...],
  "metadata": {
    "team": "FA팀",
    "week": "2025-48",
    "mail_id": "mail_001",
    "html_path": "data/2025-48/FA팀/mail_001/body.html",
    "type": "original_part",
    "part_index": 0,
    "total_parts": 3
  }
}
```

#### 8.3.4 메타데이터 필드
| 필드 | 설명 | 용도 |
|------|------|------|
| team | 팀명 | 팀별 필터링 |
| week | YYYY-WW | 히스토리 조회 |
| mail_id | 메일 식별자 | 원본 연결 |
| html_path | body.html 경로 | RAG 참조 링크 |
| type | original_part | 검색 대상 구분 |
| part_index | 파트 인덱스 | 순서 정보 |
| total_parts | 총 파트 수 | 전체 파트 개수 |

### 8.4 활용
- **Layer1**: chunks.json의 domain/tech/team으로 그룹핑하여 테이블 생성
- **Layer2**: chunks.json으로 팀별 업무 목록 + Vector DB(original_part)로 최근 3주 히스토리 조회
- **RAG Chatbot**:
  - Vector DB에서 `type: original_part` 검색
  - 오버랩 청킹으로 문맥 유지, 전체 맥락 검색 가능
  - 예: "FA팀 이번 주 보고 요약해줘", "수율 개선 관련 찾아줘"

### 8.5 원본 참조 방식
- 메일 수집 시 HTML 본문을 `body.html` 파일로 저장 (인라인 이미지는 Base64 Data URI로 변환)
- chunks.json과 Vector DB 모두 `html_path` 필드로 원본 HTML 경로 저장
- RAG 응답 시 참조 출처로 html_path 제공
- (선택) 내부 웹서버 서빙 시 URL 형식: `http://mail-server.internal/{week}/{team}/mail_{idx}/body.html`

### 8.6 한국어 처리

본 시스템은 한국어 주간 보고서를 처리하므로 **한국어 형태소 분석**이 필수적이다.

#### 8.6.1 검색 방식
| 검색 방식 | 기술 | 용도 |
|----------|------|------|
| 벡터 검색 | 임베딩 모델 | 의미 기반 유사도 검색 |
| 키워드 검색 | Nori 형태소 분석 | 정확한 용어 매칭 |
| **하이브리드** | 둘 다 | 최고 품질 (추천) |

#### 8.6.2 OpenSearch 한국어 분석기
- **Nori 플러그인** 사용 (한국어 형태소 분석기)
- `decompound_mode: mixed` 설정 (복합어 분해)
- 텍스트 필드에 `korean` analyzer 적용
- 인덱싱/검색 시 자동 형태소 분석

```json
{
  "settings": {
    "analysis": {
      "tokenizer": {
        "nori_tokenizer": {
          "type": "nori_tokenizer",
          "decompound_mode": "mixed"
        }
      },
      "analyzer": {
        "korean": {
          "type": "custom",
          "tokenizer": "nori_tokenizer",
          "filter": ["nori_readingform", "lowercase"]
        }
      }
    }
  }
}
```

#### 8.6.3 임베딩 모델
- OpenAI `text-embedding-3-small`: 다국어 지원 (한국어 포함)
- 1536 차원 벡터
- 의미 기반 유사도 검색에 사용

#### 8.6.4 하이브리드 검색 전략
```
최종 점수 = (벡터 유사도 × 0.7) + (키워드 BM25 × 0.3)
```
- 벡터 검색: "성능 향상" → "수율 개선" 찾음 (의미 유사)
- 키워드 검색: "수율개선" → "수율이 개선되었습니다" 찾음 (형태소 매칭)

---

## 9. RAG Chatbot Specification

### 9.1 목적
주간 메일 히스토리를 기반으로 한 대화형 Q&A 제공

### 9.2 아키텍처
사내 메신저 API 연동 방식으로, 별도 프론트엔드 없이 **REST API 서버**만 구현

```
┌──────────────┐    HTTP POST     ┌──────────────────┐
│  사내 메신저  │ ──────────────→ │  RAG API Server  │
│  (Webhook)   │ ←────────────── │  (FastAPI)       │
└──────────────┘    JSON Response └────────┬─────────┘
                                           │
                                    ┌──────▼──────┐
                                    │  Vector DB  │
                                    │  (ChromaDB) │
                                    └─────────────┘
```

### 9.3 구현 파일
- `rag_api.py`: FastAPI 기반 API 서버

### 9.4 API 엔드포인트

#### POST /chat
사내 메신저 webhook 수신 및 응답

**Request:**
```json
{
  "user_id": "user@company.com",
  "message": "최근 3주 DRAM-1b에서 어떤 이슈 있었어?"
}
```

**Response:**
```json
{
  "answer": "DRAM-1b 관련 최근 이슈는 다음과 같습니다...",
  "references": [
    {
      "team": "FA팀",
      "week": "2025-48",
      "mail_id": "mail_001",
      "url": "http://mail-server.internal/2025-48/FA팀/mail_001/body.html"
    }
  ]
}
```

#### GET /health
서버 상태 확인

### 9.5 예시 질문
- "최근 3주 DRAM-1b에서 어떤 이슈 있었어?"
- "FA팀이 지난달에 한 주요 분석은?"
- "수율 개선 액션 히스토리 요약해줘"

### 9.6 Workflow
```
User Query → Query 파싱 (팀/기간 추출) → ChromaDB 검색 → LLM 답변 생성 → Response
```

### 9.7 Query Parsing
- team, domain, tech, 기간(week range)을 LLM으로 구조화 추출 (선택적)
- 추출 실패 시 전체 검색

### 9.8 Answer 원칙
- 제공된 context만 근거로 답변
- 근거 없으면 "정보 없음" 명시
- 추측/창작 금지
- 답변에 사용된 출처 참조 URL 제공

---

## 10. Prompt Design Principles

- Vision: 보이는 것만 추출, 추측 금지
- Layer1: JSON row 그대로 출력, 전수 포함
- Layer2: 최근 3주 맥락 기반 스토리 요약
- Chatbot: context 외 정보 사용 금지

---

## 11. Output Formats
- Markdown: 메일/노션 공유용
- HTML: 메일 표 가독성 확보용

---

## 12. Risks

- Vision 모델 hallucination 가능성
- 이미지 수치 오인식
- Layer1 테이블 비대화로 가독성 저하
- LLM 요약 품질 편차
- Vector DB 비용/성능

---

## 13. Milestones

1. Mail + Vision ingest PoC
2. Tech JSON 정의 및 분류 검증
3. Layer1 자동 생성
4. Embedding 파이프라인 구축
5. 최근 3주 기반 Layer2 요약 PoC
6. RAG Chatbot 구축
7. End-to-End 자동 주간 리포트

---

## 14. Success Criteria

- 본문/이미지 정보 누락 없이 Layer1 반영
- 팀/Tech 기준 현황 즉시 파악 가능
- Layer2 요약만으로 주간 스토리 이해 가능
- Chatbot이 히스토리 기반 질문에 근거 있는 답변 제공
- 실사용자가 "메일보다 훨씬 보기 좋고 찾기 쉽다"고 평가

---
