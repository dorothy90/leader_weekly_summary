# RAG 검증 콘솔 설계

## 목적

완성된 Mail Research RAG의 Fast RAG와 Deep Research를 실제 API로 호출하고, 답변뿐 아니라 라우팅·검색·인용·예산·비동기 작업 상태까지 한 화면에서 검증한다. 이 화면은 일반 사용자용 챗봇이 아니라 개발·QA용 테스트 워크벤치다.

기존 `frontend/`의 Outlook 폴더 설정 화면은 유지한다. 같은 React/Vite 애플리케이션에 제품 전환 내비게이션과 `/rag` 화면을 추가한다.

## 확정된 범위

- 요청 본문의 `user_id` 입력
- 팀, 주차, 메일 유형 검색 필터
- Fast 또는 Deep 시스템의 명시적 선택
- Fast 동기 답변 표시
- Deep job 생성, 진행률 조회, SSE 이벤트 표시, 취소와 재시도
- 인용 근거, 검색 방식, 품질 상태, disclosures 표시
- 요청·응답 JSON, HTTP 상태, 지연시간, trace ID, conversation ID, job ID 표시
- Fast/Deep 예산 사용량을 API가 제공하는 범위에서 표시
- `/health`와 `/ready` 연결 상태 표시
- 오류 코드, 안전한 오류 메시지, retryable 여부 표시

Fast와 Deep은 별도 시스템으로 유지한다. UI는 자동 혼합하지 않으며 `Fast`와 `Deep`을 사용자가 직접 선택한다. API 계약 호환을 위해 요청의 `response_mode`에는 각각 `fast` 또는 `deep`만 전송한다.

## 접근 방식 비교

### 1. 기존 React 앱 안에 `/rag` 검증 콘솔 추가 — 선택

현재 Vite 개발 환경, React 19, Vitest, Testing Library를 그대로 사용한다. 폴더 설정 화면과 RAG 검증 화면을 상단 내비게이션으로 전환한다. 새 프레임워크가 필요 없고 기존 개발 명령을 유지할 수 있다.

### 2. 별도 `rag-frontend/` 애플리케이션

기존 화면과 완전히 분리되지만 의존성, 개발 서버, 빌드 설정, 테스트 구성이 중복된다. 테스트 도구 하나를 추가하는 범위에 비해 운영 부담이 크다.

### 3. FastAPI가 제공하는 단일 HTML 페이지

배포는 단순하지만 상태 관리, SSE, JSON 검사기, 접근성 테스트가 복잡해진다. 기존 React 환경을 사용하지 못하며 이후 확장성이 낮다.

## 화면 구조

### 상단 바

- `Weekly Mail` 제품명
- `Folder setup`과 `RAG Lab` 전환
- `/health`, `/ready` 상태와 마지막 확인 시각
- 현재 API target(`/api`, Vite의 `RAG_API_TARGET` 프록시 대상) 표시

### 왼쪽 요청 패널

- `user_id`: 필수. 요청 본문에만 포함하며 브라우저 영구 저장은 기본적으로 하지 않는다.
- 실행 시스템: `Fast`, `Deep` 두 선택지만 제공한다.
- `conversation_id`: 선택 입력. 비어 있으면 API가 새 ID를 만든다.
- 팀, 주차, 메일 유형 필터
- 질문 예시 목록과 요청 초기화
- Fast 예산 또는 Deep 예산의 정적 상한 설명

팀과 주차는 검색 필터일 뿐 권한이 아니다. 화면에 이를 명시하고, 모든 요청은 `user_id`를 함께 전송한다.

### 중앙 대화·결과 패널

- 사용자 질문과 답변을 시간 순서로 표시
- Fast는 최종 답변, disclosures, quality 상태를 표시
- Deep은 job 생성 직후 queued/running 상태 카드로 전환하고 진행률을 갱신
- completed 시 보고서 Markdown을 안전한 일반 텍스트 구조로 렌더링
- references는 `[S#]`, 유형, 제목, 발췌, 팀, 주차를 표시
- BM25 폴백이면 다음 문구를 눈에 띄는 경고로 정확히 표시한다.

> 임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다.

### 오른쪽 검사기

세 개의 탭을 둔다.

1. `Summary`
   - HTTP 상태, 지연시간, route/mode, retrieval mode
   - trace ID, conversation ID, job ID
   - citation valid, limited answer, reference 수
   - error code와 retryable
2. `JSON`
   - 실제 전송 요청과 수신 응답을 접을 수 있는 JSON 트리와 복사 버튼으로 표시
   - 사용자 입력을 다시 편집하는 기능은 제공하지 않는다.
3. `Events`
   - Deep SSE 이벤트의 수신 시각, status, progress
   - 연결/재연결/완료/오류 상태

API가 검색 호출 수나 토큰 사용량 같은 런타임 예산 사용량을 반환하지 않으면 UI가 추정하거나 꾸미지 않는다. 대신 계약상 상한과 현재 API에서 확인 가능한 진행률만 표시하고 `서버 미제공`으로 구분한다.

## 시각 디자인

주제는 메일 조사 시스템을 검증하는 엔지니어링 계기판이다. 기존 폴더 설정 화면의 라벤더 계열을 유지하되, 검증 콘솔은 정보 밀도가 높은 3열 구조를 사용한다.

- `Canvas` `#F4F6FA`: 전체 배경
- `Surface` `#FFFFFF`: 패널
- `Ink` `#263149`: 주요 텍스트
- `Signal Blue` `#5369DA`: 실행·선택·진행 상태
- `Verified Green` `#16804D`: 검증 성공
- `Fallback Amber` `#9A6700`: BM25·제한 응답 경고
- 표시 글꼴은 시스템 산세리프, JSON과 식별자는 시스템 모노스페이스
- 데스크톱은 요청 220–260px, 결과 가변 폭, 검사기 300–360px
- 태블릿에서는 검사기를 아래로 이동하고 모바일에서는 요청·대화·검사기를 탭으로 전환
- 기억에 남는 요소는 요청마다 갱신되는 `verification strip`이다. 소유자 경계, 인용, 검색 모드, 오류 상태를 하나의 얇은 상태선으로 표현한다.

장식용 그라데이션, 과도한 애니메이션, 큰 영웅 영역은 사용하지 않는다. 새 응답과 Deep 상태 변화에만 짧은 강조 전환을 적용하고 `prefers-reduced-motion`을 존중한다.

## 프론트엔드 구성 요소

### 서비스 계층

`frontend/src/services/ragApiService.ts`

- API base URL 정규화
- `sendChat(request)`
- `getResearchStatus(jobId, userId)`
- `cancelResearch(jobId, userId)`
- `retryResearch(jobId, userId)`
- `openResearchEvents(jobId, userId, handlers)`
- `getHealth()`과 `getReadiness()`
- HTTP 상태, 응답 시간, 안전한 오류 envelope를 공통 `ApiExchange`로 반환

SSE endpoint가 POST body를 요구하므로 브라우저 기본 `EventSource` 대신 `fetch` streaming reader를 사용한다. `AbortController`로 화면 전환, 새 요청, 완료 시 연결을 해제한다.

### 상태와 화면

- `RagLabApp`: 요청 조건, 대화, 선택된 exchange, job 상태 관리
- `RequestPanel`: user ID, 모드, conversation ID, 필터, 질문 입력
- `ConversationPanel`: 답변, 보고서, disclosures, references
- `InspectorPanel`: Summary, JSON, Events
- `ConnectionStatus`: health/readiness
- `ResearchJobControls`: 진행률, 취소, 재시도
- `ProductNav`: 기존 폴더 설정과 RAG Lab 전환

외부 상태 관리 라이브러리는 추가하지 않는다. 화면 상태와 요청 취소 핸들은 React state/ref로 관리한다.

## 데이터 흐름

### Fast

1. 사용자가 `user_id`, 필터, `Fast`, 질문을 입력한다.
2. UI가 `POST /v1/chat`에 `response_mode: "fast"`를 전송한다.
3. HTTP 상태, 지연시간, 안전한 요청/응답 JSON을 exchange로 기록한다.
4. 답변, references, quality, disclosures를 중앙 패널과 검사기에 표시한다.
5. 응답 conversation ID를 다음 요청에 사용할 수 있게 유지한다.

### Deep

1. UI가 `POST /v1/chat`에 `response_mode: "deep"`를 전송한다.
2. `202`, job ID, queued 상태를 표시한다.
3. `POST /v1/research/{job_id}/events` 스트림을 열어 진행 이벤트를 기록한다.
4. 각 이벤트 또는 연결 실패 시 owner-scoped status endpoint로 상태를 확인한다.
5. completed 시 최종 보고서와 references를 표시한다.
6. failed/cancelled 시 retry 버튼, queued/running 시 cancel 버튼을 제공한다.

## 오류 처리

- 네트워크 실패: 연결 대상과 재시도 방법 표시
- `422`: user ID, 질문, 필터 형식을 필드 가까이에 표시
- `404`: 타인/없는 리소스를 구분하지 않고 API의 안전한 메시지만 표시
- `503 INDEX_UNAVAILABLE`: retryable 상태와 검색 서비스 장애 표시
- `504 RETRIEVAL_TIMEOUT`: Fast 제한시간 초과 표시
- SSE 단절: 한 번 재연결 후 status polling으로 전환
- 잘못된 JSON 또는 비표준 오류: 원문 HTML을 렌더링하지 않고 HTTP 상태와 안전한 일반 메시지만 표시

UI는 비밀번호, API key, Authorization header, 쿠키, 원시 파일 경로, 내부 chain-of-thought를 수집하거나 표시하지 않는다. 검사기의 요청 JSON에는 `user_id`가 보이지만 브라우저 저장소에는 기록하지 않는다.

## 테스트

### 서비스 테스트

- Fast 요청 body가 정확한 `user_id`, `response_mode`, 필터를 전송
- Deep job status/cancel/retry가 body owner를 포함
- POST streaming event 파싱과 AbortController 종료
- 안전한 API 오류 envelope 파싱
- 지연시간과 HTTP 상태 기록

### 컴포넌트 테스트

- Fast/Deep 두 모드만 선택 가능
- Fast 응답, references, quality, disclosures 표시
- 정확한 BM25 폴백 문구 표시
- Deep queued → running → completed 상태와 progress 표시
- failed/cancelled 상태에서만 retry 제공
- JSON 탭이 실제 exchange를 표시하되 헤더·비밀정보는 제외
- 타인/없는 job의 동일한 404 표현
- 키보드 탐색, focus-visible, 모바일 탭 전환

### 검증 명령

- `npm test`
- `npm run lint`
- `npm run build`
- FastAPI TestClient와 프론트 서비스 계약 fixture의 필드 일치 검사

## 제외 범위

- 로그인 또는 upstream gateway 구현
- 운영용 사용자 권한 관리
- 메일 원문 전체 표시
- 프론트에서 OpenSearch, MongoDB, LLM을 직접 호출
- 서버가 반환하지 않는 내부 LangGraph 상태나 토큰 수 추정
- 내부 prompt 또는 chain-of-thought 표시

## 성공 기준

- 한 화면에서 Fast와 Deep의 정상·오류·폴백 경로를 실제 API로 재현할 수 있다.
- 요청·응답·trace·job·인용·disclosure를 서로 대응시켜 구현 여부를 확인할 수 있다.
- UI가 `user_id` 소유자 계약을 항상 전송하며 team을 권한으로 표현하지 않는다.
- BM25 폴백 안내가 정확한 문구로 보인다.
- 비밀정보, 원시 경로, 내부 사고과정이 검사기나 브라우저 저장소에 남지 않는다.
