# August 2026 Business-Day Demo Data Design

**Date:** 2026-08-17
**Status:** Approved for planning
**Scope:** Deterministic local multi-source demo data only, plus the minimum
calendar-query normalization needed to retrieve it.

## Goal

Extend the local dummy corpus across every business day in August 2026 so a
user can immediately test beginning-of-month, current-week, and end-of-month
Mail and Calendar retrieval without OpenSearch, MongoDB, embeddings, or an LLM.
The exact input `이번주 일정알려줘` must return grounded Calendar evidence for
the current week instead of a no-evidence response.

## Chosen Approach

Commit explicit JSON fixture records. Do not generate records at application
startup and do not add a fixture-generation prerequisite to the demo. Explicit
records are larger than a generator but remain inspectable, deterministic, and
compatible with the existing `InMemoryMultiSourceSearch.from_path` boundary.

The existing eight records stay unchanged, including foreign-owner, inactive,
and cancelled security decoys. New IDs must not collide with them.

## Dataset Shape

August 2026 has 21 Monday-to-Friday dates: August 3-7, 10-14, 17-21,
24-28, and 31. Add two active `kim` records for each date:

- one Mail body record at 09:00 Asia/Seoul (`00:00Z`);
- one Calendar event at 10:00-11:00 Asia/Seoul (`01:00Z`-`02:00Z`).

This adds 42 records and brings the committed corpus from 8 to 50 records.
Week values use ISO weeks `2026-32` through `2026-36`.

Mail records include trusted top-level `team`, `week`, and `mail_type` fields.
Teams rotate among `YIELD팀`, `PROCESS팀`, and `EQUIPMENT팀`; mail types use
`daily_report`. Metadata includes a bounded chunk index and sender address.

Calendar records use stable IDs such as `event-kim-20260817`, repeat that ID in
`calendar_item_id`, and include bounded start/end timestamps, timezone,
organizer, and attendee metadata. Event text includes the generic term `일정`
and a concrete action so generic schedule requests and grounded detail answers
both work. Topics rotate across NAND yield, FDC, equipment inspection, process
conditions, and quality follow-up.

## Query Behavior

The current tokenizer treats `일정알려줘` as a single token. Adding unnatural
fixture text to match that token would couple data to one typo-like phrasing.
Instead, the deterministic planner normalizes an entity-free Calendar request
to the stable semantic query `일정`. Entity-bearing requests continue using
their extracted product, issue, meeting, or person values. Saved-event
follow-ups continue to prefer stable event expansion.

This change is limited to generic Calendar discovery. It does not change the
public API, source registry, date resolver, production aliases, owner filters,
or four-action bound.

## Security and Error Handling

Every new Mail and Calendar record belongs to `kim`, is active, and is not
cancelled. Existing `lee`, inactive, and cancelled records remain in the same
fixture so owner and lifecycle rejection tests continue to exercise decoys.
No secrets, physical index names, or raw external identifiers are introduced.

Fixture loading remains strict through `StoredDocument`. Invalid dates,
duplicate IDs, missing stable Calendar relation IDs, or malformed metadata are
test failures rather than runtime fallbacks.

## Testing

Implementation follows red-green TDD. Tests must prove:

1. the corpus contains exactly 21 new Mail bodies and 21 new Calendar events,
   one of each for every August 2026 business day;
2. all new IDs are unique and every timestamp falls within its intended local
   business day;
3. Mail team/week/type fields and Calendar stable relation metadata are valid;
4. `이번주 일정알려줘`, with a fixed 2026-08-17 clock, returns grounded events
   dated August 17-21 and uses `search_calendar`;
5. ISO-date queries can retrieve beginning-of-month and August 31 records;
6. foreign, inactive, and cancelled decoys remain excluded;
7. the canonical four-tool demo path is unchanged;
8. the full backend suite, frontend tests, lint, build, both CLI scenarios, and
   live two-turn ASGI smoke test remain green.

## Non-Goals

- generating data for weekends or months other than August 2026;
- changing production OpenSearch mappings or ingest pipelines;
- adding one attachment per daily event;
- changing the public `/v1/chat` schema;
- adding a new `이번달` relative-date expression in this fixture-only scope.
