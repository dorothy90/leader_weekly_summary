# Topic Wiki operations

The Topic Wiki consumes an approved classification week and writes JSON state under
`wiki_data/`. The existing classification and report flows remain independent of
the Wiki build.

## Build an approved week

Set the OpenAI-compatible connection variables required by the deployment,
including its base URL and API key. External transmission requires an explicit data
policy acknowledgement. `KNOWLEDGE_LLM_DATA_POLICY_ACK=true` records a
deployment-level acknowledgement; `--allow-external-llm` also sets that value only
for the individual command and restores its previous value afterward.

```bash
export KNOWLEDGE_LLM_MODEL='z-ai/glm-5.2'
export KNOWLEDGE_LLM_DATA_POLICY_ACK='true'
python process_wiki.py --week 2026-W30 --allow-external-llm
```

The command prints the build-run JSON. A `published` result is complete;
`review_required` requires resolving pending assignment reviews before rerunning;
`partially_failed` lists the Topic IDs that failed while retaining their previous
valid revisions. The CLI exits successfully for `published` and `review_required`;
other statuses produce a nonzero exit. Do not enable either acknowledgement until
the selected provider is approved to receive the source data.

## Serve and verify browser routes

Build the Web application into `web/dist`, then run the preview API:

```bash
python -m uvicorn knowledge_preview:app --host 127.0.0.1 --port 8000
```

Open `/classification` for the existing workbench or `/wiki` for the Topic Wiki.
Direct browser loads and reloads are supported for Topic, LOTCD, team, and week
paths such as:

- `/wiki/topics/T-001`
- `/wiki/lotcd/DRAM/Spica/4SA`
- `/wiki/teams/Yield`
- `/wiki/weeks/2026-W30`

These paths serve the SPA entry point. API routes remain under `/api/knowledge`,
and existing report routes are unchanged.

## Recover an interrupted build

Topic revisions are written before their current Topic pointer is replaced, so a
failed draft or pointer update leaves the previous valid revision current. To mark
builds left in a transient state for more than one hour as interrupted, run:

```bash
python - <<'PY'
from wiki_store import JsonWikiStore

for run in JsonWikiStore().recover_incomplete_builds():
    print(run.run_id, run.status, run.error)
PY
```

Review the printed run IDs and the corresponding files in `wiki_data/builds/`.
The recovery operation changes only stale transient build records to `failed`; it
does not replace current Topic revisions. Remove a stale `wiki_data/.build.lock`
only after confirming that no `process_wiki.py` process is running, then rerun the
week. Identical successful input is reused; corrected or changed input creates a
new build.

## Backend verification

Run the focused Topic Wiki recovery, storage, projection, and API checks:

```bash
pytest tests/test_topic_wiki_models.py tests/test_wiki_store.py tests/test_topic_linker.py tests/test_topic_wiki_builder.py tests/test_wiki_projections.py tests/test_knowledge_api.py -q
```
