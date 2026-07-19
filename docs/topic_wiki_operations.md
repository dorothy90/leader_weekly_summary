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
├── transitions/
│   └── <review_id>.json
└── history/
    ├── evidence/
    │   └── <week>/<classification_run_id>/
    │       ├── _week.json
    │       └── <agenda_id>.json
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
from the canonical current Topic records. `wiki_data/.build.lock` is created
while a writer holds the exclusive build lock and can remain after an interrupted
process; use the stale-lock procedure below before removing it. Review transition
journals are replayed automatically when the store opens.

Approved evidence is copied once into `history/evidence/`; Topic revisions resolve
citations only through those immutable references. Wiki data created before this
evidence-reference contract must be rebuilt from approved classification weeks.
There is no fallback to mutable `classification_data` for an old Topic revision.

## Roll out the immutable-evidence contract

Use this procedure only for a deployment that already has Topic revisions without
`evidence_refs`. Stop Wiki writers first using the process check in the recovery
section. Choose explicit deployment paths and an unused backup path; do not point
either variable at a workspace root or home directory.

```bash
WIKI_DATA_PATH=/srv/weekly-mail-agent/wiki_data
WIKI_BACKUP_PATH=/srv/weekly-mail-agent/wiki_data.pre-evidence-backup
test -d "$WIKI_DATA_PATH"
test ! -e "$WIKI_BACKUP_PATH"
mv "$WIKI_DATA_PATH" "$WIKI_BACKUP_PATH"
mkdir -p "$WIKI_DATA_PATH"
```

The move is the rollback copy and is recoverable; do not delete it during rollout.
Rebuild every approved week in chronological order. Replace the sample list in
the script below with the deployment's verified approved-week list from
`classification_data`; do not include an unapproved week. The target directory is
passed explicitly to every command.

```bash
python - <<'PY'
import json
import subprocess

wiki_data_path = "/srv/weekly-mail-agent/wiki_data"
approved_weeks = ["2026-W28", "2026-W29", "2026-W30"]

for approved_week in approved_weeks:
    completed = subprocess.run(
        [
            "python", "process_wiki.py", "--week", approved_week,
            "--wiki-data-dir", wiki_data_path, "--allow-external-llm",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in {0, 1} or not completed.stdout.strip():
        raise SystemExit(
            f"{approved_week}: command failed: {completed.stderr.strip()}"
        )
    result = json.loads(completed.stdout.strip().splitlines()[-1])
    status = result["status"]
    print(approved_week, status, result["run_id"])
    if status == "review_required":
        raise SystemExit(
            f"{approved_week}: pause; resolve assignment reviews, rerun this same "
            "week until published, then resume with the following week"
        )
    if status != "published":
        raise SystemExit(f"{approved_week}: refusing to advance after {status}")
PY
```

When a run stops at `review_required`, resolve its assignment queue and run the
same command with the same `--week` and `--wiki-data-dir` until the parsed status
is `published`; only then remove that Week from the front of the script's list and
resume.

Verify the exact target before restarting writers. This check requires all three
sample Week snapshots, at least one Topic, and resolvable immutable evidence for
every current revision:

```bash
python - /srv/weekly-mail-agent/wiki_data <<'PY'
import sys
from pathlib import Path
from wiki_store import JsonWikiStore

target = Path(sys.argv[1]).resolve()
expected = Path("/srv/weekly-mail-agent/wiki_data")
if target != expected:
    raise SystemExit(f"unexpected Wiki target: {target}")
store = JsonWikiStore(target)
expected_weeks = ["2026-W28", "2026-W29", "2026-W30"]
if store.weeks() != expected_weeks:
    raise SystemExit(f"Week verification failed: {store.weeks()}")
topics = store.topics()
if not topics:
    raise SystemExit("Topic verification failed: no Topics")
for topic in topics:
    revision = store.topic_revision(topic.topic_id, topic.current_revision_id)
    if not revision.evidence_refs:
        raise SystemExit(f"missing evidence refs: {topic.topic_id}")
    for evidence_ref in revision.evidence_refs:
        store.archived_evidence(evidence_ref)
print(target, len(topics), store.weeks())
PY
```

Restart writers only after this command succeeds. Roll back by
stopping writers, moving the new `/srv/weekly-mail-agent/wiki_data` aside to a
separately named diagnostic path, and moving
`/srv/weekly-mail-agent/wiki_data.pre-evidence-backup` back to
`/srv/weekly-mail-agent/wiki_data`. Keep both directories until validation and
retention approval are complete.

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

## Release verification

Run the complete backend, Web regression, and production build gates before
deployment:

```bash
cd web
npm test
npm run build
cd ..
pytest -q
```

In the build metadata, verify that the configured default model is
`z-ai/glm-5.2` unless the deployment intentionally overrides it.
