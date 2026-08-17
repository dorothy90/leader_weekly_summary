# Multi-Source Agentic RAG Architecture HTML Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a self-contained Korean HTML inspection board for the multi-source graph, four LLM prompt contracts, Python safety boundaries, retrieval, multi-turn memory, and verified Calendar trace.

**Architecture:** Add one root-level HTML artifact with embedded CSS, sanitized static content, and progressive-enhancement JavaScript for the four-stage prompt rail. Add standard-library Python structural tests, then verify desktop and mobile browser rendering.

**Tech Stack:** HTML5, embedded CSS, vanilla JavaScript, Python `html.parser`, pytest, local browser inspection.

## Global Constraints

- Output file is exactly `MultiSource_Agentic_RAG_Architecture.html`.
- Use one local file with no framework, build step, CDN, remote font, or network request.
- Visible prose is Korean except exact identifiers, prompts, schemas, and trace values.
- Semantic stages are exactly `routing`, `planner`, `judge`, and `answer`.
- Visually distinguish LLM semantics from deterministic Python controls.
- Disclose that full message history is stored but not passed to every semantic call.
- Disclose that citation validation checks ID existence and ownership, not entailment.
- Use public August 2026 dummy data only; include no secrets, private endpoints, or live trace IDs.
- Remain usable at 320 CSS pixels and with reduced motion.

## File structure

- Create `MultiSource_Agentic_RAG_Architecture.html`: complete standalone artifact.
- Create `tests/mail_rag/test_architecture_html.py`: structure, content, accessibility, offline-resource, and secret-pattern tests.
- Modify no application runtime files.

---

### Task 1: Lock the content contract and build the static architecture

**Files:**
- Create: `tests/mail_rag/test_architecture_html.py`
- Create: `MultiSource_Agentic_RAG_Architecture.html`

**Interfaces:**
- Consumes: contracts in `app/llm/agentic.py`, `app/graphs/multi_source.py`, `app/api/routes/chat.py`, `app/persistence/conversations.py`, and `app/security/citations.py`.
- Produces: section IDs `overview`, `system-map`, `llm-rail`, `responsibility-boundary`, `multi-index`, `multi-turn`, `verified-trace`, and `limitations`.

- [ ] **Step 1: Write the failing structural test**

Create `tests/mail_rag/test_architecture_html.py`:

```python
from html.parser import HTMLParser
from pathlib import Path
import re

ARTIFACT = Path("MultiSource_Agentic_RAG_Architecture.html")
REQUIRED_SECTIONS = {
    "overview", "system-map", "llm-rail", "responsibility-boundary",
    "multi-index", "multi-turn", "verified-trace", "limitations",
}

class ArchitectureParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.stages = []
        self.external_resources = []
        self.buttons = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"])
        if values.get("data-stage"):
            self.stages.append(values["data-stage"])
        if tag == "button":
            self.buttons.append(values)
        resource = values.get("src") or values.get("href")
        if resource and re.match(r"https?://", resource):
            self.external_resources.append(resource)

def artifact_text():
    return ARTIFACT.read_text(encoding="utf-8")

def parsed_artifact():
    parser = ArchitectureParser()
    parser.feed(artifact_text())
    return parser

def test_architecture_html_contains_complete_current_system_contract():
    parser = parsed_artifact()
    text = artifact_text()
    assert REQUIRED_SECTIONS <= parser.ids
    assert parser.stages == ["routing", "planner", "judge", "answer"]
    for required in (
        "POST /v1/chat", "IntentDecision", "PlanningDecision",
        "JudgeDecision", "AnswerDecision", "search_domain_knowledge",
        "search_mail", "search_calendar", "expand_calendar_event",
        "routing → planner → judge → answer", "전체 메시지 기록",
        "의미적 뒷받침",
    ):
        assert required in text

def test_architecture_html_is_offline_and_contains_no_secret_examples():
    parser = parsed_artifact()
    text = artifact_text()
    assert parser.external_resources == []
    assert not re.search(
        r"(?i)(api[_-]?key|password)\s*[:=]\s*['\"][^'\"]+", text
    )
    assert "x-trace-id" not in text
    assert "mongodb://" not in text
    assert "@example.com" not in text
```

- [ ] **Step 2: Run the test and verify RED**

Run `python -m pytest tests/mail_rag/test_architecture_html.py -q`.

Expected: FAIL because the HTML file does not exist.

- [ ] **Step 3: Implement the static semantic structure**

Create a valid Korean HTML5 document with the eight required sections. Embed all CSS and JavaScript. The overview states `한 질문 · 의미 판단 LLM 4회 · 제한된 에이전트 루프 1개`. The end-to-end map shows client, `/v1/chat`, owner-scoped memory load, Routing, Planner, allowlisted executor, Judge loop, Answer, citation validation, memory save, and response.

Use the exact current prompts from `app/llm/agentic.py` and sanitized payload examples. Include the Domain (`syld_gpt`), Mail (`ews-mail-active`), and Calendar (`ews-calendar-active`) branches. State that Fast/Deep/Diagnostic is not in the current path.

- [ ] **Step 4: Run the structural tests and verify GREEN**

Run `python -m pytest tests/mail_rag/test_architecture_html.py -q`.

Expected: both tests PASS.

- [ ] **Step 5: Commit the static artifact**

```bash
git add MultiSource_Agentic_RAG_Architecture.html tests/mail_rag/test_architecture_html.py
git commit -m "docs(rag): add architecture inspection board"
```

### Task 2: Add prompt inspection and accessibility

**Files:**
- Modify: `tests/mail_rag/test_architecture_html.py`
- Modify: `MultiSource_Agentic_RAG_Architecture.html`

**Interfaces:**
- Consumes: four `data-stage` buttons and `id="stage-detail"`.
- Produces: keyboard stage switching, show-all prompt expansion, mobile layout, reduced-motion behavior.

- [ ] **Step 1: Write failing interaction tests**

Append:

```python
def test_llm_stage_controls_are_accessible_and_progressively_enhanced():
    parser = parsed_artifact()
    text = artifact_text()
    stage_buttons = [item for item in parser.buttons if item.get("data-stage")]
    assert len(stage_buttons) == 4
    assert all(item.get("aria-controls") == "stage-detail" for item in stage_buttons)
    assert stage_buttons[0].get("aria-pressed") == "true"
    assert all(item.get("type") == "button" for item in stage_buttons)
    assert 'id="show-all-prompts"' in text
    assert 'id="stage-detail"' in text
    assert "selectStage" in text
    assert "renderAllPrompts" in text

def test_architecture_html_has_mobile_and_reduced_motion_rules():
    text = artifact_text()
    assert "@media (max-width: 760px)" in text
    assert "@media (prefers-reduced-motion: reduce)" in text
    assert "overflow-wrap: anywhere" in text
    assert ":focus-visible" in text
```

- [ ] **Step 2: Run the test and verify RED**

Run `python -m pytest tests/mail_rag/test_architecture_html.py -q`.

Expected: FAIL on missing controls, functions, or media rules.

- [ ] **Step 3: Implement the stage data and controls**

Define one frozen `stages` JavaScript object keyed by `routing`, `planner`, `judge`, and `answer`. Each value contains number, label, role, exact prompt, sanitized input, Pydantic output, downstream consumer, failure behavior, and repeat behavior.

Implement `selectStage(stageName)` to update the labeled detail region and `aria-pressed`. Implement `renderAllPrompts()` to show all contracts and update the toggle label. Keep the complete Routing panel in initial HTML for no-JavaScript reading.

Add `@media (max-width: 760px)`, `@media (prefers-reduced-motion: reduce)`, `:focus-visible`, `overflow-wrap: anywhere`, and print rules. Run the trace pulse once only and disable it for reduced motion and print.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run `python -m pytest tests/mail_rag/test_architecture_html.py -q`.

Expected: all tests PASS.

- [ ] **Step 5: Commit the prompt inspector**

```bash
git add MultiSource_Agentic_RAG_Architecture.html tests/mail_rag/test_architecture_html.py
git commit -m "feat(docs): add interactive prompt inspector"
```

### Task 3: Browser critique and final verification

**Files:**
- Modify if browser defects require it: `MultiSource_Agentic_RAG_Architecture.html`
- Modify for regression coverage if needed: `tests/mail_rag/test_architecture_html.py`

**Interfaces:**
- Consumes: the complete standalone artifact.
- Produces: desktop/mobile verified HTML with no overflow or inaccessible controls.

- [ ] **Step 1: Serve the artifact locally**

Run `python -m http.server 8765 --bind 127.0.0.1`.

Open `http://127.0.0.1:8765/MultiSource_Agentic_RAG_Architecture.html`.

- [ ] **Step 2: Inspect desktop rendering**

At approximately 1440×1000, verify no horizontal overflow; click all four LLM stages; expand all prompts; keyboard-tab through navigation and buttons; inspect connector alignment, evidence hierarchy, and warning prominence.

- [ ] **Step 3: Inspect mobile and reduced-motion rendering**

At 390×844 and 320×700, verify the system map becomes vertical, stage controls remain reachable, code scrolls inside its container, and prose wraps. Emulate reduced motion and confirm no trace animation.

- [ ] **Step 4: Apply evidence-based corrections**

Use `apply_patch` only for defects observed in Steps 2–3. Add a failing structural test first for any defect that can be asserted, then make it pass. Do not add sections, dependencies, API calls, or runtime changes.

- [ ] **Step 5: Run final verification**

```bash
python -m pytest tests/mail_rag/test_architecture_html.py -q
PYTHONDONTWRITEBYTECODE=1 python -m pytest tests/mail_rag --ignore=tests/mail_rag/test_agent_intent_evaluation.py -q
git diff --check
```

Expected: architecture tests pass, the Mail RAG suite has zero failures, and `git diff --check` emits no output.

- [ ] **Step 6: Commit browser corrections if any**

```bash
git add MultiSource_Agentic_RAG_Architecture.html tests/mail_rag/test_architecture_html.py
git commit -m "fix(docs): polish architecture board layout"
```

If no files changed, do not create an empty commit.
