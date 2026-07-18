# Backend Task 5A Report

## Status

Implemented the one-week classification orchestration and the minimal explicit
rerun lifecycle/history support. Task 5B run comparison and CLI work were not
implemented.

## RED

Added four orchestration tests before production changes:

1. A successful one-mail run completes and persists its classification trace.
2. An injected splitter failure marks the new week/run failed, records stage,
   mail directory, exception type, and message as JSON, and preserves the prior
   completed run and trace.
3. An ordinary run cannot open a `revalidation_required` week.
4. `rerun=True` opens a `revalidation_required` week, links `prior_run_id`,
   clears approval provenance after inserting the new run, and retains both
   runs' traces.

Command:

```text
python -m pytest tests/test_classification_workbench.py -q
```

Observed expected pre-implementation failure during collection:

```text
ImportError: cannot import name 'run_week_classification' from
'classification_workbench'
```

This was the intended missing-interface failure; no production implementation
existed at that point.

## GREEN

Implemented:

- `run_week_classification(week, store, mail_directories, splitter, rerun=False)`;
- mail loading through `process_agendas.mail_from_directory`;
- extraction with an injected splitter and a resolver built from the store's
  current taxonomy and active aliases;
- per-mail persistence through `save_classified_extraction`, followed by run
  completion;
- structured failure recording through `fail_classification_run`;
- an explicit store `rerun` gate for `revalidation_required` weeks;
- per-run trace retention using `(agenda_id, run_id)` as the trace primary key,
  including migration of the previous single-key table;
- active-run scoping for correction/disposition/split trace updates so a rerun
  cannot rewrite historical traces.

Focused verification:

```text
python -m pytest tests/test_process_agendas.py tests/test_classification_workbench.py -q
54 passed, 7 warnings in 3.01s
```

Full verification:

```text
python -m pytest -q
108 passed, 8 warnings in 4.95s
```

The warnings are existing dependency deprecation warnings from Pydantic v1
compatibility and Starlette's multipart import.

## Scope and concerns

- Modified implementation/test files: `classification_workbench.py`,
  `knowledge_store.py`, and `tests/test_classification_workbench.py`.
- No Wiki, embedding, report generation, OpenSearch call, external splitter
  construction, run comparison, or CLI behavior was added.
- `classification_run.error` remains a text column for compatibility; Task 5A
  failures store a JSON object in that text field.
- Historical traces retain classification decisions per run. The canonical
  agenda/mail rows remain shared by stable agenda IDs, matching the existing
  store model and the planned Task 5B comparison by stable ID.
