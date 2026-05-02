# Monthly Cross-Team Timeline 마이그레이션 절차

`wiki_builder.py` / `generate_outlook_report.py` 의 monthly cross-team timeline 기능을 사내 환경에 적용할 때 따라야 할 단계 모음. 산출물은 섹션 6 (크로스팀이슈) 의 각 블록에 `- 2026-WW: ...` 형식의 주차별 timeline bullet 이 자동 삽입되는 것.

---

## 1. 사전 점검 (5분)

### 1.1 환경 변수
```bash
echo "OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-(unset)}"
echo "OPENROUTER_BASE_URL=${OPENROUTER_BASE_URL:-(unset)}"
echo "OPENSEARCH_HOST=${OPENSEARCH_HOST:-(unset)}"
```
- `.env` 또는 shell 에 `OPENROUTER_API_KEY` / `OPENROUTER_BASE_URL` 설정돼 있어야 함 (LLM 호출용).
- OpenSearch 가 동일 노드에서 동작하면 `OPENSEARCH_HOST` 미설정 시 `localhost:9200` default 사용.

### 1.2 의존성 확인
```bash
python -c "import openai, opensearchpy, pydantic; print('ok')"
python -c "from langchain_openai import ChatOpenAI; print('langchain ok')"
```
- `langchain_openai` 는 `extract_team_week_entities` 의 graceful degrade 분기에 사용 — 미설치여도 entity 추출 비활성화 후 다른 단계는 정상 동작.

### 1.3 OpenSearch 인덱스 점검
```bash
python -c "
from wiki_builder import get_client, WIKI_INDEX
c = get_client()
print('index exists:', c.indices.exists(index=WIKI_INDEX))
body = {'size': 0, 'aggs': {'by_type': {'terms': {'field': 'summary_type', 'size': 20}}}}
r = c.search(index=WIKI_INDEX, body=body)
for b in r['aggregations']['by_type']['buckets']:
    print(f'  {b[\"key\"]}: {b[\"doc_count\"]}건')
"
```
- 출력에 `team-week` 카운트가 있어야 monthly 생성 가능.
- 신규 type 인 `weekly-cross-issues` 는 처음엔 0건 — 첫 실행 후 채워짐 (Stage 1A 캐시).

---

## 2. 코드 동기화

```bash
git pull origin main
```

이번 변경에 포함된 코드 파일:
- `wiki_builder.py` — Stage 1A/1B/4 신규 함수, raw OpenAI tools verifier, monthly chain timeline 주입
- `generate_outlook_report.py` — `WEEK_LABEL_RE` + timeline_entries 분리/렌더링, status 코드 제거
- `generate_monthly_report.py` — render_section 호출 정리
- `scripts/eval_monthly_v2.py` — v1/v2 비교 평가 스크립트

---

## 3. 베이스라인 보존 (롤백 안전망)

기존 산출물 백업:
```bash
cp wiki/monthly/2026-04_월간요약.md  wiki/monthly/_baseline_pre_timeline.md
cp wiki/monthly/2026-04_월간요약.html wiki/monthly/_baseline_pre_timeline.html
```

---

## 4. 회귀 검증 (toggle off)

새 코드가 timeline annotate 를 우회했을 때 기존 출력과 동일한지 먼저 확인:
```bash
MONTHLY_CHAIN_ANNOTATE=0 python wiki_builder.py --monthly 2026-04
diff wiki/monthly/2026-04_월간요약.md wiki/monthly/_baseline_pre_timeline.md
```

기대:
- LLM 비결정성으로 sections 1~5 텍스트는 약간 다를 수 있음
- 섹션 6 cross-team 블록 헤더/팀 그룹은 거의 같아야 함
- timeline `- 2026-WW:` bullet 은 **없어야 함** (toggle off)

---

## 5. Timeline 적용 본 실행

```bash
python wiki_builder.py --monthly 2026-04
```

진행 로그:
- `[stage2]` 월간 종합 LLM 호출 (~60초)
- `[stage1a] 2026-WW: cross-team 이슈 N건` × 5주 (캐시 있으면 즉시, 없으면 ~30초/주차)
- `[stage4] chain 산출 N건 — ...`
- `[stage4 inject] M/N 블록에 timeline 주입 (주차 bullet 총 X건, chain 총 Y건)`
- `💾 사이드카 작성: wiki/monthly/2026-04_월간요약.md`

총 시간: **첫 실행 5~10분, 재실행 2분** (Stage 1A 캐시 hit 덕분).

---

## 6. HTML 렌더링

```bash
python generate_monthly_report.py wiki/monthly/2026-04_월간요약.md
```
출력: `wiki/monthly/2026-04_월간요약.html`

브라우저로 열어 확인:
- 섹션 6 의 각 cross-team 카드 안에 팀별 entry → `주차별 흐름` 라벨 + 1px divider → `2026-14` ~ `2026-18` 주차 chip + 본문 (11px MUTED) → 종합 row 순서
- 신규/지속/해소 chip 은 **없어야 함**

---

## 7. 정량 비교

```bash
python scripts/eval_monthly_v2.py \
  wiki/monthly/_baseline_pre_timeline.md \
  wiki/monthly/2026-04_월간요약.md
```

기대 출력:
- timeline 적용 블록: `5/5 (100%)` 식
- 블록당 평균 timeline bullet ≥ 1
- 블록별 등장 주차 리스트가 의도대로 나오는지 확인

---

## 8. 문제 발생 시 롤백

### 8.1 Timeline 만 끄기
```bash
MONTHLY_CHAIN_ANNOTATE=0 python wiki_builder.py --monthly 2026-04
```
- Stage 1A/1B/4 모두 우회. 섹션 1~5 + 섹션 6 (timeline 없는 기본) 만 생성.

### 8.2 Stage 1A 캐시 무효화 (잘못 추출됐을 때)
```bash
python -c "
from wiki_builder import get_client, WIKI_INDEX
c = get_client()
for w in ['2026-14','2026-15','2026-16','2026-17','2026-18']:
    try:
        c.delete(index=WIKI_INDEX, id=f'weekly-cross_{w}', refresh=False)
        print(f'deleted weekly-cross_{w}')
    except Exception as e:
        print(f'skip {w}: {e}')
c.indices.refresh(index=WIKI_INDEX)
"
```
- 다음 `--monthly` 실행 시 Stage 1A LLM 다시 호출.

### 8.3 코드 롤백
```bash
git revert <이번_커밋_해시>
```

---

## 9. 운영 주의사항

| 항목 | 내용 |
|---|---|
| 첫 실행 LLM 비용 | Stage 2 (1회) + Stage 1A (주차 수만큼) + Stage 1B verifier (10~50회). 월 1회 실행 기준 큰 부담 없음 |
| 재실행 비용 | Stage 1A 캐시(`weekly-cross_*` doc)로 거의 LLM 미호출. Stage 2 1회 + Stage 1B 일부 |
| `weekly-cross-issues` doc 누적 | OpenSearch 에 월 4~5건씩 쌓임. 정리 필요 시 [8.2] 사용 |
| GLM-4.7 reasoning 토큰 | Stage 2 `max_tokens=16000`, verifier `max_tokens=2000` 으로 설정됨. 모델 변경 시 재조정 필요 |
| Stage 1A 추출 누락 | LLM 비결정성으로 일부 주차에서 같은 cross-team 이슈가 빠질 수 있음. timeline 표현은 "안 빠진 주차만 표시" 라 안전 (해소/지속 단정 안 함) |

---

## 10. 다른 월 적용

```bash
python generate_dummy_team_weeks.py --month 2026-05    # (테스트 환경에서만, 운영은 실제 raw 사용)
python wiki_builder.py --monthly 2026-05
python generate_monthly_report.py wiki/monthly/2026-05_월간요약.md
```

운영 데이터의 경우 `generate_dummy_team_weeks.py` 호출은 생략하고 `team-week` summary 가 OpenSearch 에 이미 인덱싱된 상태에서 `wiki_builder.py --monthly YYYY-MM` 만 실행.

---

## 11. 새로 추가된 OpenSearch doc 종류

| summary_type | doc_id | text 내용 | 누가 생성 |
|---|---|---|---|
| `weekly-cross-issues` | `weekly-cross_{week}` | `WeeklyCrossExtraction.json()` 직렬화 (issues 리스트) | Stage 1A `extract_weekly_cross_team_issues` |

기존 `team-week`, `monthly`, `overview` 등은 변경 없음.
