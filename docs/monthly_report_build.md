# Monthly Report 구축 기록

주간 리포트 시스템(`generate_outlook_report.py` + `wiki_builder.py`)을 기반으로 **월간 리포트 파이프라인**을 구축한 과정과 결과를 정리한 문서.

---

## 0. 개요

### 목표
- 주간 리포트와 동일 톤·구조로 **월간 종합 리포트** 생성
- 6 섹션 구조: 그룹 핵심 / 수율 / 품질 / 증산TF / 개발제품 / 크로스팀이슈
- 새 분류 체계(16개 팀, 5개 그룹) 도입 — 기존 weekly의 EQUIP/PE/PROCESS 등 코드와 분리
- 임원 보고용 가독성 우선의 Outlook 2016 호환 HTML 출력

### 산출물 3종
| 파일 | 역할 | 영구/일회성 |
|---|---|---|
| `generate_monthly_report.py` | md → Outlook HTML 변환기 | 영구 |
| `wiki_builder.py` (확장) | LLM 2-stage 월간 요약 생성 + `--monthly` CLI | 영구 |
| `generate_dummy_team_weeks.py` | 검증용 더미 데이터 시드/정리 | 일회성(throwaway) |

---

## 1. 요구사항 분석

### 1.1 사용자 초기 spec
6개 섹션 / 17개(이후 16개로 정정) 행 라벨:

```
1. 그룹별 핵심 1줄                       → DRAM PTE, NAND PTE, DRAM SRT, NAND SRT, 우시 PTE
2. 수율 주요내용                          → Spica수율, HBM수율, LC_CP수율, Olympus수율, CL_PE수율, 우시수율PTE
3. 품질 주요내용                          → DRAM품질PTE, NAND품질PTE
4. 증산TF수율분과                         → DRAM수율전략, DRAM FA PTE, NAND수율전략, NAND FA PTE
5. 개발제품수율 및 양산성                  → DRAM SRT 개발공정(@HBM4E만), Heraion양산수율, Procyon양산수율, Robson양산수율
6. 크로스팀이슈 (한달치)                   → weekly와 동일 ### 토픽 (팀 <-> 팀) 포맷
```

### 1.2 Q&A로 확정한 결정사항
| # | 질문 | 답 |
|---|---|---|
| Q1 | 범위 | 변환기 + LLM 생성기 + 더미 생성기 모두 |
| Q2 | 섹션 1 렌더링 | 행 테이블 (라벨 배지 + 1줄) |
| Q3 | 섹션 6 포맷 | weekly와 동일 cross-team 포맷 |
| Q4 | LLM 입력 소스 | team-week md 한 달치 (디테일/토큰 균형 best) |
| Q5 | 매핑 출처 | `team_dict.py`의 `teams_by_group` (사용자 직접 작성) |
| Q6 | LLM 비용 | 사내 LLM 무료 → 진행 |
| Q7 | OpenSearch | 사용자가 직접 시작 |

### 1.3 핵심 제약
- **CLAUDE.md 원칙**: 단어 기반 키워드 라우팅 금지 → 분류는 LLM 프롬프트의 자연어 매핑 가이드로만
- **Outlook 2016 호환**: 테이블 기반 레이아웃, 인라인 CSS, JS 불가
- **기존 weekly 파이프라인 무영향**: 변경은 additive only

---

## 2. 설계

### 2.1 아키텍처
```
[더미 시드 / 실 운영 데이터]
        ↓
   wiki_summaries (OpenSearch, summary_type=team-week)
        ↓
   month_to_weeks(month) → ISO Thursday rule (4~5주)
        ↓
   fetch_team_week_summaries_for_month → 16팀 × N주
        ↓
   generate_monthly_overview (2-stage LLM)
   ├─ Stage 1: 팀별 N주 압축 (입력 ≤ 60KB면 생략)
   └─ Stage 2: 16개 팀 요약 → 6섹션 종합
        ↓
   wiki/monthly/{YYYY-MM}_월간요약.md  (사이드카)
   + OpenSearch (summary_type=monthly, doc_id=monthly_{month})
        ↓
   generate_monthly_report.py
        ↓
   wiki/monthly/{YYYY-MM}_월간요약.html (Outlook 2016 호환)
```

### 2.2 변환기 모듈 구조
- `generate_outlook_report`에서 `parse_frontmatter`, `split_sections`, `parse_bullets`, `split_labeled`, `esc_inline`, `parse_cross_team_groups`, `render_cross_team_groups`, `section_header`, `HEAD_TEMPLATE` 및 색/폰트 상수 import 재사용
- 신규 렌더러:
  - `_render_minimal_rows`: 채움 배경 없는 미니멀 행 (NAVY bold 라벨 + 1px divider)
  - `render_label_table`: 섹션 2~5용 NAVY filled 배지 (170px)
  - `render_group_callouts`: 섹션 1용 미니멀 (120px 라벨, 15px 폰트, 임원 보고 톤)
- `MONTHLY_SECTION_STYLES` dict로 섹션 번호 → 렌더러 dispatch
- canonical vs display 라벨 분리: `SECTION5_DISPLAY = {"DRAM SRT 개발공정": "DRAM SRT 개발공정(@HBM4E만)"}`

### 2.3 생성기 (wiki_builder.py 확장)
신규 추가 함수:
- `month_to_weeks(month)` — ISO Thursday rule. `2026-04 → ['2026-14', ..., '2026-18']` (5주)
- `fetch_team_week_summaries_for_month(client, month)` — `summary_type=team-week, week IN months_weeks`
- `_format_group_mapping(teams_by_group)` — LLM 프롬프트용 자연어 매핑 블록
- `_build_monthly_system_prompt(month, weeks, teams_by_group)` — 6섹션 verbatim 라벨 + 매핑 + 주차 범위 동적 주입
- `_call_team_month_compress_llm(team, month, weeks_text)` — Stage 1 압축
- `generate_monthly_overview(month, summaries)` — 2-stage 또는 단일 호출
- `backfill_monthly_overview(...)` — OS 저장 + md 사이드카 작성
- `CROSS_GROUPS_PROMPT_FRAGMENT` 상수 — weekly §3 프롬프트 단편(향후 weekly와 공유 예정)

CLI:
- `--monthly YYYY-MM` 플래그 추가 → `args.list_weeks → reannotate → build_topics → monthly → backfill_all` 라우팅 분기

### 2.4 CCG 리뷰 결과 (Codex + 자체 검토 병합)
| # | Codex 발견 | 반영 |
|---|---|---|
| HIGH-1 | "16팀×4주=64" 더미 hardcode와 ISO 5주(2026-04)의 모순 | 더미 개수 동적화 (`month_to_weeks` 기반, 80개) |
| HIGH-2 | 평균 874자/팀-주차 측정 → 80건 시 7만자 → input context 한도 초과 가능 | 2-stage 요약을 기본 아키텍처로 격상 |
| HIGH-3 | `save_wiki_doc.week=month` 누수 위험 | 모든 weekly 조회 코드에 `summary_type` 필터 명시 감사 (정공법은 `month` 정식 필드 추가, v1.1 작업) |
| MED-1 | canonical vs display 라벨 분리 필요 | `SECTION5_CANONICAL` + `SECTION5_DISPLAY` 분리 |
| MED-2 | Outlook 2016 word-break 신뢰성 | 폭 확대보다 짧은 display 라벨 우선 검증 |
| MED-3 | §6 프롬프트 단편 drift 위험 | `CROSS_GROUPS_PROMPT_FRAGMENT` 모듈 상수화 |
| LOW-1 | 더미 OS 잔존 → RAG 학습 위험 | `dummy_team_week_` doc_id prefix 강제 + `--purge` 플래그 |

> Gemini는 API 키 인증 실패로 미참여.

---

## 3. 구현 단계

### Phase 1: 변환기 (`generate_monthly_report.py`)
- outlook 모듈 재사용 import
- 6섹션 dispatch + canonical/display 라벨 분리
- 섹션별 row 라벨 list (정렬·존재 검증용 데이터, 분류 로직 X)
- 모듈 로드 시 sanity check: 섹션 2~5 canonical 합집합 == team_dict 전체 팀 검증

### Phase 2: 생성기 (`wiki_builder.py` additive)
- 기존 함수 미수정, 새 함수만 추가
- 2-stage 아키텍처:
  - Stage 1 임계값: 입력 ≤ 60,000자면 생략 (오늘 검증에선 48,547자였으므로 단일 호출 모드 작동)
  - Stage 2: temperature 0.2, max_tokens 8000
- `summary_type="monthly"` + `doc_id="monthly_{month}"` 분리
- 사이드카 md 작성: `wiki/monthly/{month}_월간요약.md` (frontmatter 포함)

### Phase 3: 더미 생성기 (`generate_dummy_team_weeks.py` throwaway)
- 16팀 × N주 동적 결정 (`month_to_weeks` 사용)
- 도메인 시드 단어 사전 (분류 X, 본문 다양성용)
- 결정론적 본문 생성 (sha256 seed → 수율값 ±swing)
- OS 격리: `dummy_team_week_{week}_{team}` doc_id, `is_dummy: true` frontmatter
- `--purge`: month 지정 시 doc_id 명시 삭제, 미지정 시 match_all 후 클라이언트측 prefix 필터 (OS 3.4가 `_id` 필드의 prefix 쿼리 거부)

---

## 4. 검증

### 4.1 Smoke Test (8 step)
| # | 검증 항목 | 결과 |
|---|---|---|
| 1 | `generate_monthly_report` import | ✅ |
| 2 | `wiki_builder` 신규 함수 import | ✅ |
| 3 | team_dict 16팀 카운트 | ✅ |
| 4 | `month_to_weeks('2026-04')` = 5주 | ✅ |
| 5 | 더미 80개 파일 생성 (file-only) | ✅ |
| 6 | 변환기 sample md → HTML | ✅ |
| 7 | HTML 구조 (Month banner ×2, 21 행, cross-team 카드) | ✅ |
| 8 | 더미 정리 (파일 0개 잔존) | ✅ |

### 4.2 End-to-End (실 OpenSearch + LLM)
1. 더미 시드: 16팀 × 5주 = 80건 → 파일 + OS 인덱싱 80/80 ✅
2. `wiki_builder.py --monthly 2026-04`: 80건 로드 → Stage 2 단일 호출 (48,547자 입력 < 60K 임계, Stage 1 생략) → 3,937자 출력 ✅
3. 생성된 md 검증: 섹션 1~5 라벨 일치 5/6/2/4/4 = 21행, 0 placeholder ✅
4. 변환기 HTML 렌더: 21 데이터 행, 4 cross-team 카드, `(@HBM4E만)` display 라벨 적용 ✅
5. 더미 정리: OS 80/80 삭제 (실 데이터 6건 보존) + 파일 80개 삭제 ✅

### 4.3 검증 중 발견·수정한 결함
1. **Cross-team 토픽 제목 괄호로 인한 사일런트 드롭**
   - LLM이 `### 공정 이슈(Etch/Particle)에 따른... (Spica <-> ...)` 헤더 출력
   - `CROSS_GROUP_HEADER_RE`의 title 패턴이 `(`를 허용하지 않아 첫 그룹 무성 누락
   - **해결**: 시스템 프롬프트에 "토픽 제목에 괄호 금지" 규칙 추가. 재실행 시 LLM 정확히 준수

2. **OpenSearch `prefix` on `_id` 거부**
   - 초기 `--purge` 구현이 `prefix` 쿼리 사용 → OS 3.4 거부
   - **해결**: month 지정 시 `_doc_id(team, week)` 결정론적 조립 → 명시적 단건 삭제. 미지정 시 `match_all` 후 클라이언트측 prefix 필터

---

## 5. UI 반복 개선 (4 라운드)

| 라운드 | 사용자 피드백 | 적용 |
|---|---|---|
| R1 | 섹션 1 GUI가 섹션 2~5와 겹친다, 라벨이 두 줄로 잘림 | 섹션 1을 stacked 카드로 분리 + 배지 폭 170 → 220px |
| R2 | 팀-내용이 같은 level에 있어야 함 (stacked 안 됨) | 섹션 1을 horizontal로 복귀, 폭/배경/폰트로 차별화 |
| R3 | 더 심플하고 간결, 임원 보고용 가독성 | 채움 배경 제거 → 미니멀 1px divider 스타일. 섹션 1만 미니멀, 섹션 2~5는 그대로 두라는 요청 |
| R4 | 전체 리포트 폭 확장, 팀 컬럼은 적절한 폭으로 복원 | 본문 wrapper 680 → **800px**, 섹션 1 라벨 140 → 120px, 섹션 2~5 배지 220 → **170px** |

### 최종 GUI 구성
- **전체 폭**: 800px
- **섹션 1**: 미니멀 (NAVY 15px bold 라벨 120px + 흰 본문 + 1px divider) — 임원 보고 톤
- **섹션 2~5**: NAVY filled 배지 170px + 흰 본문 + BORDER 셀 — 상세 정보 톤
- **섹션 6**: weekly와 동일 노란 cross-team 카드 — 이슈 강조 톤

---

## 6. 사용법

### 더미로 검증
```bash
# 1) 더미 80개(16팀 × 5주) 시드 + OpenSearch 인덱싱
python generate_dummy_team_weeks.py --month 2026-04

# 2) 월간 요약 생성 (md + OS)
python wiki_builder.py --monthly 2026-04

# 3) HTML 변환
python generate_monthly_report.py wiki/monthly/2026-04_월간요약.md

# 4) 브라우저 미리보기
open wiki/monthly/2026-04_월간요약.html

# 5) 검증 종료 후 정리
python generate_dummy_team_weeks.py --month 2026-04 --purge
```

### 실 운영
1. 정합 weekly 파이프라인이 한 달치 team-week 데이터를 `wiki_summaries` 인덱스에 적재
2. `python wiki_builder.py --monthly YYYY-MM` 한 줄로 월간 md 생성
3. `python generate_monthly_report.py wiki/monthly/YYYY-MM_월간요약.md` 으로 HTML 변환
4. HTML을 Outlook 본문으로 복사하거나 SMTP 첨부

---

## 7. 후속 과제

| 우선순위 | 항목 | 비고 |
|---|---|---|
| v1.1 | `save_wiki_doc`에 `month` 정식 필드 추가 + 마이그레이션 | 현재 `week=month` 재사용 임시 |
| v1.1 | weekly의 §3 프롬프트를 `CROSS_GROUPS_PROMPT_FRAGMENT` 사용으로 리팩터 | drift 방지 |
| v2 | §6 cross-month status (신규/지속) 어노테이션 | `annotate_cross_month_status` 신규 설계 필요 |
| v2 | RAG 경로의 `summary_type='monthly'` 포함 여부 정책 명문화 | topic timeline은 team-week만 보므로 직접 영향 제한적 |
| 정리 | 실 운영 데이터 인입 후 `generate_dummy_team_weeks.py` 삭제 | throwaway |

---

## 부록 A. 파일 변경 요약

| Path | Action |
|---|---|
| `generate_monthly_report.py` | **신규** (~340 LOC) |
| `wiki_builder.py` | **수정 (additive)** — 약 200 LOC 추가 + CLI 분기 |
| `generate_dummy_team_weeks.py` | **신규 (throwaway)** (~190 LOC) |
| `wiki/monthly/` | **디렉토리 신규** |
| `wiki/monthly/2026-04_월간요약.md` | LLM 산출물 |
| `wiki/monthly/2026-04_월간요약.html` | 변환기 산출물 (800px Outlook HTML) |
| `team_dict.py` | **변경 없음** (사용자가 16팀으로 정리해둔 상태) |
| `generate_outlook_report.py` | **변경 없음** (라이브러리로만 사용) |

## 부록 B. 디자인 결정 로그

- **2-stage vs 단일 호출**: 60KB 임계로 분기. 80건 평균 605자 → 약 48KB → 단일 호출 모드 작동. 임계 초과 시 16회 Stage 1 압축 후 1회 Stage 2 종합.
- **canonical vs display 라벨**: 섹션 5의 "(@HBM4E만)" 한정자는 표시용에만. 매칭/저장은 canonical로.
- **`week=month` 필드 재사용 (v1)**: 스키마 변경 회피. `summary_type="monthly"` + `doc_id` prefix로 식별. 단점: 모든 weekly 조회 코드가 `summary_type` 필터를 명시해야 함 → 감사 필요.
- **더미 doc_id prefix `dummy_`**: RAG/topic timeline 격리. `--purge` 플래그가 검증 후 즉시 정리.
- **임원 보고용 톤 분리**: 섹션 1만 미니멀(요약), 섹션 2~5는 filled 배지(상세). 동일 horizontal 레이아웃 유지로 일관성 + 폰트/배경/폭으로 위계 표현.
