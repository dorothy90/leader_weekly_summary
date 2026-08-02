# Multi-turn Chat Composer Design

## Goal

Make the RAG test console behave like a normal chat UI for message entry and
multi-turn verification without removing its test controls or diagnostics.

## Scope

- Keep `user_id`, Auto/Fast/Deep mode, conversation ID, and retrieval filters
  in the left request settings panel.
- Remove the question textarea and run button from the left panel.
- Add a sticky message composer to the bottom of the center conversation panel.
- Preserve and render every submitted user turn and corresponding API result
  during the current browser session.
- Automatically send the server-provided `conversation_id` on later turns for
  the same owner.
- Keep the right API inspector and Deep job controls unchanged.

## Component Boundaries

### Request settings

`RequestPanel` owns only request configuration and validation for owner,
routing mode, optional conversation ID, and filters. It emits the current
settings to the parent when they change; it no longer submits a chat message.

### Conversation workspace

`ConversationPanel` renders an ordered list of turns. Each turn retains the
submitted question and its associated Fast, General, Clarify, Deep, or error
state so a later request does not erase earlier evidence. Its footer contains
the chat composer and Deep cancel/retry actions when applicable.

### Orchestration

`RagLabApp` combines the current settings with the composer message, calls the
existing API client, appends results to the active turn, and carries forward
the returned conversation ID. Changing `user_id` clears the carried server
conversation ID to prevent cross-owner continuation.

## Data Flow

1. Tester configures owner, routing mode, and optional filters on the left.
2. Tester enters a message in the center-bottom composer and sends it.
3. The user message appears immediately as a pending turn.
4. The API response updates that same turn and the latest exchange appears in
   the inspector.
5. The returned conversation ID becomes the default for the next message.
6. Subsequent turns append below prior turns and reuse that conversation ID.

Only one request or Deep job is actively tracked at a time. Starting a new
request stops prior active job tracking, matching current behavior, while
preserving the prior turn in the visible transcript.

## Interaction Details

- `Enter` sends; `Shift+Enter` inserts a newline.
- Empty messages cannot be sent.
- The composer is disabled while the current request is being accepted.
- After a successful send, the composer clears and focus returns to it.
- The conversation scrolls to the newest turn.
- On narrow screens, settings, chat, and inspector remain switchable; the
  composer belongs to the chat/result view.

## Error Handling

- Settings validation remains next to the relevant left-panel field.
- Message validation appears next to the composer.
- Safe API errors render inside the corresponding turn and remain available in
  the inspector.
- Existing retryability labels, BM25 fallback disclosure, routing diagnostics,
  references, and Deep status controls remain visible.

## Testing

- Component tests verify the left panel contains no question control.
- Composer tests verify Enter, Shift+Enter, empty-message validation, disabled
  behavior, and submission using the current settings.
- Multi-turn tests verify two question/answer pairs remain visible and the
  second payload reuses the first response's conversation ID.
- Owner-change tests verify the carried conversation ID is cleared.
- Existing Fast, General, Clarify, Deep, error, inspector, and mobile tests are
  adapted without weakening their assertions.
- Frontend test, lint, and production build gates must pass; the running test UI
  is then checked in the browser at `/rag`.

## Non-goals

- No backend or API contract changes.
- No persisted transcript across reloads.
- No simultaneous Deep jobs.
- No redesign of the settings or inspector panels.
