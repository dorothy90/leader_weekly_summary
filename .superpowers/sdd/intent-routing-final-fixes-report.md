# Intent routing final fixes report

Date: 2026-08-02 (Asia/Seoul)
Base: `ee1cd88`

## Implemented

1. Normalized model-produced `general` and `clarify` decisions to
   `estimated_searches=0`. The clarification API response continues to report
   `quality.retrieval_mode=not_used`.
2. Replaced raw Deep keyword overrides with bounded Korean and English intent
   patterns. Artifact and research nouns must now occur in direct creation or
   analysis requests. Concept and product questions remain General.
3. Added bounded English mail-object and retrieval-action signals for the
   deterministic router-error fallback. Direct mail searches and summaries use
   Fast, while unrelated conversation and meta-questions remain General.
4. Preserved the four-week and three-team Deep hard constraints, explicit
   Fast/Deep handling, server-owned reason provenance, and valid structured
   model-route authority outside those Deep constraints.

## TDD evidence

### RED 1: requested behavior

Command:

```bash
PYTHONPATH=. pytest -q tests/mail_rag/test_router.py tests/mail_rag/test_chat_api.py
```

Result: exit 1; `9 failed, 55 passed in 0.75s`.

The expected failures covered clarify search-count normalization, the three
English mail fallback requests, English artifact creation, the two Korean
concept false positives, and clarify API diagnostics. An earlier plain `pytest`
attempt failed during collection because the runner did not put the repository
on `PYTHONPATH`; it was corrected before recording behavioral RED.

### GREEN 1: requested behavior

Command:

```bash
PYTHONPATH=. pytest -q tests/mail_rag/test_router.py tests/mail_rag/test_chat_api.py
```

Result after the minimal routing changes: exit 0; `64 passed in 0.57s`.

### RED 2: self-review boundary hardening

Command:

```bash
PYTHONPATH=. pytest -q tests/mail_rag/test_router.py \
  -k 'keeps_output_and_analysis_concepts_general'
```

Result: exit 1; `2 failed, 6 passed, 35 deselected in 0.38s`.

The failures showed that `explain how to create a report` incorrectly selected
Deep and `explain how to search mail` incorrectly selected Fast.

### Focused GREEN

Command:

```bash
PYTHONPATH=. pytest -q tests/mail_rag/test_router.py tests/mail_rag/test_chat_api.py
```

Result: exit 0; `69 passed in 0.66s`.

## Full verification

The complete suite loaded the existing external environment with
`python-dotenv` into the child process. Values were neither printed nor
evaluated as shell code, and the external file was not modified.

Command:

```bash
python -m dotenv \
  -f /Users/daehwankim/Documents/weekly_mail_agent/.env \
  run --override -- \
  zsh -c 'PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python -m pytest tests/mail_rag -q'
```

Result: exit 0; `272 passed in 6.23s`.

Additional gates:

- `python -m compileall -q app`: exit 0.
- `python -m black --check app/graphs/router.py tests/mail_rag/test_router.py tests/mail_rag/test_chat_api.py`: exit 0; three files unchanged.
- `git diff --check`: exit 0; no output.

## Files changed

- `app/graphs/router.py`
- `tests/mail_rag/test_router.py`
- `tests/mail_rag/test_chat_api.py`
- `.superpowers/sdd/intent-routing-final-fixes-report.md`

The pre-existing untracked `docs/deep_agent_mail_chatbot_review.md` was not
opened, changed, staged, or added.

## Self-review

- English tokens use word boundaries and direct/polite request anchoring;
  embedded meta-questions do not trigger Fast or Deep.
- Korean output patterns require an artifact plus a terminal creation action;
  Korean analysis patterns require an analysis request verb. The required
  concept questions therefore remain General.
- Valid structured model decisions remain authoritative unless a four-week,
  three-team, or bounded research-output Deep constraint applies.
- No ACL, `user_id`, citation, BM25 disclosure, Fast/Deep execution, frontend,
  or API mapping production code was changed.
- No unresolved functional concern was found in the final diff.
