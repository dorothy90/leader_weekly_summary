# RAG Verification Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a React/Vite RAG verification console that exercises the real Fast and Deep mail RAG APIs and exposes safe request, response, citation, job, event, timing, and error diagnostics.

**Architecture:** Extend the existing `frontend/` application with a small pathname router and an `/rag` workspace while keeping the folder setup wizard at `/`. A typed `RagApiService` owns every HTTP and POST-streaming call; React components consume safe `ApiExchange` records and never call API endpoints directly. The Vite development proxy sends `/api/*` to the FastAPI process so the browser needs no permissive CORS configuration.

**Tech Stack:** React 19, TypeScript 6, Vite 8, Vitest, React Testing Library, native `fetch`, native `ReadableStream`, CSS.

## Global Constraints

- Fast and Deep are separate systems; the UI exposes only explicit `Fast` and `Deep` choices and sends `response_mode: "fast"` or `response_mode: "deep"`.
- Every chat, research status, cancel, retry, and event request sends the request-body `user_id`; team is a search facet, never authorization.
- Do not persist `user_id`, request bodies, responses, API headers, or event payloads in `localStorage` or `sessionStorage`.
- Never display or collect passwords, API keys, Authorization headers, cookies, raw filesystem paths, prompts, or chain-of-thought.
- Display the exact fallback disclosure: `임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다.`
- Show only server-provided runtime metrics. Static Fast/Deep caps may be labeled as contract limits; unavailable runtime counters must say `서버 미제공`.
- Foreign, missing, and malformed resources use the API's same safe not-found message; the UI must not infer which case occurred.
- Preserve the existing Outlook folder setup flow and its tests.
- Support 320px mobile width, keyboard focus, and `prefers-reduced-motion`.

## File Structure

```text
frontend/src/
├── App.tsx                         # lightweight pathname routing and product navigation
├── styles.css                     # existing wizard styles plus verification-console tokens/layout
├── rag/
│   ├── RagLabApp.tsx              # orchestration and request/job state
│   ├── RequestPanel.tsx           # owner, filters, mode, question form
│   ├── ConversationPanel.tsx      # answers, reports, disclosures, references, job controls
│   ├── InspectorPanel.tsx         # Summary / JSON / Events diagnostics
│   ├── ConnectionStatus.tsx       # health and readiness checks
│   ├── types.ts                    # frontend API and view-state contracts
│   └── __tests__/
│       ├── RagLabApp.test.tsx
│       └── InspectorPanel.test.tsx
├── services/
│   ├── ragApiService.ts            # fetch, POST stream, timing, safe error parsing
│   └── ragApiService.test.ts
└── components/
    └── ProductNav.tsx              # Folder setup / RAG Lab navigation
```

---

### Task 1: Typed RAG API service and development proxy

**Files:**
- Create: `frontend/src/rag/types.ts`
- Create: `frontend/src/services/ragApiService.ts`
- Create: `frontend/src/services/ragApiService.test.ts`
- Modify: `frontend/vite.config.ts`

**Interfaces:**
- Produces: `RagApiService`, `ChatPayload`, `ChatResponse`, `ResearchJobResponse`, `ResearchEvent`, `ApiExchange<T>`, `SafeApiError`
- Consumes: FastAPI contracts from `app/domain/chat.py` and `app/api/routes/research.py`

- [ ] **Step 1: Define failing service tests**

Create tests that stub `global.fetch` and assert the exact owner-scoped request bodies:

```ts
it('sends explicit fast chat with owner and facets', async () => {
  fetchMock.mockResolvedValue(jsonResponse({
    conversation_id: 'c1', mode: 'fast_rag', answer: '답 [S1]',
    references: [], quality: { citation_valid: true, limited_answer: false, retrieval_mode: 'hybrid' },
    disclosures: [], trace_id: 't1',
  }))
  await service.sendChat({
    user_id: 'kim', message: '질문', response_mode: 'fast',
    filters: { teams: ['YIELD'], weeks: ['2026-31'], mail_type: 'weekly' },
  })
  expect(fetchMock).toHaveBeenCalledWith('/api/v1/chat', expect.objectContaining({
    method: 'POST',
    body: JSON.stringify(expect.objectContaining({ user_id: 'kim', response_mode: 'fast' })),
  }))
})

it.each(['status', 'cancel', 'retry'])('sends owner for research %s', async (action) => {
  fetchMock.mockResolvedValue(jsonResponse(completedJob))
  await service.researchAction('job-1', action, 'kim')
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ user_id: 'kim' })
})
```

Add tests for HTTP status and elapsed time capture, safe FastAPI error envelopes, non-JSON failure without HTML rendering, POST event-stream parsing across split chunks, and `AbortSignal` cancellation.

- [ ] **Step 2: Run the tests and confirm RED**

Run: `cd frontend && npm test -- src/services/ragApiService.test.ts`

Expected: FAIL because `ragApiService.ts` and `rag/types.ts` do not exist.

- [ ] **Step 3: Define complete frontend contracts**

Implement discriminated types with the server's exact field names:

```ts
export type ExecutionMode = 'fast' | 'deep'
export interface RetrievalFilters { teams: string[]; weeks: string[]; mail_type?: string }
export interface ChatPayload {
  user_id: string
  message: string
  conversation_id?: string
  filters: RetrievalFilters
  response_mode: ExecutionMode
}
export interface ApiExchange<T> {
  request: { method: 'GET' | 'POST'; path: string; body?: unknown }
  response?: T
  status: number
  durationMs: number
  receivedAt: string
  error?: SafeApiError
}
```

Include `ChatReference`, `QualityStatus`, `ChatResponse`, `ResearchJobResponse`, `ResearchEvent`, and `SafeApiError`. Do not include headers or cookies in diagnostic types.

- [ ] **Step 4: Implement the service boundary**

Implement `requestJson<T>()` with `performance.now()`, JSON content checks, and safe errors. Implement:

```ts
sendChat(payload: ChatPayload): Promise<ApiExchange<ChatResponse>>
researchAction(jobId: string, action: 'status'|'cancel'|'retry', userId: string): Promise<ApiExchange<ResearchJobResponse>>
streamResearchEvents(jobId: string, userId: string, onEvent: (event: ResearchEvent) => void, signal: AbortSignal): Promise<void>
getHealth(): Promise<ApiExchange<HealthResponse>>
getReadiness(): Promise<ApiExchange<ReadinessResponse>>
```

Normalize base URLs, encode `jobId` as one path segment, and parse `data:` SSE frames from a POST `fetch` stream. On unknown payloads use `요청을 처리할 수 없습니다.` without including response HTML.

- [ ] **Step 5: Add the Vite API proxy**

Update `vite.config.ts`:

```ts
server: {
  proxy: {
    '/api': {
      target: process.env.RAG_API_TARGET ?? 'http://127.0.0.1:8000',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, ''),
    },
  },
},
```

- [ ] **Step 6: Verify and commit**

Run:

```bash
cd frontend
npm test -- src/services/ragApiService.test.ts
npm run build
```

Expected: service tests PASS and TypeScript/Vite build exits 0.

Commit:

```bash
git add frontend/src/rag/types.ts frontend/src/services/ragApiService.ts frontend/src/services/ragApiService.test.ts frontend/vite.config.ts
git commit -m "feat(ui): add rag api client"
```

---

### Task 2: Product navigation and request workspace

**Files:**
- Create: `frontend/src/components/ProductNav.tsx`
- Create: `frontend/src/rag/RequestPanel.tsx`
- Create: `frontend/src/rag/ConnectionStatus.tsx`
- Create: `frontend/src/rag/__tests__/RequestPanel.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `ExecutionMode`, `RetrievalFilters`, `RagApiService`
- Produces: `RagRequestDraft`, `RequestPanel.onSubmit(draft)`, pathname navigation to `/` and `/rag`

- [ ] **Step 1: Write failing routing and form tests**

Test that `/` renders the existing connection wizard and `/rag` renders the lab. Test the request panel:

```ts
it('submits only explicit fast or deep mode with normalized facets', async () => {
  render(<RequestPanel onSubmit={onSubmit} disabled={false} />)
  await user.type(screen.getByLabelText('user_id'), ' kim ')
  await user.click(screen.getByRole('button', { name: 'Deep' }))
  await user.type(screen.getByLabelText('질문'), '4주 보고서')
  await user.click(screen.getByRole('button', { name: '실행' }))
  expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({
    userId: 'kim', mode: 'deep', question: '4주 보고서',
  }))
})
```

Assert there is no `Auto` control, `user_id` is required, invalid weeks are rejected, team help text says it is not authorization, and no draft is written to web storage.

- [ ] **Step 2: Run and confirm RED**

Run: `cd frontend && npm test -- src/App.test.tsx src/rag/__tests__/RequestPanel.test.tsx`

Expected: FAIL because the navigation and request components do not exist.

- [ ] **Step 3: Implement the lightweight pathname router**

In `App.tsx`, keep the existing wizard as `FolderSetupApp`, use `window.location.pathname`, `history.pushState`, and a `popstate` listener. `ProductNav` renders semantic links/buttons for `/` and `/rag`; do not add React Router.

- [ ] **Step 4: Implement request and connection controls**

`RequestPanel` owns only draft fields. Normalize comma/newline-separated teams and weeks, validate `YYYY-WW`, and emit:

```ts
interface RagRequestDraft {
  userId: string
  mode: 'fast' | 'deep'
  conversationId?: string
  question: string
  filters: RetrievalFilters
}
```

`ConnectionStatus` invokes health/readiness on mount and manual refresh, shows `ready`, `degraded`, `offline`, latency, and safe dependency names only.

- [ ] **Step 5: Verify and commit**

Run:

```bash
cd frontend
npm test -- src/App.test.tsx src/rag/__tests__/RequestPanel.test.tsx
npm run build
```

Expected: tests and build PASS; existing wizard tests remain green.

Commit:

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/components/ProductNav.tsx frontend/src/rag/RequestPanel.tsx frontend/src/rag/ConnectionStatus.tsx frontend/src/rag/__tests__/RequestPanel.test.tsx
git commit -m "feat(ui): add rag request workspace"
```

---

### Task 3: Fast/Deep orchestration, diagnostics, and job controls

**Files:**
- Create: `frontend/src/rag/RagLabApp.tsx`
- Create: `frontend/src/rag/ConversationPanel.tsx`
- Create: `frontend/src/rag/InspectorPanel.tsx`
- Create: `frontend/src/rag/__tests__/RagLabApp.test.tsx`
- Create: `frontend/src/rag/__tests__/InspectorPanel.test.tsx`

**Interfaces:**
- Consumes: `RagApiService`, `ApiExchange`, `ChatResponse`, `ResearchJobResponse`, `ResearchEvent`, `RagRequestDraft`
- Produces: complete `/rag` interaction, including active `AbortController` lifecycle

- [ ] **Step 1: Write failing Fast and diagnostics tests**

Use a fake `RagApiService`. Verify:

```ts
expect(screen.getByText('hybrid')).toBeInTheDocument()
expect(screen.getByText('인용 유효')).toBeInTheDocument()
expect(screen.getByText('S1')).toBeInTheDocument()
expect(screen.getByText('trace-1')).toBeInTheDocument()
expect(screen.getByText('200')).toBeInTheDocument()
```

Add exact BM25 disclosure assertion, limited answer status, reference details, request/response JSON tabs, JSON copy action, and safe error/retryable display. Assert `JSON.stringify(exchange)` contains no headers/cookies.

- [ ] **Step 2: Write failing Deep workflow tests**

Fake a `202` chat response with `job_id`, emit queued/running events, and return completed status. Assert progress changes, completed report/references appear, cancel is shown only for queued/running, retry only for failed/cancelled, and a new request aborts the old stream.

- [ ] **Step 3: Run and confirm RED**

Run:

```bash
cd frontend
npm test -- src/rag/__tests__/RagLabApp.test.tsx src/rag/__tests__/InspectorPanel.test.tsx
```

Expected: FAIL because orchestration and panels do not exist.

- [ ] **Step 4: Implement `RagLabApp` orchestration**

On submit, build an exact `ChatPayload` and call `sendChat`. For Fast, append the response and select its exchange. For Deep:

1. Save the returned job ID and queued state.
2. Start POST SSE with an `AbortController`.
3. On each event append `{ receivedAt, status, progress }` and call owner-scoped status when progress changes or completion is signaled.
4. If streaming fails once, show `상태 조회로 전환됨` and poll status every two seconds.
5. Abort the stream and polling on unmount, new job, cancel, or terminal status.

Do not infer budget usage. Expose static contract limits and label missing counters `서버 미제공`.

- [ ] **Step 5: Implement result and inspector panels**

`ConversationPanel` renders safe text, exact disclosures, references, progress, cancel, and retry actions. Do not use `dangerouslySetInnerHTML`; render report lines and lists as text nodes.

`InspectorPanel` provides:

- Summary: status, duration, mode, retrieval mode, trace/conversation/job IDs, quality, reference count, error/retryable.
- JSON: stable `JSON.stringify(value, null, 2)` inside `<pre>`, request and response separated, copy buttons.
- Events: received time, job status, progress, connection notes.

- [ ] **Step 6: Verify and commit**

Run:

```bash
cd frontend
npm test -- src/rag/__tests__
npm run build
```

Expected: Fast, Deep, diagnostics, and streaming tests PASS; build exits 0.

Commit:

```bash
git add frontend/src/rag/RagLabApp.tsx frontend/src/rag/ConversationPanel.tsx frontend/src/rag/InspectorPanel.tsx frontend/src/rag/__tests__
git commit -m "feat(ui): add rag diagnostics console"
```

---

### Task 4: Responsive visual system, documentation, and end-to-end verification

**Files:**
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/index.html`
- Modify: `docs/operations.md`
- Modify: `docs/api.md`
- Test: all `frontend/src/**/*.test.ts?(x)`

**Interfaces:**
- Consumes: all Task 1–3 components
- Produces: production build and documented local test workflow

- [ ] **Step 1: Add semantic console styles**

Extend the existing token system with `--signal-blue`, `--verified`, `--fallback`, and `--code-surface`. Implement:

- 3-column desktop grid: request 240px, conversation minmax(360px, 1fr), inspector 340px
- inspector below content under 1000px
- mobile product navigation and tabbed request/conversation/inspector views under 680px
- visible keyboard focus and minimum 40px interactive targets
- verification strip for owner/citation/retrieval/error state
- `@media (prefers-reduced-motion: reduce)` disabling transitions and animations

- [ ] **Step 2: Update product copy and bootstrapping**

Set the page title to `Weekly Mail · RAG Verification Console`. Keep the existing app bootstrap and inject a default `RagApiService('/api')` only at the composition root so tests can pass a fake service.

- [ ] **Step 3: Document the local run flow**

Add exact commands:

```bash
# terminal 1
python - <<'PY'
import uvicorn
from app.api.dependencies import build_container
from app.api.main import create_app
uvicorn.run(create_app(build_container()), host='127.0.0.1', port=8000)
PY

# terminal 2
cd frontend
npm install
npm run dev
```

Document `RAG_API_TARGET` for another backend host, `/rag`, required upstream identity caveat, and that this is a development verification console—not an authentication boundary.

- [ ] **Step 4: Run full verification**

Run:

```bash
cd frontend
npm test
npm run lint
npm run build
cd ..
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q
git diff --check
```

Expected:

- all frontend tests PASS
- ESLint exits 0
- TypeScript/Vite production build exits 0
- all Python tests PASS
- diff check exits 0

- [ ] **Step 5: Perform visual QA**

Start FastAPI and Vite, open `/rag`, and inspect desktop (1440px), tablet (900px), and mobile (390px). Verify no horizontal clipping, readable JSON, keyboard focus, reduced motion, Fast response, Deep queued/running/completed controls, and safe error layouts.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/styles.css frontend/src/main.tsx frontend/index.html docs/operations.md docs/api.md
git commit -m "feat(ui): finish rag verification console"
```
