# Node Run Diagnostics Design

## Goal

Expose a safe, request-correlated timeline for every Router, Fast RAG, Deep Research, embedding, and OpenSearch step in the test API response and UI.

## API contract

`ExecutionMetadata.node_runs` is a bounded list of node executions. Each item contains only:

- stable sequence number and allowlisted node name;
- `ok`, `error`, or `cancelled` status;
- start offset and duration in milliseconds;
- safe input/output counts for history, tasks, searches, candidates, and evidence;
- retrieval mode, BM25 fallback flag, attempt number, and exception class.

Raw questions, mail content, prompts, document identifiers, credentials, exception messages, and chain-of-thought are forbidden.

## Collection

A request-scoped `ContextVar` recorder is created by Fast or Deep invocation when one does not already exist. Router, graph nodes, embedding, and OpenSearch boundaries append records to the same recorder, including parallel Deep branches. Execution results receive a snapshot ordered by start sequence.

## UI

The inspector adds a Nodes tab with a compact ordered timeline. Each row shows status, node name, start offset, duration, counts, retrieval mode, fallback, and error class. Raw JSON remains available in the existing JSON tab.

## Verification

- Domain tests reject raw/unknown diagnostic fields.
- Fast, Deep, Router, and retrieval tests verify successful and failing node records.
- UI tests verify timeline rendering and tab keyboard behavior.
- Full backend and frontend suites must pass.
