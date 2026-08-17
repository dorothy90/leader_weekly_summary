# August 2026 Business-Day Demo Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic Mail and Calendar demo records for every August 2026 business day and make `이번주 일정알려줘` return the August 17-21 Calendar evidence.

**Architecture:** Keep the existing in-memory loader and append 42 explicit, schema-valid records to the committed JSON corpus. Add one narrow planner fallback for entity-free Calendar searches, fix the demo clock at Monday 2026-08-17, and prove the result through fixture, planner, API, CLI, and regression tests.

**Tech Stack:** Python 3.12, Pydantic v2, LangGraph workflow, pytest, FastAPI/httpx ASGI tests, JSON fixtures, React/Vite frontend regression checks.

## Global Constraints

- Preserve all eight existing corpus records byte-for-byte in meaning, including the `lee`, inactive, and cancelled decoys.
- Add exactly 21 active `kim` Mail body records and 21 active, non-cancelled `kim` Calendar event records for August 3-7, 10-14, 17-21, 24-28, and 31, 2026.
- Store Mail at 09:00 Asia/Seoul (`00:00Z`) and Calendar at 10:00-11:00 Asia/Seoul (`01:00Z`-`02:00Z`).
- Use ISO weeks `2026-32` through `2026-36`, `daily_report`, and rotating `YIELD팀`, `PROCESS팀`, and `EQUIPMENT팀` values for Mail.
- Give every Calendar event a stable `event-kim-YYYYMMDD` relationship ID in `document_id`, `source_id`, `parent_event_id`, and `metadata.calendar_item_id`.
- Keep the public API, production aliases, owner/lifecycle filters, date resolver, and four-action bound unchanged.
- Do not add runtime fixture generation, weekend records, other months, attachments for daily events, or a new `이번달` resolver.
- Follow red-green TDD for behavior changes and commit after each independently testable task.

---

## File Structure

- `fixtures/multi_source_demo/corpus.json`: retain the current eight records and append the 42 explicit August business-day records.
- `tests/mail_rag/test_multi_source_dummy.py`: own corpus cardinality, weekday coverage, timestamp, metadata, ID uniqueness, and decoy-preservation contracts.
- `app/llm/agentic.py`: normalize only entity-free `calendar_search` queries to the semantic token `일정`.
- `app/api/dependencies.py`: move the deterministic demo clock from Sunday August 16 to Monday August 17, 2026.
- `tests/mail_rag/test_agentic_dates.py`: prove generic Calendar normalization while preserving entity-bearing query behavior.
- `tests/mail_rag/test_multi_source_demo_api.py`: prove current-week and boundary-date responses through the public ASGI API.
- `docs/multi_source_demo.md`: document the committed date coverage, fixed demo date, and exact test command.

### Task 1: Lock and Populate the August Corpus Contract

**Files:**
- Modify: `tests/mail_rag/test_multi_source_dummy.py:1-40`
- Modify: `fixtures/multi_source_demo/corpus.json:1-110`

**Interfaces:**
- Consumes: `InMemoryMultiSourceSearch.from_path(path: Path, registry: SourceRegistry) -> InMemoryMultiSourceSearch` and `StoredDocument` fields already defined in `app/retrieval/multi_source.py`.
- Produces: a 50-record corpus containing 42 identifiable daily records whose IDs end in `YYYYMMDD`; Task 2 API tests rely on those IDs.

- [ ] **Step 1: Write the failing fixture contract test**

Change the datetime import and add the timezone import:

```python
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
```

Add these constants and tests immediately after `FIXTURE`:

```python
AUGUST_2026_BUSINESS_DAYS = {
    date(2026, 8, day)
    for day in (
        3, 4, 5, 6, 7,
        10, 11, 12, 13, 14,
        17, 18, 19, 20, 21,
        24, 25, 26, 27, 28,
        31,
    )
}
LEGACY_DOCUMENT_IDS = {
    "domain-cell-leakage",
    "mail-kim-body-0",
    "mail-kim-attachment-0",
    "event-kim-1",
    "event-kim-1-action",
    "mail-lee-decoy",
    "mail-kim-inactive",
    "event-kim-cancelled",
}


def _daily_documents():
    return [
        item
        for item in service().documents
        if item.document_id.startswith(
            ("mail-kim-202608", "event-kim-202608")
        )
    ]


def _document_day(document_id: str) -> date:
    return datetime.strptime(document_id[-8:], "%Y%m%d").date()


def test_august_corpus_has_one_mail_and_calendar_record_per_business_day():
    documents = service().documents
    daily = _daily_documents()
    mail = [item for item in daily if item.source_type == "mail"]
    calendar = [item for item in daily if item.source_type == "calendar"]

    assert len(documents) == 50
    assert len({item.document_id for item in documents}) == 50
    assert len(mail) == 21
    assert len(calendar) == 21
    assert {_document_day(item.document_id) for item in mail} == (
        AUGUST_2026_BUSINESS_DAYS
    )
    assert {_document_day(item.document_id) for item in calendar} == (
        AUGUST_2026_BUSINESS_DAYS
    )


def test_august_daily_records_have_local_business_hours_and_metadata():
    seoul = ZoneInfo("Asia/Seoul")
    daily = _daily_documents()

    assert len(daily) == 42

    for item in daily:
        business_day = _document_day(item.document_id)
        occurred = datetime.fromisoformat(
            item.occurred_at.replace("Z", "+00:00")
        )
        local = occurred.astimezone(seoul)

        assert local.date() == business_day
        assert item.employee_id == "kim"
        assert item.is_active is True
        assert item.is_cancelled is False

        if item.source_type == "mail":
            iso_year, iso_week, _ = business_day.isocalendar()
            assert item.index == "ews-mail-active"
            assert item.content_kind == "body"
            assert local.hour == 9
            assert item.team in {"YIELD팀", "PROCESS팀", "EQUIPMENT팀"}
            assert item.week == f"{iso_year}-{iso_week:02d}"
            assert item.mail_type == "daily_report"
            assert item.metadata == {
                "chunk_index": 0,
                "sender_email": "kim.oo@example.com",
            }
        else:
            start = datetime.fromisoformat(
                item.metadata["start_at_utc"].replace("Z", "+00:00")
            )
            end = datetime.fromisoformat(
                item.metadata["end_at_utc"].replace("Z", "+00:00")
            )
            assert item.index == "ews-calendar-active"
            assert item.content_kind == "event"
            assert local.hour == 10
            assert end - start == timedelta(hours=1)
            assert item.source_id == item.document_id
            assert item.parent_event_id == item.document_id
            assert item.metadata["calendar_item_id"] == item.document_id
            assert item.metadata["timezone"] == "Asia/Seoul"
            assert "일정" in f"{item.title} {item.text}"


def test_august_extension_preserves_security_decoys():
    by_id = {item.document_id: item for item in service().documents}

    assert LEGACY_DOCUMENT_IDS <= by_id.keys()
    assert by_id["mail-lee-decoy"].employee_id == "lee"
    assert by_id["mail-kim-inactive"].is_active is False
    assert by_id["event-kim-cancelled"].is_cancelled is True
```

- [ ] **Step 2: Run the fixture tests and verify the corpus contract fails**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_dummy.py \
  -k 'august_corpus or august_daily or august_extension' -v
```

Expected: the corpus-count and daily-metadata tests fail because there are 8 total records and 0 daily records; the decoy-preservation test passes.

- [ ] **Step 3: Append the 42 explicit records to the JSON fixture**

Keep the current eight objects unchanged. Add a comma after the existing `event-kim-cancelled` object, then append the following exact objects before the array's closing `]`:

```json
{"index":"ews-mail-active","document_id":"mail-kim-20260803","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260803","parent_event_id":null,"employee_id":"kim","team":"YIELD팀","week":"2026-32","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-03 NAND 수율 점검 일일 업무 메일","text":"김OO의 2026-08-03 업무 메일: NAND 수율 점검 상태를 확인했고 Action으로 수율 편차 원인을 분석한다.","occurred_at":"2026-08-03T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260803","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260803","parent_event_id":"event-kim-20260803","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-03 NAND 수율 점검 일정","text":"2026-08-03 일정: NAND 수율 점검 회의. Action: 수율 편차 원인을 분석한다.","occurred_at":"2026-08-03T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260803","start_at_utc":"2026-08-03T01:00:00Z","end_at_utc":"2026-08-03T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260804","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260804","parent_event_id":null,"employee_id":"kim","team":"PROCESS팀","week":"2026-32","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-04 FDC 추세 확인 일일 업무 메일","text":"김OO의 2026-08-04 업무 메일: FDC 추세 확인 상태를 확인했고 Action으로 장비 A FDC 로그를 검토한다.","occurred_at":"2026-08-04T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260804","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260804","parent_event_id":"event-kim-20260804","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-04 FDC 추세 확인 일정","text":"2026-08-04 일정: FDC 추세 확인 회의. Action: 장비 A FDC 로그를 검토한다.","occurred_at":"2026-08-04T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260804","start_at_utc":"2026-08-04T01:00:00Z","end_at_utc":"2026-08-04T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260805","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260805","parent_event_id":null,"employee_id":"kim","team":"EQUIPMENT팀","week":"2026-32","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-05 장비 정기 점검 일일 업무 메일","text":"김OO의 2026-08-05 업무 메일: 장비 정기 점검 상태를 확인했고 Action으로 PM 체크리스트를 확인한다.","occurred_at":"2026-08-05T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260805","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260805","parent_event_id":"event-kim-20260805","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-05 장비 정기 점검 일정","text":"2026-08-05 일정: 장비 정기 점검 회의. Action: PM 체크리스트를 확인한다.","occurred_at":"2026-08-05T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260805","start_at_utc":"2026-08-05T01:00:00Z","end_at_utc":"2026-08-05T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260806","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260806","parent_event_id":null,"employee_id":"kim","team":"YIELD팀","week":"2026-32","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-06 공정 조건 검토 일일 업무 메일","text":"김OO의 2026-08-06 업무 메일: 공정 조건 검토 상태를 확인했고 Action으로 split 조건 결과를 비교한다.","occurred_at":"2026-08-06T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260806","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260806","parent_event_id":"event-kim-20260806","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-06 공정 조건 검토 일정","text":"2026-08-06 일정: 공정 조건 검토 회의. Action: split 조건 결과를 비교한다.","occurred_at":"2026-08-06T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260806","start_at_utc":"2026-08-06T01:00:00Z","end_at_utc":"2026-08-06T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260807","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260807","parent_event_id":null,"employee_id":"kim","team":"PROCESS팀","week":"2026-32","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-07 품질 후속 조치 일일 업무 메일","text":"김OO의 2026-08-07 업무 메일: 품질 후속 조치 상태를 확인했고 Action으로 불량 샘플 분석 결과를 공유한다.","occurred_at":"2026-08-07T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260807","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260807","parent_event_id":"event-kim-20260807","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-07 품질 후속 조치 일정","text":"2026-08-07 일정: 품질 후속 조치 회의. Action: 불량 샘플 분석 결과를 공유한다.","occurred_at":"2026-08-07T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260807","start_at_utc":"2026-08-07T01:00:00Z","end_at_utc":"2026-08-07T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260810","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260810","parent_event_id":null,"employee_id":"kim","team":"EQUIPMENT팀","week":"2026-33","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-10 NAND 수율 점검 일일 업무 메일","text":"김OO의 2026-08-10 업무 메일: NAND 수율 점검 상태를 확인했고 Action으로 수율 편차 원인을 분석한다.","occurred_at":"2026-08-10T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260810","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260810","parent_event_id":"event-kim-20260810","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-10 NAND 수율 점검 일정","text":"2026-08-10 일정: NAND 수율 점검 회의. Action: 수율 편차 원인을 분석한다.","occurred_at":"2026-08-10T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260810","start_at_utc":"2026-08-10T01:00:00Z","end_at_utc":"2026-08-10T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260811","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260811","parent_event_id":null,"employee_id":"kim","team":"YIELD팀","week":"2026-33","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-11 FDC 추세 확인 일일 업무 메일","text":"김OO의 2026-08-11 업무 메일: FDC 추세 확인 상태를 확인했고 Action으로 장비 A FDC 로그를 검토한다.","occurred_at":"2026-08-11T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260811","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260811","parent_event_id":"event-kim-20260811","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-11 FDC 추세 확인 일정","text":"2026-08-11 일정: FDC 추세 확인 회의. Action: 장비 A FDC 로그를 검토한다.","occurred_at":"2026-08-11T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260811","start_at_utc":"2026-08-11T01:00:00Z","end_at_utc":"2026-08-11T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260812","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260812","parent_event_id":null,"employee_id":"kim","team":"PROCESS팀","week":"2026-33","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-12 장비 정기 점검 일일 업무 메일","text":"김OO의 2026-08-12 업무 메일: 장비 정기 점검 상태를 확인했고 Action으로 PM 체크리스트를 확인한다.","occurred_at":"2026-08-12T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260812","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260812","parent_event_id":"event-kim-20260812","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-12 장비 정기 점검 일정","text":"2026-08-12 일정: 장비 정기 점검 회의. Action: PM 체크리스트를 확인한다.","occurred_at":"2026-08-12T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260812","start_at_utc":"2026-08-12T01:00:00Z","end_at_utc":"2026-08-12T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260813","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260813","parent_event_id":null,"employee_id":"kim","team":"EQUIPMENT팀","week":"2026-33","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-13 공정 조건 검토 일일 업무 메일","text":"김OO의 2026-08-13 업무 메일: 공정 조건 검토 상태를 확인했고 Action으로 split 조건 결과를 비교한다.","occurred_at":"2026-08-13T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260813","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260813","parent_event_id":"event-kim-20260813","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-13 공정 조건 검토 일정","text":"2026-08-13 일정: 공정 조건 검토 회의. Action: split 조건 결과를 비교한다.","occurred_at":"2026-08-13T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260813","start_at_utc":"2026-08-13T01:00:00Z","end_at_utc":"2026-08-13T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260814","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260814","parent_event_id":null,"employee_id":"kim","team":"YIELD팀","week":"2026-33","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-14 품질 후속 조치 일일 업무 메일","text":"김OO의 2026-08-14 업무 메일: 품질 후속 조치 상태를 확인했고 Action으로 불량 샘플 분석 결과를 공유한다.","occurred_at":"2026-08-14T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260814","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260814","parent_event_id":"event-kim-20260814","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-14 품질 후속 조치 일정","text":"2026-08-14 일정: 품질 후속 조치 회의. Action: 불량 샘플 분석 결과를 공유한다.","occurred_at":"2026-08-14T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260814","start_at_utc":"2026-08-14T01:00:00Z","end_at_utc":"2026-08-14T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260817","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260817","parent_event_id":null,"employee_id":"kim","team":"PROCESS팀","week":"2026-34","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-17 NAND 수율 점검 일일 업무 메일","text":"김OO의 2026-08-17 업무 메일: NAND 수율 점검 상태를 확인했고 Action으로 수율 편차 원인을 분석한다.","occurred_at":"2026-08-17T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260817","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260817","parent_event_id":"event-kim-20260817","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-17 NAND 수율 점검 일정","text":"2026-08-17 일정: NAND 수율 점검 회의. Action: 수율 편차 원인을 분석한다.","occurred_at":"2026-08-17T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260817","start_at_utc":"2026-08-17T01:00:00Z","end_at_utc":"2026-08-17T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260818","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260818","parent_event_id":null,"employee_id":"kim","team":"EQUIPMENT팀","week":"2026-34","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-18 FDC 추세 확인 일일 업무 메일","text":"김OO의 2026-08-18 업무 메일: FDC 추세 확인 상태를 확인했고 Action으로 장비 A FDC 로그를 검토한다.","occurred_at":"2026-08-18T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260818","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260818","parent_event_id":"event-kim-20260818","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-18 FDC 추세 확인 일정","text":"2026-08-18 일정: FDC 추세 확인 회의. Action: 장비 A FDC 로그를 검토한다.","occurred_at":"2026-08-18T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260818","start_at_utc":"2026-08-18T01:00:00Z","end_at_utc":"2026-08-18T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260819","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260819","parent_event_id":null,"employee_id":"kim","team":"YIELD팀","week":"2026-34","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-19 장비 정기 점검 일일 업무 메일","text":"김OO의 2026-08-19 업무 메일: 장비 정기 점검 상태를 확인했고 Action으로 PM 체크리스트를 확인한다.","occurred_at":"2026-08-19T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260819","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260819","parent_event_id":"event-kim-20260819","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-19 장비 정기 점검 일정","text":"2026-08-19 일정: 장비 정기 점검 회의. Action: PM 체크리스트를 확인한다.","occurred_at":"2026-08-19T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260819","start_at_utc":"2026-08-19T01:00:00Z","end_at_utc":"2026-08-19T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260820","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260820","parent_event_id":null,"employee_id":"kim","team":"PROCESS팀","week":"2026-34","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-20 공정 조건 검토 일일 업무 메일","text":"김OO의 2026-08-20 업무 메일: 공정 조건 검토 상태를 확인했고 Action으로 split 조건 결과를 비교한다.","occurred_at":"2026-08-20T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260820","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260820","parent_event_id":"event-kim-20260820","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-20 공정 조건 검토 일정","text":"2026-08-20 일정: 공정 조건 검토 회의. Action: split 조건 결과를 비교한다.","occurred_at":"2026-08-20T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260820","start_at_utc":"2026-08-20T01:00:00Z","end_at_utc":"2026-08-20T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260821","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260821","parent_event_id":null,"employee_id":"kim","team":"EQUIPMENT팀","week":"2026-34","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-21 품질 후속 조치 일일 업무 메일","text":"김OO의 2026-08-21 업무 메일: 품질 후속 조치 상태를 확인했고 Action으로 불량 샘플 분석 결과를 공유한다.","occurred_at":"2026-08-21T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260821","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260821","parent_event_id":"event-kim-20260821","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-21 품질 후속 조치 일정","text":"2026-08-21 일정: 품질 후속 조치 회의. Action: 불량 샘플 분석 결과를 공유한다.","occurred_at":"2026-08-21T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260821","start_at_utc":"2026-08-21T01:00:00Z","end_at_utc":"2026-08-21T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260824","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260824","parent_event_id":null,"employee_id":"kim","team":"YIELD팀","week":"2026-35","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-24 NAND 수율 점검 일일 업무 메일","text":"김OO의 2026-08-24 업무 메일: NAND 수율 점검 상태를 확인했고 Action으로 수율 편차 원인을 분석한다.","occurred_at":"2026-08-24T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260824","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260824","parent_event_id":"event-kim-20260824","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-24 NAND 수율 점검 일정","text":"2026-08-24 일정: NAND 수율 점검 회의. Action: 수율 편차 원인을 분석한다.","occurred_at":"2026-08-24T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260824","start_at_utc":"2026-08-24T01:00:00Z","end_at_utc":"2026-08-24T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260825","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260825","parent_event_id":null,"employee_id":"kim","team":"PROCESS팀","week":"2026-35","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-25 FDC 추세 확인 일일 업무 메일","text":"김OO의 2026-08-25 업무 메일: FDC 추세 확인 상태를 확인했고 Action으로 장비 A FDC 로그를 검토한다.","occurred_at":"2026-08-25T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260825","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260825","parent_event_id":"event-kim-20260825","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-25 FDC 추세 확인 일정","text":"2026-08-25 일정: FDC 추세 확인 회의. Action: 장비 A FDC 로그를 검토한다.","occurred_at":"2026-08-25T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260825","start_at_utc":"2026-08-25T01:00:00Z","end_at_utc":"2026-08-25T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260826","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260826","parent_event_id":null,"employee_id":"kim","team":"EQUIPMENT팀","week":"2026-35","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-26 장비 정기 점검 일일 업무 메일","text":"김OO의 2026-08-26 업무 메일: 장비 정기 점검 상태를 확인했고 Action으로 PM 체크리스트를 확인한다.","occurred_at":"2026-08-26T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260826","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260826","parent_event_id":"event-kim-20260826","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-26 장비 정기 점검 일정","text":"2026-08-26 일정: 장비 정기 점검 회의. Action: PM 체크리스트를 확인한다.","occurred_at":"2026-08-26T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260826","start_at_utc":"2026-08-26T01:00:00Z","end_at_utc":"2026-08-26T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260827","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260827","parent_event_id":null,"employee_id":"kim","team":"YIELD팀","week":"2026-35","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-27 공정 조건 검토 일일 업무 메일","text":"김OO의 2026-08-27 업무 메일: 공정 조건 검토 상태를 확인했고 Action으로 split 조건 결과를 비교한다.","occurred_at":"2026-08-27T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260827","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260827","parent_event_id":"event-kim-20260827","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-27 공정 조건 검토 일정","text":"2026-08-27 일정: 공정 조건 검토 회의. Action: split 조건 결과를 비교한다.","occurred_at":"2026-08-27T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260827","start_at_utc":"2026-08-27T01:00:00Z","end_at_utc":"2026-08-27T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260828","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260828","parent_event_id":null,"employee_id":"kim","team":"PROCESS팀","week":"2026-35","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-28 품질 후속 조치 일일 업무 메일","text":"김OO의 2026-08-28 업무 메일: 품질 후속 조치 상태를 확인했고 Action으로 불량 샘플 분석 결과를 공유한다.","occurred_at":"2026-08-28T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260828","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260828","parent_event_id":"event-kim-20260828","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-28 품질 후속 조치 일정","text":"2026-08-28 일정: 품질 후속 조치 회의. Action: 불량 샘플 분석 결과를 공유한다.","occurred_at":"2026-08-28T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260828","start_at_utc":"2026-08-28T01:00:00Z","end_at_utc":"2026-08-28T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}},
{"index":"ews-mail-active","document_id":"mail-kim-20260831","source_type":"mail","content_kind":"body","source_id":"mail-kim-20260831","parent_event_id":null,"employee_id":"kim","team":"EQUIPMENT팀","week":"2026-36","mail_type":"daily_report","is_active":true,"is_cancelled":false,"title":"2026-08-31 NAND 수율 점검 일일 업무 메일","text":"김OO의 2026-08-31 업무 메일: NAND 수율 점검 상태를 확인했고 Action으로 수율 편차 원인을 분석한다.","occurred_at":"2026-08-31T00:00:00Z","metadata":{"chunk_index":0,"sender_email":"kim.oo@example.com"}},
{"index":"ews-calendar-active","document_id":"event-kim-20260831","source_type":"calendar","content_kind":"event","source_id":"event-kim-20260831","parent_event_id":"event-kim-20260831","employee_id":"kim","is_active":true,"is_cancelled":false,"title":"2026-08-31 NAND 수율 점검 일정","text":"2026-08-31 일정: NAND 수율 점검 회의. Action: 수율 편차 원인을 분석한다.","occurred_at":"2026-08-31T01:00:00Z","metadata":{"calendar_item_id":"event-kim-20260831","start_at_utc":"2026-08-31T01:00:00Z","end_at_utc":"2026-08-31T02:00:00Z","timezone":"Asia/Seoul","organizer_email":"lead@example.com","attendee_emails":["kim.oo@example.com"]}}
```

- [ ] **Step 4: Run the complete in-memory search test file**

Run:

```bash
python -m pytest tests/mail_rag/test_multi_source_dummy.py -v
```

Expected: all tests in `test_multi_source_dummy.py` pass, including the three new corpus-contract tests.

- [ ] **Step 5: Commit the corpus contract and data**

```bash
git add tests/mail_rag/test_multi_source_dummy.py \
  fixtures/multi_source_demo/corpus.json
git commit -m "feat(demo): add August business-day corpus"
```

### Task 2: Normalize Generic Calendar Discovery and Prove Public Responses

**Files:**
- Modify: `tests/mail_rag/test_agentic_dates.py:58-69`
- Modify: `tests/mail_rag/test_multi_source_demo_api.py:1-128`
- Modify: `app/llm/agentic.py:102-109`
- Modify: `app/api/dependencies.py:205-229`

**Interfaces:**
- Consumes: Task 1's `event-kim-YYYYMMDD` records and the existing `RuleBasedAgentModel.analyze/plan` workflow.
- Produces: `RuleBasedAgentModel._query(question: str, analysis: QueryAnalysis) -> str` returning `일정` only when no recognized entity exists and `question_type == "calendar_search"`; the demo container uses a timezone-aware 2026-08-17 clock.

- [ ] **Step 1: Write failing planner tests for generic and entity-bearing Calendar queries**

Append to `tests/mail_rag/test_agentic_dates.py`:

```python
def test_entity_free_calendar_plan_uses_stable_schedule_query():
    model = RuleBasedAgentModel(now=datetime(2026, 8, 17, tzinfo=UTC))
    memory = ConversationMemory()
    question = "이번주 일정알려줘"
    query_analysis = asyncio.run(
        model.analyze(question, memory, "Asia/Seoul")
    )
    action = asyncio.run(
        model.plan(question, query_analysis, [], memory)
    )

    assert query_analysis.question_type == "calendar_search"
    assert action.tool == "search_calendar"
    assert action.query == "일정"


def test_entity_bearing_calendar_plan_keeps_extracted_entity_query():
    model = RuleBasedAgentModel(now=datetime(2026, 8, 17, tzinfo=UTC))
    memory = ConversationMemory()
    question = "이번주 NAND 일정 알려줘"
    query_analysis = asyncio.run(
        model.analyze(question, memory, "Asia/Seoul")
    )
    action = asyncio.run(
        model.plan(question, query_analysis, [], memory)
    )

    assert action.tool == "search_calendar"
    assert action.query == "NAND"
```

- [ ] **Step 2: Write the failing current-week API test and boundary-date guards**

Add `import pytest` after the existing standard-library imports in `tests/mail_rag/test_multi_source_demo_api.py`, then add these tests after the canonical-flow test:

```python
def test_demo_api_returns_all_current_week_events_for_generic_schedule_question():
    container = build_demo_container()
    app = create_app(container)

    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": "이번주 일정알려줘",
                "response_mode": "fast",
            },
        )
    )

    assert response.status_code == 200
    body = response.json()
    assert {item["document_id"] for item in body["references"]} == {
        "event-kim-20260817",
        "event-kim-20260818",
        "event-kim-20260819",
        "event-kim-20260820",
        "event-kim-20260821",
    }
    assert body["quality"]["citation_valid"] is True
    assert body["quality"]["limited_answer"] is False
    assert [
        action.tool for action, _owner in container.fast.agentic.search.calls
    ] == ["search_calendar"]
    assert container.fast.agentic.search.calls[0][0].query == "일정"


@pytest.mark.parametrize(
    ("day", "expected_event_id"),
    [
        ("2026-08-03", "event-kim-20260803"),
        ("2026-08-31", "event-kim-20260831"),
    ],
)
def test_demo_api_retrieves_beginning_and_end_of_month_events(
    day,
    expected_event_id,
):
    app = create_app(build_demo_container())

    response = asyncio.run(
        post(
            app,
            {
                "user_id": "kim",
                "message": f"{day} 일정 알려줘",
                "response_mode": "fast",
            },
        )
    )

    assert response.status_code == 200
    assert {
        item["document_id"] for item in response.json()["references"]
    } == {expected_event_id}
```

- [ ] **Step 3: Run the new behavior tests and verify they fail for the intended reasons**

Run:

```bash
python -m pytest \
  tests/mail_rag/test_agentic_dates.py \
  tests/mail_rag/test_multi_source_demo_api.py \
  -k 'entity_free_calendar or entity_bearing_calendar or current_week_events or beginning_and_end' \
  -v
```

Expected: the entity-free planner test reports the raw `이번주 일정알려줘` query instead of `일정`, and the current-week API test has no references. The entity-bearing and ISO-boundary tests may already pass; they guard the narrowness of the change and the existing date resolver.

- [ ] **Step 4: Implement the minimum entity-free Calendar normalization**

Replace `RuleBasedAgentModel._query` in `app/llm/agentic.py` with:

```python
    @staticmethod
    def _query(question, analysis):
        values = [
            analysis.entities[key]
            for key in ("product", "issue", "meeting", "person")
            if key in analysis.entities
        ]
        if values:
            return " ".join(dict.fromkeys(values))
        if analysis.question_type == "calendar_search":
            return "일정"
        return question
```

This leaves Mail, domain, multi-source, and entity-bearing Calendar queries on their existing paths.

- [ ] **Step 5: Move the deterministic demo clock to Monday August 17**

In `build_demo_container` in `app/api/dependencies.py`, replace the model construction with:

```python
    model = RuleBasedAgentModel(now=datetime(2026, 8, 17, 0, tzinfo=UTC))
```

At 09:00 Asia/Seoul this makes `이번주` resolve to the half-open local week August 17-24 and `지난주` resolve to August 10-17, which is covered by Task 1 data.

- [ ] **Step 6: Run focused planner, API, and canonical regression tests**

Run:

```bash
python -m pytest \
  tests/mail_rag/test_agentic_dates.py \
  tests/mail_rag/test_multi_source_demo_api.py \
  tests/mail_rag/test_multi_source_dummy.py -v
```

Expected: all tests pass. In particular, the canonical API still uses `search_mail -> search_calendar -> expand_calendar_event -> search_domain_knowledge`, while the exact generic schedule question uses only `search_calendar` and returns five references dated August 17-21.

- [ ] **Step 7: Commit the query behavior and public-response tests**

```bash
git add app/llm/agentic.py app/api/dependencies.py \
  tests/mail_rag/test_agentic_dates.py \
  tests/mail_rag/test_multi_source_demo_api.py
git commit -m "fix(demo): ground generic weekly schedules"
```

### Task 3: Document Coverage and Run the Full Release Gate

**Files:**
- Modify: `docs/multi_source_demo.md:7-23`
- Verify: entire backend and frontend without additional source changes

**Interfaces:**
- Consumes: Task 2's fixed demo date and generic Calendar behavior.
- Produces: operator-facing commands for the exact user phrase and fresh release evidence for the whole repository.

- [ ] **Step 1: Document the committed August coverage and exact command**

Insert this section after the CLI section in `docs/multi_source_demo.md`:

````markdown
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
````

- [ ] **Step 2: Run the full backend suite**

Run:

```bash
python -m pytest -q
```

Expected: exit status 0 with no failed tests.

- [ ] **Step 3: Run frontend tests, lint, and production build**

Run from `frontend/`:

```bash
npm test -- --run
npm run lint
npm run build
```

Expected: all three commands exit 0; Vitest reports no failed tests, ESLint reports no errors, and Vite creates the production bundle.

- [ ] **Step 4: Run both required CLI scenarios**

Run from the repository root:

```bash
OPENROUTER_API_KEY= python scripts/run_multi_source_demo.py
OPENROUTER_API_KEY= python scripts/run_multi_source_demo.py "이번주 일정알려줘"
```

Expected: both commands exit 0. The first prints the canonical four-tool order and Mail/Calendar/domain sources. The second prints five cited Calendar lines, `Sources: calendar, calendar, calendar, calendar, calendar`, and `Tool calls: search_calendar`.

- [ ] **Step 5: Run a live two-turn ASGI smoke test**

Start the server in terminal A:

```bash
MULTI_SOURCE_DEMO=true OPENROUTER_API_KEY= \
  uvicorn app.api.main:app --host 127.0.0.1 --port 8000
```

Run this client in terminal B:

```bash
python - <<'PY'
import httpx

base = "http://127.0.0.1:8000"
ready = httpx.get(f"{base}/ready", timeout=10)
ready.raise_for_status()
first = httpx.post(
    f"{base}/v1/chat",
    json={
        "user_id": "kim",
        "message": "NAND Yield Review 회의 찾아줘",
        "response_mode": "fast",
    },
    timeout=10,
)
first.raise_for_status()
second = httpx.post(
    f"{base}/v1/chat",
    json={
        "user_id": "kim",
        "conversation_id": first.json()["conversation_id"],
        "message": "그 회의에서 Action 뭐였어?",
        "response_mode": "fast",
    },
    timeout=10,
)
second.raise_for_status()
assert first.json()["references"]
assert second.json()["references"]
assert "Action" in second.json()["answer"]
print("live ASGI two-turn smoke: PASS")
PY
```

Expected: the client prints `live ASGI two-turn smoke: PASS`; stop terminal A with Ctrl-C afterward.

- [ ] **Step 6: Inspect the final diff and commit documentation**

Run:

```bash
git diff --check
git status --short
git diff --stat HEAD~2
```

Expected: `git diff --check` is silent; only the intended documentation change is uncommitted. The pre-existing untracked `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md` remains untouched and must not be staged.

Commit only the demo documentation:

```bash
git add docs/multi_source_demo.md
git commit -m "docs(demo): describe August fixture coverage"
```

Final expected repository state: no uncommitted implementation files, with only the pre-existing untracked `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md` still shown by `git status --short`.
