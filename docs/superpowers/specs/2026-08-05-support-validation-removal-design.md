# Claim-Support Validation Removal Design

## Goal

Remove the additional LLM-based claim-support validation from both Fast RAG and Deep Research so it no longer adds latency or rejects an otherwise citation-valid answer.

## Scope

- Remove `ClaimSupportDecision` and its LLM calls from `app/graphs/fast_rag.py` and `app/graphs/deep_research.py`.
- Preserve deterministic `[S#]` citation syntax, evidence existence, uniqueness, and owner validation through `CitationValidator`.
- Preserve evidence grading, query rewrite limits, answer/report citation revision, limited-answer behavior, and public reference filtering.
- Do not add a feature flag, warning mode, replacement verifier, or unrelated refactor.

## Data Flow

Fast RAG will finish as:

```text
contextualize -> plan -> retrieve -> grade/rewrite -> generate
-> citation validate/revise -> execution result
```

Deep Research will finish as:

```text
plan -> parallel research -> gap analysis -> synthesize/revise citations
-> execution result
```

An answer or report with valid owned citations will no longer make a second LLM call for semantic entailment. Invalid, unknown, duplicate, or unauthorized citations will still fail the existing citation gate.

## Error Handling

Removing the support call eliminates `UNSUPPORTED_ANSWER` and support-check parse/transport failures from these workflows. Existing retrieval, generation, citation-validation, and timeout errors remain unchanged. The `support_validation` execution-stage literal may remain for backward compatibility with persisted historical records, but new runs will not emit it.

## Testing

- Add a Fast RAG regression test proving a citation-valid answer returns successfully without a support-validation LLM call.
- Add a Deep Research regression test proving a citation-valid report returns successfully without a support-validation LLM call.
- Retain and run existing citation-validation tests to confirm invalid citations are still blocked.
- Run the focused backend tests, then the full backend test suite.

## Success Criteria

- No production code invokes `ClaimSupportDecision` or a claim-support prompt.
- Fast and Deep each avoid one LLM round trip after citation validation.
- Citation and owner validation behavior remains intact.
- Relevant tests pass.
