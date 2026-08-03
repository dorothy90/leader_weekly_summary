# Single-Source Conversation Memory Design

## Goal

Make ordinary follow-up questions retain their subject without coupling chat
history to retrieval status, citation status, or execution diagnostics. A safe
answer shown to the user must remain referable on the next turn even when the
underlying mail search was limited.

## Problem

The current conversation model stores the same exchange in both `messages` and
`turns`. Saving uses `execution.include_in_llm_history`, while loading from
`turns` additionally requires `execution.status == "succeeded"`. A successful
general fallback can therefore be written to `messages` and then discarded
because its original retrieval execution remains `limited / NO_EVIDENCE`.

Fast RAG also derives a standalone follow-up question for retrieval and grading
but sends the original, context-dependent message to final generation. A turn
such as `그중 제일 가까운 데는?` can consequently lose its subject after
retrieval has already resolved it.

## Design

### Canonical conversation history

`ConversationMemory.messages` is the only source used to build LLM chat
history. `ConversationMemory.turns` remains as a backward-compatible execution
and audit record, but it never decides which conversation messages exist.

Every accepted user message is appended to `messages`, including requests whose
execution later fails. A sanitized assistant message is appended whenever the
API returns a non-null public answer. This includes safe limited answers and the
labelled no-evidence general fallback. Failed executions with `answer=null`
append only the user message.

The `include_in_llm_history` field remains in the API and persisted turn
envelope for compatibility and diagnostics during this change. It no longer
overrides the canonical public message ledger. A later migration may remove the
duplicate turn content and the compatibility field separately.

### One resolved query per RAG turn

The Fast contextualization step produces one `standalone_question`. Planning,
retrieval, grading, rewriting, and final answer generation all use that same
resolved question. The original user message remains in the message ledger for
natural conversation display and auditability.

If model-based contextualization fails, the workflow deterministically combines
the most recent prior user message with the current follow-up instead of silently
falling back to the context-free current text. This fallback is bounded by the
existing message and request limits.

Deep contextualization uses the same conversation-history helper and fallback
behavior.

### Retrieval filters

Retrieval filters remain request-scoped in this change. The web client already
sends the current filters on every turn. Persisted `memory.filters` remains for
schema compatibility and diagnostics, but is not implicitly inherited because
an empty filter currently cannot distinguish "clear the scope" from "inherit
the previous scope".

### Security and ownership

Conversation lookup continues to require the exact `conversation_id` and owner
policy. All message content is sanitized before persistence and again before
model input. Citation evidence remains owner-scoped and reusable only from
successful verified turns; changing the message-history source does not relax
evidence eligibility.

### Compatibility

Existing Mongo documents containing only `messages` remain readable. Documents
containing both `messages` and `turns` use `messages` as their canonical history.
No database migration or API response change is required. `turns` continues to
power execution diagnostics.

## Verification

Regression tests must demonstrate:

1. A persisted `limited / NO_EVIDENCE` fallback with a public answer is present
   in the next turn's model history.
2. Failed turns retain the user's subject while excluding a null assistant
   answer.
3. A Fast follow-up uses the standalone question for final generation as well as
   retrieval and grading.
4. Contextualization failure produces a bounded deterministic contextual query.
5. Existing successful-history filtering, owner-scoped evidence reuse,
   diagnostics, API contracts, frontend tests, lint, and production build remain
   green.

## Non-goals

- Removing the `turns`, `filters`, or `include_in_llm_history` compatibility
  fields in this patch.
- Adding long-term semantic memory or cross-conversation user memory.
- Persisting the browser transcript across page reloads.
- Changing retrieval, citation-validation, or owner-authorization policy.
