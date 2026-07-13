# Integrated Wiki Semantic Retry Design

## Goal

Generate every Domain, Tech, and LOTCD canonical Wiki page even when the LLM's
first structured response is schema-valid but fails evidence or issue-state
validation. Invalid pages must never overwrite the previous canonical page.

## Scope

- Keep `z-ai/glm-4.7` and the existing OpenRouter-compatible generator.
- Retry only semantic validation failures produced by the integrated Wiki
  pipeline.
- Retry malformed structured-output parser responses once before treating the
  page as failed.
- Allow at most two corrective retries per analysis or narrative draft.
- Derive ongoing, resolved, and reopened issue decisions from validated
  timelines and child digests rather than asking the LLM to restate them.
- Allow OpenRouter reasoning effort to be set through
  `KNOWLEDGE_LLM_REASONING_EFFORT`.
- Skip LLM calls for leaf pages with no direct, historical, or child evidence;
  render the canonical six-section shell with low confidence instead.
- Preserve the existing taxonomy, OpenSearch indexes, page schema, citations,
  issue continuity rules, and parent-after-child generation order.

## Design

The builder will separate one page generation attempt into two validated
stages:

1. Analysis generation and validation
   - Generate `PageAnalysis` from the existing context.
   - Run evidence and issue-decision validation.
   - On failure, append the exact validation error plus the original context to
     a corrective prompt and regenerate the analysis.
   - Stop after the initial attempt plus two corrective retries.

2. Narrative generation and validation
   - Generate `NarrativeDraft` from the validated analysis.
   - Validate factual units, mail citations, and citation scope.
   - On failure, append the exact validation error plus the validated analysis
     and regenerate the draft.
   - Stop after the initial attempt plus two corrective retries.

The retry controller belongs in `integrated_wiki_builder.py`. Issue status and
its latest evidence are deterministic because the existing state normalization
already defines them. The LLM remains responsible for supported claims,
contradictions, outline, and narrative prose. It must correct invalid claims or
citations, while existing deterministic validators remain the authority.

## Failure behavior

- A page that still fails after two retries is recorded in `BuildResult.failures`.
- A failed child prevents its Tech or Domain parent from being regenerated.
- Failed pages are not passed to `save_integrated_pages`, so their existing
  canonical documents remain unchanged.
- API keys and model credentials remain process environment only.

## Verification

- Unit test: analysis omits issue decisions, deterministic timeline and child
  evidence supplies them, and page generation continues without an LLM retry.
- Unit test: reopened evidence propagates from LOTCD to Tech and Domain.
- Unit test: optional reasoning effort becomes the OpenRouter `reasoning` body.
- Unit test: an evidence-free leaf does not call the LLM generator.
- Unit test: malformed tool arguments receive one structured-output retry.
- Unit test: first draft contains an uncited factual unit, corrective retry adds
  a valid `[mail:...]` citation.
- Unit test: three invalid responses produce one page failure and no saved page.
- Regression: existing integrated builder and API/Web tests remain green.
- Live regeneration: all 23 pages succeed for `2026-W28`.
- API check: DRAM has non-empty `current_body_markdown`, six narrative sections,
  `weekly_history`, and resolvable inline mail citations.
