# Mail Knowledge Web MVP 구현 계획

## 현재 구현 상태 (2026-07-12)

완료:

- 더미 taxonomy 7 Tech, 14 LOTCD
- 더미 메일 12개, 정답 agenda 29개
- FastAPI Knowledge 조회·검토·alias API
- SQLite canonical 저장소와 fixture seed
- Explorer 3단 화면, 직접 언급·하위 포함, 원문 highlight
- 기간·발신팀·topic·상태·검토 filter와 URL 상태 보존
- Review 분류 확정·보류, 확정 agenda 다중 경로 재수정과 revision 저장
- Mapping Admin alias 추가·수정·soft delete·변경 이력
- Mapping Admin Tech·LOTCD 추가·수정·삭제·연결 보호
- LLM agenda structured split과 canonical resolver
- 인용문 제거, source offset, 미등록 LOTCD review routing
- 주간 pipeline feature flag 연결
- 별도 OpenSearch `mail_agendas` index와 confirmed agenda 동기화
- SQLite transaction 기반 OpenSearch sync queue와 재시도 command
- header 인증 hook과 editor role 기반 변경 권한
- FastAPI production build 직접 제공
- Obsidian dark 기반 `/wiki/docs` Vault·Reader·Metadata 3단 화면
- 기준 `wiki_frontend`와 동일한 280px Vault·760px Reader·320px Metadata 구조
- Category index note, Agenda Wiki note, 분류별 Metadata
- Evidence trail, 원본 Mail modal, Backlink, Timeline, Fab pair 비교
- Review 전역 Inbox와 J/K·H keyboard flow
- lazy-loaded `/wiki/graph` Local Graph, node type·depth·검색 filter
- 기존 `/explorer` Table View 보존
- OpenSearch 기반 `category_wiki_builder.py`와 분류별 영구 Wiki 문서
- 업무 Pending·Resolved·Reopened 주차 이력 및 분류 Pending 별도 표시

검증 결과:

```text
Backend tests: 47 passed
Frontend tests: 15 passed
Frontend production build: passed
Dummy resolver target path accuracy: 100%
Dummy resolver scope accuracy: 100%
Actual LLM dummy mail split: 3/3 agenda와 path 일치
OpenSearch confirmed agenda index: 28 documents
Browser QA: Wiki Docs·Review·Graph·Source modal, 1280px/200% 상당 viewport overflow 없음, console error 0
Category Wiki: latest 23 docs + 2026-28 snapshot 23 docs
```

남은 운영 입력:

- 실제 Tech·LOTCD·제품·alias 기준정보
- 사내 인증 방식
- 실제 메일 외부 LLM 전송 정책 또는 사내 모델 endpoint
- 실제 사내 브라우저·SSO 환경 acceptance test

## 로컬 실행

API:

```bash
python -m uvicorn knowledge_preview:app --host 127.0.0.1 --port 8002
```

Web:

```bash
cd web
npm install
npm run dev -- --host 127.0.0.1
```

접속:

```text
http://127.0.0.1:5173/explorer
http://127.0.0.1:5173/review
http://127.0.0.1:5173/mappings
http://127.0.0.1:5173/wiki/docs
http://127.0.0.1:5173/wiki/graph
```

Production build는 FastAPI가 직접 제공할 수 있다:

```bash
cd web && npm ci && npm run build
cd .. && python -m uvicorn knowledge_preview:app --host 0.0.0.0 --port 8002
```

이 경우 `http://localhost:8002/explorer`, `/mappings`에서 접근한다. 별도 build 경로는 `KNOWLEDGE_WEB_DIST`로 지정한다.

분류 교정 또는 신규 추출 후 OpenSearch sync가 실패하면 SQLite queue에 남는다. 재시도:

```bash
python scripts/sync_agenda_opensearch.py
```

운영 검색 backend:

```bash
export KNOWLEDGE_SEARCH_BACKEND=opensearch
```

확정 agenda는 Nori BM25 순위를 사용한다. 미확정 agenda와 sync 대기 메일은 SQLite 검색으로 합성하며 OpenSearch 장애 시 전체 SQLite 검색으로 fallback한다.

테스트:

```bash
python -m pytest -q tests
python scripts/eval_agenda_resolver.py
cd web && npm test && npm run build
```

주간 pipeline에서 agenda 추출은 기본 비활성이다. 실제 taxonomy와 LLM 보안 정책을 확정한 뒤 `ENABLE_AGENDA_EXTRACTION=true`로 활성화한다.

운영 taxonomy 초기화:

```bash
export KNOWLEDGE_TAXONOMY_PATH=/secure/config/taxonomy.json
export KNOWLEDGE_DB_PATH=/secure/data/knowledge.db
```

새 DB를 처음 열 때 지정 taxonomy만 category·alias로 저장하며 dummy 메일은 넣지 않는다. 운영 taxonomy는 fixture와 같은 JSON schema를 사용하고 `is_dummy`를 `false`로 설정한다. 이미 생성된 dummy DB 경로를 운영에 재사용하지 않는다.

명시적 DB seed:

```bash
# demo fixture 포함
python scripts/seed_knowledge_db.py --db-path data/knowledge/demo.db

# 실제 taxonomy만 포함
python scripts/seed_knowledge_db.py \
  --db-path /secure/data/knowledge.db \
  --taxonomy /secure/config/taxonomy.json
```

기존 DB 교체는 `--reset`을 명시해야 한다.

인증 mode:

```bash
export KNOWLEDGE_AUTH_MODE=header
```

운영 reverse proxy가 SSO 인증 후 `X-User-Id`, `X-User-Roles`를 설정한다. 변경 API는 `knowledge-editor` role을 요구하며 revision의 변경자는 client body가 아니라 인증 identity에서 기록한다. Proxy는 외부 요청의 동일 header를 제거한 뒤 신뢰 가능한 값만 다시 설정해야 한다. 로컬 dummy demo는 기본 `disabled` mode를 사용한다.

LLM endpoint 설정:

```bash
export KNOWLEDGE_LLM_BASE_URL=https://internal-openai-compatible.example/v1
export KNOWLEDGE_LLM_API_KEY=...
export KNOWLEDGE_LLM_MODEL=approved-model
export KNOWLEDGE_LLM_DATA_POLICY_ACK=true
export ENABLE_AGENDA_EXTRACTION=true
```

`KNOWLEDGE_LLM_*`가 기존 `OPENROUTER_*`보다 우선한다. 주간 pipeline은 data-policy 승인 flag가 없으면 메일 수집 전에 중단한다.

## 목표

메일을 독립 agenda로 분해하고 `DRAM/NAND > Tech > LOTCD`에 mapping한 뒤, 사용자가 Web에서 탐색·검토·교정할 수 있게 한다.

제품명, 용량, 성능, Fab ID는 분류 단계가 아니라 LOTCD metadata로 취급한다. 하나의 agenda가 여러 LOTCD 또는 DRAM·NAND 양쪽에 걸칠 수 있다.

## 구현 전제

- `DRAM/NAND > Tech > LOTCD`는 고정 3단계다.
- 상위 항목 선택 시 `직접 언급`과 `하위 포함`을 분리한다.
- 같은 제품 별칭이 여러 LOTCD에 해당하면 모든 LOTCD를 연결한다.
- 분류 근거가 되는 원문을 항상 보존한다.
- 미등록 LOTCD와 낮은 신뢰도 결과는 자동 확정하지 않고 검토함으로 보낸다.
- `fixtures/knowledge/taxonomy.json`의 NAND LOTCD와 일부 제품 metadata는 UI 검증용 가상 값이다. 운영 전 실제 기준정보로 교체해야 한다.

## 저장소 현황과 선택

현재 저장소는 FastAPI, OpenSearch, Streamlit 기반이다. 사용자용 Web frontend는 없고 Streamlit은 RAG API 테스트 화면이다. `chunk_classify.py`는 domain과 기존 Tech code를 분류하지만 목표 계층과 scope 모델이 다르다.

MVP 선택:

- 기존 Streamlit 화면 유지
- 사용자용 `web/` React + TypeScript + Vite 앱 신설
- 기존 FastAPI 앱에 `/api/knowledge/*` endpoint 추가
- 첫 vertical slice는 JSON fixture를 읽어 제공
- 교정 기능 추가 시 SQLite를 canonical 저장소로 사용
- OpenSearch는 원문·agenda 검색과 운영 데이터 색인에 사용
- 기존 `weekly_mail` index는 바로 변경하지 않고 agenda용 index를 분리

SQLite를 먼저 쓰는 이유는 mapping 교정, review 상태, 수정 이력 같은 정합성 있는 쓰기 작업이 필요하기 때문이다. OpenSearch는 검색용 파생 저장소로 둔다.

## 준비된 더미 데이터

- `fixtures/knowledge/taxonomy.json`
  - DRAM: Spica, Canopus, Lucy, Procyon
  - NAND: Heraion, Colosseum, Petra
  - Tech별 LOTCD, Fab ID, 제품 metadata, 별칭
- `fixtures/knowledge/mails.json`
  - 12개 가상 메일
  - domain 전체, Tech 전체, LOTCD 직접 언급, 제품 별칭, 다중 LOTCD, DRAM/NAND 교차, 미등록 LOTCD, 답장 인용문 포함
- `fixtures/knowledge/expected_agendas.json`
  - 29개 정답 agenda
  - source quote, scope, target path, topic, 상태, 신뢰도 포함
  - 답장 인용문 제외 정답 포함

더미 데이터는 `.invalid` 메일 주소와 `dummy_` ID를 사용한다. 운영 데이터와 섞지 않는다.

## 화면 구조

### Wiki Docs — 기본 화면

기본 route:

```text
/wiki/docs
/wiki/docs/dram/spica/4sa
/wiki/graph
```

Obsidian dark UI를 기준으로 다음 구조를 사용한다.

```text
280px Vault | 760px Markdown-style Reader | 320px Metadata
```

- Category 선택 시 자동 구성된 index note 표시
- Category index의 Recent agenda 또는 Metadata의 Linked notes에서 Wiki note 표시
- Metadata에서 Frontmatter, Evidence trail, Backlink, Timeline 표시
- LOTCD product metadata로 Fab pair 연결
- Source mail modal에서 정확한 source quote highlight
- Graph는 별도 lazy chunk이며 선택 node 주변 Local Graph 지원
- Review는 category path와 무관한 전역 Inbox
- `/explorer`는 기존 고밀도 Table View로 유지

### Explorer

기본 route:

```text
/explorer
/explorer/dram
/explorer/dram/spica
/explorer/dram/spica/4sa
```

3단 분할:

```text
분류 트리 | agenda 목록 | agenda 상세와 메일 원문
```

필수 동작:

- 분류 node 선택
- `직접 언급` / `하위 포함` 전환
- 기간, 발신팀, topic, 상태, 검토 여부 filter
- 검색어와 filter를 URL query에 저장
- agenda 선택 시 원문 근거 highlight
- 여러 target path를 가진 agenda는 한 건으로 표시하고 모든 path badge 노출

### Review

route: `/review`

- 낮은 신뢰도, 미등록 LOTCD, 충돌 alias만 표시
- 원문과 분류 결과 좌우 비교
- agenda 합치기·나누기는 후속 단계
- MVP에서는 target path 수정, 확정, 보류만 제공
- 수정 후 새로고침해도 결과 유지

### Mapping Admin

route: `/mappings`

- Domain, Tech, LOTCD, Fab ID, 제품 metadata 조회
- alias 추가·수정
- 같은 alias가 여러 LOTCD에 연결되는 group mapping 지원
- mapping 변경 이력 표시

## 데이터 모델

```text
category
  id, level(domain|tech|lotcd), name, parent_id

lotcd_metadata
  category_id, fab_id, product_code, product

alias
  id, value, normalized_value

alias_target
  alias_id, category_id

mail
  id, subject, sender_team, sender, received_at, body, reply_to

agenda
  id, mail_id, summary, source_quote, source_start, source_end,
  scope, topic, state, confidence, review_status

agenda_target
  agenda_id, category_id

classification_revision
  id, agenda_id, before_json, after_json, changed_at, changed_by
```

한 agenda와 여러 category 연결을 허용한다. 집계할 때 `agenda_target` 행 수가 아니라 고유 agenda ID를 센다.

## API 계약

### 조회

```text
GET /api/knowledge/taxonomy
GET /api/knowledge/agendas
GET /api/knowledge/agendas/{agenda_id}
GET /api/knowledge/mails/{mail_id}
GET /api/knowledge/review-queue
```

`GET /api/knowledge/agendas` query:

```text
domain=DRAM
tech=Spica
lotcd=4SA
scope_mode=direct|descendants
topic=yield
state=open
review_status=pending
q=장비 조건
date_from=2026-07-01
date_to=2026-07-31
```

### 교정

```text
PATCH /api/knowledge/agendas/{agenda_id}/classification
POST  /api/knowledge/aliases
PATCH /api/knowledge/aliases/{alias_id}
```

수정 API는 기존 값, 변경 값, 사용자, 시간을 revision에 남긴다.

## agenda 추출 순서

1. 답장 header와 인용문 분리
2. 표, bullet, 문단의 상위 문맥 보존
3. LLM이 독립 agenda와 정확한 source quote 추출
4. 규칙 기반 LOTCD·Tech·alias 탐지
5. 기준정보로 canonical path resolve
6. target path에 따라 scope 계산
7. source quote가 원문에 존재하는지 검증
8. 미등록·충돌·낮은 신뢰도 결과를 review queue에 저장
9. 확정 agenda만 OpenSearch agenda index에 색인

LLM이 category를 자유 생성하지 못하게 한다. LLM은 agenda 분해와 문맥 판단을 담당하고, 최종 path는 기준정보 resolver가 결정한다.

새 구현은 `chunk_classify.py`를 즉시 변경하지 않는다. `agenda_extract.py`와 Pydantic schema를 별도로 만든 뒤 fixture 평가를 통과하면 기존 pipeline 연결을 결정한다.

## 단계별 구현

### 1. Fixture API와 read-only Explorer

작업:

- FastAPI에 fixture 조회 endpoint 추가
- `web/` React 앱 생성
- App shell, taxonomy tree, scope toggle, agenda list, detail panel 구현
- URL path와 filter 상태 연결
- source quote highlight 구현

검증:

- 12개 메일과 29개 agenda 조회
- DRAM, Spica, 4SA 선택 시 목록 범위가 달라짐
- direct와 descendants 결과가 구분됨
- 다중 LOTCD agenda가 중복 row로 보이지 않음
- production build 성공

### 2. SQLite 저장과 Review

작업:

- fixture seed CLI 추가
- SQLite schema와 migration 추가
- review queue와 classification 수정 API 구현
- Review 화면 구현
- revision 기록 구현

검증:

- `4ZZ`가 review queue에 표시
- `4ZZ`의 Tech·LOTCD mapping 수정 후 새로고침해도 유지
- 변경 전후 revision 조회 가능

### 3. Mapping Admin

작업:

- category·metadata·alias CRUD 구현
- group alias의 다중 LOTCD 연결 구현
- alias 충돌 검사 구현

검증:

- `SP LPDDR5 24G`가 4SA·6SA 모두 resolve
- 중복 alias 등록 시 충돌 표시
- category 삭제는 연결된 agenda가 있으면 차단

### 4. 자동 agenda 추출

작업:

- Pydantic `AgendaDraft` schema 작성
- 인용문 제거와 source offset 계산
- LLM structured output 추가
- canonical resolver와 confidence 규칙 추가
- fixture 정답 평가 script 작성

검증:

- 알려진 LOTCD target path exact match 95% 이상
- domain·Tech·LOTCD scope exact match 95% 이상
- `dummy_mail_012` 인용문 중복 agenda 미생성
- `dummy_mail_011` 미등록 4ZZ 자동 확정 금지
- 모든 agenda source quote가 원문 substring

### 5. 기존 mail pipeline 연결

작업:

- `process_attachment` 이후 agenda 추출 단계 연결
- agenda용 OpenSearch index 생성
- FastAPI 검색을 fixture/SQLite에서 운영 index로 전환
- 실패한 메일 재처리 command 추가

검증:

- 기존 메일 수집·RAG pipeline 회귀 없음
- 같은 mail ID 재처리 시 agenda 중복 생성 없음
- 부분 실패 시 원본 메일과 기존 agenda 보존

## frontend 파일 계획

```text
web/
  src/
    app/
      router.tsx
    pages/
      ExplorerPage.tsx
      ReviewPage.tsx
      MappingPage.tsx
    components/
      TaxonomyTree.tsx
      ScopeToggle.tsx
      AgendaList.tsx
      AgendaDetail.tsx
      EvidenceText.tsx
    api/
      knowledge.ts
    styles/
      tokens.css
      app.css
```

초기 상태 관리는 React state와 URL로 제한한다. 전역 상태 library와 대형 table library는 MVP에서 제외한다.

## 시각 방향

- desktop-first, 최소 목표 폭 1280px
- 데이터 밀도 높은 작업대 형태
- DRAM 청색, NAND 보라색
- LOTCD는 mono typography와 outline badge
- 색상은 domain, 상태, review 경고 의미에만 사용
- breadcrumb를 `DRAM / Spica / 4SA` category rail로 표현
- 큰 KPI 카드와 장식용 chart는 MVP에서 제외
- keyboard focus, reduced motion, 200% zoom을 검증

## 테스트와 완료 기준

Backend:

- fixture schema validation
- category descendant query
- direct scope query
- multi-target deduplication
- revision persistence
- unknown LOTCD review routing

Frontend:

- taxonomy 선택과 URL 동기화
- direct/descendants 전환
- agenda 상세 선택
- source highlight
- review 수정 흐름

최종 완료 기준:

- 사용자가 DRAM에서 Spica, 4SA까지 내려가며 agenda를 탐색 가능
- 상위 직접 agenda와 하위 포함 agenda를 혼동하지 않음
- 각 결과에서 원문 근거 확인 가능
- 틀린 mapping을 Web에서 고치고 이력 확인 가능
- 더미 정답 fixture 기준 분류 정확도 목표 충족
- 기존 Streamlit과 주간메일 pipeline 동작 유지

## MVP 제외 범위

- chat UI 통합
- dashboard chart 다수
- Graph DB
- 모바일 편집
- 자동 메일 발송
- 사람 승인 없는 mapping 자동 학습

## 운영 전 필수 입력

- 실제 DRAM/NAND Tech 목록
- 실제 LOTCD와 Tech mapping
- 제품명·용량·성능 metadata
- 현업 alias 목록
- 사내 인증 방식
- 실제 메일을 외부 LLM에 전송할 수 있는지에 대한 보안 정책

실제 메일은 보안 정책과 모델 배치 위치가 확정되기 전 외부 LLM으로 보내지 않는다.
