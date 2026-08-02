# Multi-turn Chat Composer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move only the question input into a standard bottom chat composer and preserve visible multi-turn history.

**Architecture:** `RequestPanel` emits validated request settings, `ConversationPanel` owns the visible composer and renders immutable turn records, and `RagLabApp` combines both for each API call. The latest exchange still drives the existing Inspector while every completed or failed turn stays in the center transcript.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, CSS, Vite

## Global Constraints

- No backend or API contract changes.
- Keep left request settings and right Inspector behavior.
- Reuse the server conversation ID only for the same owner.
- Preserve Fast/General/Clarify/Deep diagnostics, references, disclosures, and errors.
- Do not persist sensitive request or transcript data to browser storage.

---

### Task 1: Split settings from message submission

**Files:**
- Modify: `frontend/src/rag/types.ts`
- Modify: `frontend/src/rag/RequestPanel.tsx`
- Modify: `frontend/src/rag/__tests__/RequestPanel.test.tsx`

**Interfaces:**
- Produces: `RagRequestSettings` with `userId`, `mode`, optional `conversationId`, and `filters`.
- Produces: `RequestPanelProps.onSettingsChange(settings?: RagRequestSettings)`.
- Removes: question state, question validation, textarea, and run button from `RequestPanel`.

- [ ] **Step 1: Write failing panel tests**

Assert that the panel has no `질문` textbox or `실행` button, changing valid
settings emits a normalized `RagRequestSettings`, invalid `YYYY-WW` input emits
no usable settings, and owner changes clear the carried conversation ID.

- [ ] **Step 2: Verify RED**

Run: `npm test -- src/rag/__tests__/RequestPanel.test.tsx`

Expected: failures because the question controls still exist and
`onSettingsChange` is not defined.

- [ ] **Step 3: Implement the settings interface**

Add this focused type and update the panel contract:

```ts
export interface RagRequestSettings {
  userId: string
  mode: ExecutionMode
  conversationId?: string
  filters: RetrievalFilters
}
```

Validate owner and week format inside `RequestPanel`, call
`onSettingsChange(undefined)` while invalid, and emit normalized settings from
an effect when valid. Keep the current owner/authorization help text.

- [ ] **Step 4: Verify GREEN**

Run: `npm test -- src/rag/__tests__/RequestPanel.test.tsx`

Expected: all RequestPanel tests pass.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/rag/types.ts frontend/src/rag/RequestPanel.tsx frontend/src/rag/__tests__/RequestPanel.test.tsx
git commit -m "refactor(ui): separate RAG request settings"
```

### Task 2: Add composer and immutable multi-turn transcript

**Files:**
- Modify: `frontend/src/rag/types.ts`
- Modify: `frontend/src/rag/RagLabApp.tsx`
- Modify: `frontend/src/rag/ConversationPanel.tsx`
- Modify: `frontend/src/rag/__tests__/RagLabApp.test.tsx`

**Interfaces:**
- Consumes: `RagRequestSettings` from Task 1.
- Produces: `ConversationTurn` records containing a stable ID, question,
  optional chat/job/error, and pending state.
- Produces: `ConversationPanelProps.onSend(message: string)`.

- [ ] **Step 1: Write failing multi-turn and composer tests**

Cover these observable behaviors:

```ts
expect(screen.getByRole('textbox', { name: '메시지' })).toBeInTheDocument()
expect(screen.getByRole('button', { name: '전송' })).toBeInTheDocument()
expect(screen.getByText('첫 질문')).toBeInTheDocument()
expect(screen.getByText('첫 답변')).toBeInTheDocument()
expect(screen.getByText('후속 질문')).toBeInTheDocument()
expect(screen.getByText('후속 답변')).toBeInTheDocument()
expect(service.sendChat).toHaveBeenLastCalledWith(
  expect.objectContaining({ conversation_id: 'conversation-followup' }),
)
```

Also verify Enter submits, Shift+Enter does not submit, empty text is rejected,
the composer clears after send, and a changed owner does not reuse the prior
conversation ID.

- [ ] **Step 2: Verify RED**

Run: `npm test -- src/rag/__tests__/RagLabApp.test.tsx`

Expected: failures because no center composer or persistent turn list exists.

- [ ] **Step 3: Implement turn state and composer submission**

Add this state shape:

```ts
export interface ConversationTurn {
  id: number
  question: string
  chat?: ChatResponse
  job?: ResearchJobResponse
  error?: SafeApiError
  pending: boolean
}
```

In `RagLabApp`, store `settings` and `turns`; append a pending turn before
calling `sendChat`, then update only that turn by ID. Carry the response
conversation ID forward and clear it when the settings owner changes. Keep the
latest API exchange/job exchange/events for Inspector diagnostics.

In `ConversationPanel`, map every turn in order and retain the existing routing,
quality, reference, disclosure, Deep progress, error, cancel, and retry views.
Add a controlled textarea footer labeled `메시지`, a `전송` button, Enter send,
Shift+Enter newline, disabled state, validation, focus restoration, and
scroll-to-latest behavior.

- [ ] **Step 4: Verify GREEN and existing routing coverage**

Run: `npm test -- src/rag/__tests__/RagLabApp.test.tsx`

Expected: all RagLabApp tests pass, including Fast, General, Clarify, Deep,
error, mobile, and multi-turn cases.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/rag/types.ts frontend/src/rag/RagLabApp.tsx frontend/src/rag/ConversationPanel.tsx frontend/src/rag/__tests__/RagLabApp.test.tsx
git commit -m "feat(ui): add multi-turn chat composer"
```

### Task 3: Style and verify the chat workspace

**Files:**
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: composer and transcript class names from Task 2.
- Produces: sticky bottom composer on desktop and mobile without moving either
  side panel.

- [ ] **Step 1: Add layout assertions where behavior is semantic**

Assert the composer remains inside the `대화 및 결과` region and the request
settings remain in their form. Avoid testing raw CSS declarations.

- [ ] **Step 2: Verify RED if a semantic boundary is missing**

Run: `npm test -- src/rag/__tests__/RagLabApp.test.tsx`

Expected: any new semantic assertion fails before the matching markup change;
otherwise proceed because visual CSS is verified through build and browser QA.

- [ ] **Step 3: Implement minimal visual changes**

Keep the three-column shell. Make the conversation scroll area a stable
transcript and add a bordered, sticky composer footer with a flexible textarea
and compact send button. Preserve visible keyboard focus, mobile result-view
placement, and reduced-motion compatibility.

- [ ] **Step 4: Run complete frontend verification**

Run: `npm test && npm run lint && npm run build`

Expected: all tests pass, ESLint exits 0, and Vite production build exits 0.

- [ ] **Step 5: Browser QA**

At `http://127.0.0.1:5180/rag`, send two messages with one owner and verify both
question/answer pairs remain visible, the second request reuses the displayed
conversation ID, and Inspector shows the latest exchange.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/styles.css frontend/src/rag/__tests__/RagLabApp.test.tsx
git commit -m "style(ui): place composer below conversation"
```
