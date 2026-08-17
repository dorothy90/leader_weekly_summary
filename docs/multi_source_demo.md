# Multi-Source Agentic RAG Demo

## Requirements

- Python dependencies from `requirements.txt`
- Node dependencies already installed with `cd frontend && npm ci` when needed
- no OpenSearch, MongoDB, embedding, or LLM service for demo mode

## CLI

Run the canonical cross-source question or pass a custom question:

```bash
python scripts/run_multi_source_demo.py
python scripts/run_multi_source_demo.py "지난주 NAND 회의에서 Action 뭐였어?"
```

The canonical offline scenario exits with status 0, shows the scripted tool order
`search_mail`, `search_calendar`, and `search_domain_knowledge`, and prints cited
Mail, Calendar, and Domain evidence. A custom question uses the configured LLM for
routing, planning, evidence judging, and answer generation; it does not have to use
the canonical three-tool path.

## August 2026 dummy coverage

The committed corpus contains one Mail body at 09:00 Asia/Seoul and one
Calendar event at 10:00-11:00 Asia/Seoul for every Monday-to-Friday date in
August 2026: August 3-7, 10-14, 17-21, 24-28, and 31. Demo time is fixed at
August 17, 2026 so this command returns five grounded Calendar events for
August 17-21:

```bash
python scripts/run_multi_source_demo.py "이번주 일정알려줘"
```

Use an explicit ISO date to inspect the month boundaries, for example
`2026-08-03 일정 알려줘` or `2026-08-31 일정 알려줘`.

## API and frontend

Start the demo API and frontend in separate terminals:

```bash
MULTI_SOURCE_DEMO=true uvicorn app.api.main:app --host 127.0.0.1 --port 8000
cd frontend && npm run dev
```

Open the frontend's RAG console, use `user_id=kim`, and send a question. Every
chat request enters the same multi-source agent; there is no execution-mode
selector. No `.env` secrets are required. Verify the API before opening the
frontend:

```bash
curl --fail --silent http://127.0.0.1:8000/ready
```

Demo readiness reports its in-memory dependencies as ready; it does not probe
OpenSearch or MongoDB.

## Production aliases

Configure `DOMAIN_KNOWLEDGE_INDEX`, `MAIL_INDEX_ALIAS`, and
`CALENDAR_INDEX_ALIAS`. The defaults are `syld_gpt`, `ews-mail-active`, and
`ews-calendar-active`. The service validates the Mail and Calendar aliases in
readiness and never accepts an index name from the model. Alias cutover does
not require an agent code change.

## Security checks

Mail is filtered by the current `employee_id` and `is_active=true`. Calendar
adds `is_cancelled=false`. Event expansion repeats the same filters. Missing,
inactive, cancelled, or foreign events return no bundle and do not reveal
whether they exist. Production expansion first authorizes the canonical parent
event independently of attachment/output filters and only then loads its
filtered bundle. Calendar evidence keeps its unique storage document ID while
validated `calendar_item_id`/`parent_event_id` relation keys are used for
expansion and follow-up memory. Only normalized same-owner evidence can enter
model context, memory, citations, or public references.
