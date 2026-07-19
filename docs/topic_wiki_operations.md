# Topic Wiki operations

The Topic Wiki consumes an approved classification week and writes JSON state under
`wiki_data/`. The existing classification and report flows remain independent of
the Wiki build. Automatic Topic Wiki building is disabled by default; the weekly
pipeline calls it only when `ENABLE_TOPIC_WIKI=true`.

## Authoritative data layout

`wiki_data/` is the canonical JSON store. Its implemented paths are:

```text
wiki_data/
├── catalog.json
├── topics/
│   └── <topic_id>.json
├── assignments/
│   └── <agenda_id>.json
├── relations/
│   └── <relation_id>.json
├── reviews/
│   └── <review_id>.json
├── builds/
│   └── <run_id>.json
├── weeks/
│   └── <week>.json
└── history/
    ├── topics/
    │   └── <topic_id>/
    │       └── <revision_id>.json
    └── weeks/
        └── <week>/
            └── <revision_id>.json
```

`topics/<topic_id>.json` is the current Topic record and points to its current
revision. Topic revision history is immutable under `history/topics/`.
`weeks/<week>.json` is the current Week projection; replaced Week projections move
to `history/weeks/`. Assignments, relations, reviews, and build runs use their
corresponding top-level directories. `catalog.json` is a derived catalog rebuilt
from the canonical current Topic records. `wiki_data/.build.lock` exists only
while a writer holds the exclusive build lock.

## Build an approved week

Set the OpenAI-compatible connection variables required by the deployment,
including its base URL and API key. The default model is `z-ai/glm-5.2` unless
`KNOWLEDGE_LLM_MODEL` or `LLM_MODEL` overrides it. A non-loopback provider requires
an explicit data-policy acknowledgement. `KNOWLEDGE_LLM_DATA_POLICY_ACK=true`
records a deployment-level acknowledgement; `process_wiki.py
--allow-external-llm` also sets that value only for that command and restores its
previous value afterward. Loopback providers do not require this external-data
gate.

```bash
export KNOWLEDGE_LLM_MODEL='z-ai/glm-5.2'
export KNOWLEDGE_LLM_DATA_POLICY_ACK='true'
python process_wiki.py --week 2026-W30 --allow-external-llm
```

The command prints the build-run JSON. The environment export and CLI flag are
shown together to make the external opt-in explicit; either acknowledgement path
satisfies the runtime gate. A `published` result is complete;
`review_required` requires resolving pending assignment reviews before rerunning;
`partially_failed` lists the Topic IDs that failed while retaining their previous
valid revisions. The CLI exits successfully for `published` and `review_required`;
other statuses produce a nonzero exit. Do not enable either acknowledgement until
the selected provider is approved to receive the source data.

To opt the existing weekly pipeline into automatic Topic Wiki builds, configure
the deployment environment before running `run_pipeline.py`:

```bash
export ENABLE_TOPIC_WIKI='true'
export KNOWLEDGE_LLM_MODEL='z-ai/glm-5.2'
export KNOWLEDGE_LLM_DATA_POLICY_ACK='true'
python run_pipeline.py
```

The scheduled path calls `process_wiki.process_week()` directly, so it cannot use
the CLI flag and must receive `KNOWLEDGE_LLM_DATA_POLICY_ACK=true` in its
environment when the configured provider is external. If `ENABLE_TOPIC_WIKI` is
unset or differs from `true` case-insensitively, the pipeline does not build the
Topic Wiki.

## Classify and approve the week

Generate the classification JSON first:

```bash
export KNOWLEDGE_LLM_DATA_POLICY_ACK='true'
python process_agendas.py --week 2026-W30 --allow-external-llm
```

For `process_agendas.py`, the CLI flag is mandatory confirmation but does not set
the environment acknowledgement. An external provider therefore requires both
the flag and `KNOWLEDGE_LLM_DATA_POLICY_ACK=true`; a loopback provider still
requires the CLI flag but not the environment acknowledgement.

Resolve any classification items still requiring review in `/classification`.
Start the preview API in one terminal:

```bash
python -m uvicorn knowledge_preview:app --host 127.0.0.1 --port 8000
```

In another terminal, inspect the classification summaries. When the target week
reports `ready_for_approval`, approve it through the editor endpoint:

```bash
curl -fsS \
  http://127.0.0.1:8000/api/knowledge/classification/weeks \
  | python -m json.tool
curl -fsS -X POST \
  http://127.0.0.1:8000/api/knowledge/classification/weeks/2026-W30/approve \
  | python -m json.tool
```

The default `KNOWLEDGE_AUTH_MODE=disabled` supplies a local demo editor. In header
authentication mode, add trusted-proxy headers `X-User-Id` and
`X-User-Roles: knowledge-editor` to editor requests.

## Build and resolve Wiki reviews

Build directly with the CLI command shown above, or use the authenticated editor
endpoint while the preview API is running:

```bash
curl -fsS -X POST \
  http://127.0.0.1:8000/api/knowledge/wiki/builds/2026-W30 \
  | python -m json.tool
```

When that API uses an external provider, start the preview server with
`KNOWLEDGE_LLM_DATA_POLICY_ACK=true` in its environment; the direct CLI flag does
not affect a separate server process.

If the result is `review_required`, list pending reviews:

```bash
curl -fsS \
  'http://127.0.0.1:8000/api/knowledge/wiki/reviews?status=pending' \
  | python -m json.tool
```

For an assignment review, attach the Agenda to one of that review's returned
candidate Topic IDs:

```bash
REVIEW_ID='R-...'
TOPIC_ID='T-...'
curl -fsS -X POST \
  "http://127.0.0.1:8000/api/knowledge/wiki/reviews/${REVIEW_ID}/resolve" \
  -H 'Content-Type: application/json' \
  -d "{\"action\":\"attach\",\"topic_id\":\"${TOPIC_ID}\"}" \
  | python -m json.tool
```

Alternatively, create a new Topic with
`{"action":"create","title":"New Topic title"}`, or defer the review with
`{"action":"hold"}`. Rerun the Wiki build after all pending assignment reviews
are resolved. In header authentication mode, include the same editor headers on
the build and resolution requests.

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
failed draft or pointer update leaves the previous valid revision current. Recovery
must not run concurrently with a build.

First stop scheduled or direct Wiki builders and the preview API so no new editor
request can start a build. Verify that no build-capable process remains:

```bash
ps -ax -o pid=,command= \
  | grep -E '[p]rocess_wiki.py|[r]un_pipeline.py|[u]vicorn .*knowledge_preview'
```

Do not continue while this command prints a live process. After it prints no
matches, inspect a present lock and confirm that its recorded PID is absent:

```bash
cat wiki_data/.build.lock
ps -p "$(cat wiki_data/.build.lock)" -o pid=,etime=,command=
```

If no lock exists, skip directly to the recovery command below. If `ps` shows a
live process, do not recover or remove the lock. Stop here and let that process
finish. Only when the recorded PID is absent, remove the stale lock:

```bash
rm wiki_data/.build.lock
```

Then mark builds left in a transient state for more than one hour as interrupted
and rebuild the derived catalog from canonical current Topics:

```bash
python - <<'PY'
from wiki_store import JsonWikiStore

store = JsonWikiStore()
for run in store.recover_incomplete_builds():
    print(run.run_id, run.status, run.error)
store.rebuild_catalog()
PY
```

Review the printed run IDs and the corresponding files in `wiki_data/builds/`.
The recovery operation changes only stale transient build records to `failed`; it
does not replace current Topic revisions. Catalog rebuilding changes only the
derived `catalog.json`. After reviewing the recovered build records, rerun the
week. Identical successful input is reused; corrected or changed input creates a
new build.

## Backend verification

Run the focused Topic Wiki recovery, storage, projection, and API checks:

```bash
pytest tests/test_topic_wiki_models.py tests/test_wiki_store.py tests/test_topic_linker.py tests/test_topic_wiki_builder.py tests/test_wiki_projections.py tests/test_knowledge_api.py -q
```
