# Search Document Metadata Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bound shared evidence metadata and make alternate CLI source assertions order- and duplicate-sensitive.

**Architecture:** Define one reusable Pydantic v2 metadata type in the domain contract and import its constants into the production sanitizer. Reuse the same contract on demo stored documents so both production and deterministic paths validate before structured observation serialization.

**Tech Stack:** Python 3.13, Pydantic v2, pytest, subprocess CLI tests

## Global Constraints

- Metadata permits only bounded simple JSON scalars, `null`, and flat bounded string lists.
- Nested containers, arbitrary objects, non-finite or excessive numbers, and serialized UTF-8 payloads above 4096 bytes fail closed.
- Preserve the user's untracked `MULTI_SOURCE_AGENTIC_RAG_CODEX_PLAN.md`.

---

### Task 1: Shared metadata contract

**Files:**
- Modify: `app/domain/agentic.py`
- Modify: `app/retrieval/multi_source.py`
- Modify: `app/retrieval/multi_source_opensearch.py`
- Test: `tests/mail_rag/test_agentic_contracts.py`

**Interfaces:**
- Consumes: production sanitizer bounds and `SearchDocument.metadata`
- Produces: reusable `SearchMetadata` and shared `MAX_METADATA_*` constants

- [x] **Step 1: Write failing boundary and serialization tests**

```python
with pytest.raises(ValidationError):
    _contract_document(metadata={"nested": {"unsafe": True}})

observation = Observation(action=action, result=result)
assert observation.model_dump(mode="json")["result"]["documents"][0]["metadata"] == metadata
```

- [x] **Step 2: Verify RED**

Run: `python -m pytest tests/mail_rag/test_agentic_contracts.py -q`
Expected: metadata key/count/item/aggregate cases fail because the shared contract is missing.

- [x] **Step 3: Implement one shared bounded Pydantic type**

```python
SearchMetadata = Annotated[
    dict[MetadataKey, MetadataValue],
    Field(max_length=MAX_METADATA_KEYS),
    AfterValidator(_validate_metadata_aggregate),
]
```

Use it for both `SearchDocument.metadata` and directly coupled `StoredDocument.metadata`; import the shared constants in the OpenSearch sanitizer.

- [x] **Step 4: Verify GREEN**

Run: `python -m pytest tests/mail_rag/test_agentic_contracts.py tests/mail_rag/test_multi_source_opensearch.py tests/mail_rag/test_multi_source_graph.py tests/mail_rag/test_multi_source_dummy.py -q`
Expected: all tests pass.

### Task 2: Exact alternate CLI sources and final verification

**Files:**
- Modify: `tests/mail_rag/test_multi_source_demo_api.py`
- Modify: `.superpowers/sdd/task-8-implementer-report.md`

**Interfaces:**
- Consumes: the existing `Sources:` CLI line
- Produces: exact ordered `['calendar', 'calendar']` assertion

- [x] **Step 1: Tighten the test and prove mutation RED**

```python
assert sources == ["calendar", "calendar"]
```

Temporarily add/reorder one emitted source, run the single test to observe failure, then restore the script exactly.

- [x] **Step 2: Verify GREEN and the complete matrix**

Run focused tests, the four-module security suite, `python -m pytest -q`, frontend test/lint/build, and both CLI commands. Expected: every command exits zero.

- [x] **Step 3: Commit**

```bash
git add app tests docs
git commit -m "fix(rag): bound evidence metadata"
```
