# RAG Answer Structure Design

## Goal

Make every Fast RAG and Deep Research answer use the same three-section Markdown structure without adding another LLM call:

```markdown
### 요약
...

### 상세설명
...

### 핵심결론
...
```

## Scope

- Apply the structure to successful Fast RAG answers and Deep Research reports.
- Apply the same structure to limited, no-evidence, invalid-citation, and timeout answers returned by those RAG workflows.
- Keep factual claims tied to the existing `[S#]` citations.
- Leave General, Clarification, Diagnostic, and Corpus Info responses unchanged.
- Implement this together with the separately approved removal of the additional LLM claim-support validation.

## Approach

Use the existing answer-generation and citation-revision calls. Update their instructions to require the exact three headings in the specified order. Represent deterministic fallback messages with the same headings.

Add a deterministic formatter that validates the heading count and order. If a model response does not satisfy the contract, preserve its complete text under `상세설명` and add non-factual summary and conclusion text. This guarantees the public structure without another model request or loss of cited content. Do not add structured JSON generation, a post-processing model call, or a new formatting dependency.

The API continues returning Markdown in the existing `answer` field, so no request or response schema changes are required.

## Data Flow

Fast RAG:

```text
retrieve -> grade/rewrite -> generate three-section answer
-> citation validate/revise -> return
```

Deep Research:

```text
plan -> research/gap loop -> synthesize three-section report
-> citation validate/revise -> return
```

## Error Handling

Fallback answers preserve the three headings. The reason for a limited answer appears in `요약`, available evidence or missing scope appears in `상세설명`, and the actionable conclusion appears in `핵심결론`. A malformed model response is wrapped deterministically with its original text intact. Existing disclosure text, including BM25 fallback disclosure, remains outside the generated sections as response metadata and may still be appended to the answer by the existing API behavior.

Invalid citations continue to block unsupported references through `CitationValidator`. Removing the separate claim-support LLM call does not alter citation syntax, evidence existence, uniqueness, or owner checks.

## Testing

- Verify Fast RAG successful answers contain all three headings in the required order.
- Verify Deep Research successful reports contain all three headings in the required order.
- Verify limited and no-evidence paths preserve the same structure.
- Verify a malformed model answer is wrapped once without losing its text or citations.
- Verify invalid citations are still rejected or revised by the existing citation gate.
- Verify the removed claim-support LLM call is not made.
- Run focused workflow tests followed by the complete backend test suite.

## Success Criteria

- All Fast and Deep RAG answer bodies contain exactly one `요약`, `상세설명`, and `핵심결론` heading in that order.
- No additional LLM request is introduced.
- Existing API contracts and citation authorization behavior remain unchanged.
- Relevant tests pass.
