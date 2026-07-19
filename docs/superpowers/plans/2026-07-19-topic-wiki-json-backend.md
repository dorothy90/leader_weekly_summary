# Topic Wiki JSON Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a JSON-only Topic Wiki backend that consumes an approved LOTCD classification week, links Agenda evidence into persistent Topics, publishes cited revisions, and serves Topic, LOTCD, Team, and Week APIs.

**Architecture:** Keep `classification_data/{week}.json` read-only and authoritative for classification. Add strict Pydantic contracts plus an atomic `wiki_data/` store; Topic is canonical, while LOTCD and Team are computed projections and Week is a versioned snapshot. Use the existing OpenAI-compatible LangChain connection with `z-ai/glm-5.2` as the default model, without adding SQLite, OpenSearch, or a second embedding stage.

**Tech Stack:** Python 3.11+, Pydantic v2, LangChain `ChatOpenAI.with_structured_output`, JSON files with `os.replace`, FastAPI, pytest.

## Global Constraints

- Work only in `.worktrees/lotcd-classification` on `codex/lotcd-classification`.
- Default LLM model is exactly `z-ai/glm-5.2`; `KNOWLEDGE_LLM_MODEL` and `LLM_MODEL` retain override precedence.
- Do not add SQLite, OpenSearch Wiki indexes, vector fields, or new embeddings.
- Never modify `classification_data/{week}.json` from Wiki code.
- Build only from `workflow_state=approved` and record the approved `active_run_id` and taxonomy version.
- Topic is the only canonical accumulated narrative. LOTCD and Team are projections; Week is a versioned snapshot.
- Every factual claim must reference valid Agenda IDs from the approved classification input.
- An ambiguous Agenda-to-Topic match creates a blocking review; a pending Topic relation is non-blocking.
- A failed Topic update keeps its previous valid revision current.
- Preserve the existing Classification Workbench and weekly/monthly report behavior.
- Keep external LLM transmission gated by `KNOWLEDGE_LLM_DATA_POLICY_ACK=true`.

---

## File structure

- Modify `agenda_extract.py`: change the default model only.
- Modify `knowledge_models.py`: preserve Wiki-required source metadata and define Topic Wiki API/store contracts.
- Modify `process_agendas.py`: store a repo-relative source path on `Mail`.
- Modify `classification_store.py`: persist topic/state hints and source metadata; expose an approved-week read method.
- Create `wiki_store.py`: atomic JSON persistence, lock, recovery, catalog rebuild, and revision publication.
- Create `topic_linker.py`: deterministic candidate retrieval and structured attach/create/review decisions.
- Create `topic_wiki_builder.py`: two-stage Topic update, evidence validation, relation proposals, and build orchestration.
- Create `wiki_projections.py`: deterministic LOTCD, Team, and Week views.
- Create `process_wiki.py`: explicit approved-week CLI.
- Modify `knowledge_api.py`: Wiki read, review, and build endpoints.
- Modify `knowledge_web.py`: SPA fallback routes for `/wiki/*`.
- Modify `run_pipeline.py`: optional post-approval Wiki hook only; default remains disabled.
- Create `tests/test_wiki_store.py`, `tests/test_topic_linker.py`, `tests/test_topic_wiki_builder.py`, and `tests/test_wiki_projections.py`.
- Modify `tests/test_agenda_extract.py`, `tests/test_json_classification_store.py`, `tests/test_process_agendas.py`, `tests/test_knowledge_api.py`, and `tests/test_knowledge_web.py`.
- Create `fixtures/wiki/approved-week.json` and `fixtures/wiki/expected-topic-links.json`.

### Task 1: Set GLM-5.2 and preserve Wiki source metadata

**Files:**
- Modify: `agenda_extract.py:278-318`
- Modify: `knowledge_models.py:50-145`
- Modify: `process_agendas.py:23-49`
- Modify: `classification_store.py:230-275,413-472`
- Modify: `tests/test_agenda_extract.py`
- Modify: `tests/test_process_agendas.py`
- Modify: `tests/test_json_classification_store.py`

**Interfaces:**
- Consumes: existing `Mail`, `ExtractedAgenda`, and `JsonClassificationStore.save_classified_extraction()`.
- Produces: `ClassificationItem.team`, `subject`, `received_at`, `source_path`, `topic_hint`, `state_hint`; `JsonClassificationStore.approved_week(week) -> WeekDocument`; `classification_item(agenda_id) -> tuple[str, ClassificationItem]`.

- [ ] **Step 1: Write failing model/default tests**

```python
def test_llm_connection_defaults_to_glm_52(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_LLM_BASE_URL", "http://localhost:8000/v1")
    for name in ("KNOWLEDGE_LLM_MODEL", "LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert llm_connection().model == "z-ai/glm-5.2"

# In the existing save_one() test helper, create the fixture Mail with:
# mail = mail.model_copy(update={"source_path": "data/2026-28/Spica수율/mail_001/combined.txt"})

def test_saved_item_preserves_wiki_source_metadata(tmp_path):
    store = make_store(tmp_path)
    _run, result, _summary = save_one(store)
    item = store.classification_items("2026-28")[0]
    assert item.team == "Spica수율"
    assert item.topic_hint == result.agendas[0].topic
    assert item.state_hint == result.agendas[0].state
    assert item.source_path.endswith("combined.txt")
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_agenda_extract.py::test_llm_connection_defaults_to_glm_52 tests/test_json_classification_store.py::test_saved_item_preserves_wiki_source_metadata -q`

Expected: the model assertion reports `z-ai/glm-4.7-flash`, and `ClassificationItem` has no `team` field.

- [ ] **Step 3: Add backward-compatible metadata fields**

```python
class Mail(StrictModel):
    id: str
    subject: str
    sender_team: str
    sender: str
    received_at: datetime
    body: str
    reply_to: str | None = None
    source_path: str | None = None


class ClassificationItem(StrictModel):
    agenda_id: str
    mail_id: str
    summary: str
    source_quote: str
    classification_context: str
    item_kind: ItemKind
    decision: ClassificationDecision
    revision_count: int
    team: str = "unknown"
    subject: str = ""
    received_at: datetime | None = None
    source_path: str | None = None
    topic_hint: str = ""
    state_hint: str = ""
```

In `process_agendas.mail_from_directory()` set:

```python
source_path=combined_path.resolve().relative_to(DATA_DIR.resolve().parent).as_posix(),
```

In `save_classified_extraction()` set the six metadata fields from `mail` and `agenda`. In `split_classification_item()` copy them from `original` to every child.

- [ ] **Step 4: Add an approved-week guard**

```python
def approved_week(self, week: str) -> WeekDocument:
    document = self._load_week(week)
    if document.workflow_state != "approved":
        raise ValueError(f"Week {week} is not approved")
    if not document.active_run_id:
        raise ValueError(f"Week {week} has no active run")
    return document.model_copy(deep=True)

def classification_item(self, agenda_id: str) -> tuple[str, ClassificationItem]:
    document, item = self._find_item(agenda_id)
    return document.week, item.model_copy(deep=True)
```

- [ ] **Step 5: Change the default model and run tests**

Change only the fallback in `llm_connection()`:

```python
model = (
    os.getenv("KNOWLEDGE_LLM_MODEL")
    or os.getenv("LLM_MODEL")
    or "z-ai/glm-5.2"
)
```

Run: `pytest tests/test_agenda_extract.py tests/test_process_agendas.py tests/test_json_classification_store.py -q`

Expected: all selected tests pass.

- [ ] **Step 6: Commit**

```bash
git add agenda_extract.py knowledge_models.py process_agendas.py classification_store.py tests/test_agenda_extract.py tests/test_process_agendas.py tests/test_json_classification_store.py
git commit -m "feat(classification): preserve wiki source metadata"
```

### Task 2: Define strict Topic Wiki contracts

**Files:**
- Modify: `knowledge_models.py`
- Create: `tests/test_topic_wiki_models.py`

**Interfaces:**
- Consumes: `CategoryPath`, `ClassificationItem`, `StrictModel`.
- Produces: `WikiTopic`, `TopicRevision`, `TopicAssignment`, `TopicRelation`, `WikiReview`, `WikiBuildRun`, `LotcdWikiView`, `TeamWikiView`, `WeekWikiView`, and request/response models used by all later tasks.

- [ ] **Step 1: Write strict contract tests**

```python
def test_supported_claim_requires_agenda_evidence():
    with pytest.raises(ValidationError):
        SupportedClaim(text="4SA 불량이 감소했다", agenda_ids=[])


def test_topic_keeps_one_canonical_revision_pointer():
    topic = WikiTopic(
        topic_id="T-001", title="4SA D1 불량", topic_kind="issue",
        primary_area="yield_defect", state="monitoring", importance="high",
        first_seen_week="2026-W29", last_updated_week="2026-W30",
        target_paths=[CategoryPath(domain="DRAM", tech="Spica", lotcd="4SA")],
        teams=["Yield"], source_agenda_ids=["A-001"], current_revision_id="REV-002",
    )
    assert topic.current_revision_id == "REV-002"
```

- [ ] **Step 2: Verify the tests fail**

Run: `pytest tests/test_topic_wiki_models.py -q`

Expected: collection fails because the Topic Wiki models do not exist.

- [ ] **Step 3: Add exact enums and core models**

```python
DomainName = Literal["DRAM", "NAND"]
TopicKind = Literal["issue", "observation", "change", "experiment", "action", "decision", "plan", "knowledge"]
KnowledgeArea = Literal["yield_defect", "process_equipment", "quality_analysis", "experiment_validation", "product_production", "schedule_delivery", "decision_action", "other"]
TopicState = Literal["new", "investigating", "action_in_progress", "monitoring", "resolved", "reopened", "closed", "review_required"]
RelationKind = Literal["possible_cause", "affects", "measurement_effect", "comparison", "follow_up", "supports", "contradicts", "shares_condition"]


class SupportedClaim(StrictModel):
    text: str = Field(min_length=1)
    agenda_ids: list[str] = Field(min_length=1)

class TopicSection(StrictModel):
    key: Literal["current_state", "observations", "cause_and_impact", "actions_and_decisions", "lotcd_differences", "team_contributions", "related_topics", "open_questions", "timeline"]
    title: str
    body: str


class WikiTopic(StrictModel):
    topic_id: str
    title: str
    topic_kind: TopicKind
    primary_area: KnowledgeArea
    secondary_areas: list[KnowledgeArea] = Field(default_factory=list)
    state: TopicState
    importance: Literal["low", "medium", "high", "critical"]
    first_seen_week: str
    last_updated_week: str
    target_paths: list[CategoryPath]
    teams: list[str]
    source_agenda_ids: list[str]
    related_topic_ids: list[str] = Field(default_factory=list)
    current_revision_id: str
```

Add these models with the exact fields below:

```python
class TopicRevision(StrictModel):
    revision_id: str; topic_id: str; week: str; body_markdown: str
    sections: list[TopicSection]
    claims: list[SupportedClaim]; source_agenda_ids: list[str]
    created_at: datetime; model: str

class TopicAssignment(StrictModel):
    agenda_id: str; topic_id: str
    decision: Literal["attach", "create"]
    confidence: float = Field(ge=0, le=1)
    rationale: str; decision_source: Literal["auto", "manual"]
    decided_by: str; decided_at: datetime

class TopicCandidate(StrictModel):
    topic_id: str
    score: float
    rank_reasons: list[str] = Field(default_factory=list)

class TopicRelation(StrictModel):
    relation_id: str; source_topic_id: str; target_topic_id: str
    kind: RelationKind; agenda_ids: list[str]; confidence: float
    review_state: Literal["pending", "accepted", "rejected"]

class WikiReview(StrictModel):
    review_id: str; kind: Literal["assignment", "relation"]
    agenda_id: str | None = None; candidates: list[TopicCandidate] = Field(default_factory=list)
    relation_id: str | None = None
    rationale: str = ""
    status: Literal["pending", "resolved", "held"] = "pending"
```

- [ ] **Step 4: Add build and projection contracts**

```python
class TopicListItem(StrictModel):
    topic_id: str
    title: str
    state: TopicState
    importance: Literal["low", "medium", "high", "critical"]
    primary_area: KnowledgeArea
    target_paths: list[CategoryPath]
    teams: list[str]
    last_updated_week: str
    evidence_count: int
    rank_reasons: list[str] = Field(default_factory=list)


class WikiEvidence(StrictModel):
    agenda_id: str
    mail_id: str
    team: str
    week: str
    subject: str
    source_quote: str
    source_path: str | None = None


class WikiTopicDetail(StrictModel):
    topic: WikiTopic
    body_markdown: str
    sections: list[TopicSection]
    claims: list[SupportedClaim]
    evidence: list[WikiEvidence]
    relations: list[TopicRelation]


class LotcdWikiView(StrictModel):
    domain: Literal["DRAM", "NAND"]
    tech: str
    lotcd: str
    summary: str
    recent_changes: list[TopicListItem]
    active_topics: list[TopicListItem]
    knowledge_areas: dict[KnowledgeArea, list[TopicListItem]]
    actions_and_decisions: list[TopicListItem]
    related_lotcds: list[str]
    closed_topics: dict[str, list[TopicListItem]]
    activity: list[WikiEvidence]
    topic_ids: list[str]


class TeamWikiView(StrictModel):
    team: str
    topics: list[TopicListItem]
    topic_ids: list[str]
    recent_activity: list[WikiEvidence]
    partner_teams: list[str]
    target_paths: list[CategoryPath]
    actions_and_decisions: list[TopicListItem]


class WeekWikiView(StrictModel):
    week: str
    revision_id: str
    published_at: datetime
    build_run_id: str
    new_topic_ids: list[str]
    changed_topic_ids: list[str]
    resolved_topic_ids: list[str]
    reopened_topic_ids: list[str]
    new_relation_ids: list[str]
    pending_assignment_count: int
    contradictions: list[str]
    teams: list[str]


class WikiBuildRun(StrictModel):
    run_id: str
    week: str
    classification_run_id: str
    status: Literal["linking", "review_required", "generating", "validating", "published", "partially_failed", "failed"]
    input_hash: str
    model: str
    affected_topic_ids: list[str] = Field(default_factory=list)
    failed_topic_ids: list[str] = Field(default_factory=list)
    started_at: datetime
    completed_at: datetime | None = None
    error: str | None = None


class WikiReviewResolution(StrictModel):
    action: Literal["attach", "create", "hold", "accept", "reject"]
    topic_id: str | None = None
    title: str | None = None
```

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_topic_wiki_models.py -q`

Expected: all tests pass.

```bash
git add knowledge_models.py tests/test_topic_wiki_models.py
git commit -m "feat(wiki): add topic wiki contracts"
```

### Task 3: Add the atomic JSON Wiki store

**Files:**
- Create: `wiki_store.py`
- Create: `tests/test_wiki_store.py`

**Interfaces:**
- Consumes: Topic Wiki models from Task 2.
- Produces: `JsonWikiStore`, `WikiStoreLockError`, `rebuild_catalog()`, and atomic current/revision publication.

- [ ] **Step 1: Write failing persistence and recovery tests**

```python
def test_publish_revision_keeps_current_and_history(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    store.publish_topic(topic(), revision())
    assert store.topic("T-001").current_revision_id == "REV-001"
    assert store.topic_revision("T-001", "REV-001").topic_id == "T-001"


def test_second_build_lock_is_rejected(tmp_path):
    store = JsonWikiStore(tmp_path / "wiki_data")
    with store.build_lock():
        with pytest.raises(WikiStoreLockError):
            with store.build_lock():
                pass
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_wiki_store.py -q`

Expected: collection fails with `ModuleNotFoundError: wiki_store`.

- [ ] **Step 3: Implement validated atomic IO and lock**

```python
class JsonWikiStore:
    def __init__(self, root: Path = DEFAULT_WIKI_DATA_DIR):
        self.root = Path(root)
        for name in ("topics", "assignments", "relations", "reviews", "builds", "weeks", "history/topics", "history/weeks"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _atomic_write(path: Path, model: StrictModel) -> None:
        validated = type(model).model_validate(model.model_dump(mode="json"))
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(validated.model_dump_json(indent=2), encoding="utf-8")
        os.replace(temporary, path)

    @contextmanager
    def build_lock(self):
        lock = self.root / ".build.lock"
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise WikiStoreLockError("Wiki build is already running") from exc
        try:
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            yield
        finally:
            lock.unlink(missing_ok=True)
```

- [ ] **Step 4: Implement store methods and recovery**

Use one validated loader and the exact public methods below:

```python
def _load(path: Path, model_type):
    if not path.exists():
        raise KeyError(path.stem)
    return model_type.model_validate_json(path.read_text(encoding="utf-8"))

def topics(self) -> list[WikiTopic]:
    return sorted((_load(path, WikiTopic) for path in self.root.joinpath("topics").glob("*.json")), key=lambda item: item.topic_id)

def topic(self, topic_id: str) -> WikiTopic:
    return _load(self.root / "topics" / f"{_safe_id(topic_id)}.json", WikiTopic)

def topic_revision(self, topic_id: str, revision_id: str) -> TopicRevision:
    return _load(self.root / "history" / "topics" / _safe_id(topic_id) / f"{_safe_id(revision_id)}.json", TopicRevision)

def publish_topic(self, topic: WikiTopic, revision: TopicRevision) -> None:
    if topic.topic_id != revision.topic_id or topic.current_revision_id != revision.revision_id:
        raise ValueError("Topic and revision identity mismatch")
    self._atomic_write(self.root / "history" / "topics" / topic.topic_id / f"{revision.revision_id}.json", revision)
    self._atomic_write(self.root / "topics" / f"{topic.topic_id}.json", topic)

def assignment(self, agenda_id: str) -> TopicAssignment | None:
    path = self.root / "assignments" / f"{_safe_id(agenda_id)}.json"
    return _load(path, TopicAssignment) if path.exists() else None

def save_assignment(self, value: TopicAssignment) -> None:
    self._atomic_write(self.root / "assignments" / f"{value.agenda_id}.json", value)

def save_relation(self, value: TopicRelation) -> None:
    self._atomic_write(self.root / "relations" / f"{value.relation_id}.json", value)

def reviews(self, status: str | None = None) -> list[WikiReview]:
    values = [_load(path, WikiReview) for path in self.root.joinpath("reviews").glob("*.json")]
    return sorted((item for item in values if status is None or item.status == status), key=lambda item: item.review_id)

def save_review(self, value: WikiReview) -> None:
    self._atomic_write(self.root / "reviews" / f"{value.review_id}.json", value)

def save_build(self, value: WikiBuildRun) -> None:
    self._atomic_write(self.root / "builds" / f"{value.run_id}.json", value)

def build(self, run_id: str) -> WikiBuildRun:
    return _load(self.root / "builds" / f"{_safe_id(run_id)}.json", WikiBuildRun)

def successful_build(self, input_hash: str) -> WikiBuildRun | None:
    builds = (_load(path, WikiBuildRun) for path in self.root.joinpath("builds").glob("*.json"))
    return next((item for item in builds if item.input_hash == input_hash and item.status == "published"), None)

def assignment_digest(self) -> str:
    payload = [path.read_text(encoding="utf-8") for path in sorted(self.root.joinpath("assignments").glob("*.json"))]
    return hashlib.sha256("\n".join(payload).encode("utf-8")).hexdigest()

def start_build(self, week: str, classification_run_id: str, input_hash: str, model: str) -> WikiBuildRun:
    run = WikiBuildRun(run_id=uuid.uuid4().hex, week=week, classification_run_id=classification_run_id, status="linking", input_hash=input_hash, model=model, started_at=datetime.now(UTC))
    self.save_build(run)
    return run

def finish_build(self, run: WikiBuildRun, status: str, failed_topic_ids: list[str] | None = None) -> WikiBuildRun:
    finished = run.model_copy(update={"status": status, "failed_topic_ids": failed_topic_ids or [], "completed_at": datetime.now(UTC)})
    self.save_build(finished)
    return finished

def save_week(self, value: WeekWikiView) -> None:
    current = self.root / "weeks" / f"{value.week}.json"
    if current.exists():
        previous = _load(current, WeekWikiView)
        self._atomic_write(self.root / "history" / "weeks" / value.week / f"{previous.revision_id}.json", previous)
    self._atomic_write(current, value)

def week(self, week: str) -> WeekWikiView:
    return _load(self.root / "weeks" / f"{_safe_id(week)}.json", WeekWikiView)
```

`rebuild_catalog()` derives a JSON array from `topics()` and atomically replaces `catalog.json`. `recover_incomplete_builds()` loads build files, changes transient states older than the configured recovery threshold to `failed`, records `completed_at` and `error="interrupted build"`, and never alters Topic pointers.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_wiki_store.py -q`

Expected: all tests pass.

```bash
git add wiki_store.py tests/test_wiki_store.py
git commit -m "feat(wiki): add atomic JSON topic store"
```

### Task 4: Build deterministic Topic candidates and review decisions

**Files:**
- Create: `topic_linker.py`
- Create: `tests/test_topic_linker.py`
- Create: `fixtures/wiki/expected-topic-links.json`

**Interfaces:**
- Consumes: `ClassificationItem`, existing `WikiTopic` records, and a structured `DecisionFn`.
- Produces: `rank_topic_candidates()`, `link_agenda()`, `build_link_decider()`, `resolve_wiki_review()`, `TopicLinkProposal`, assignments, or blocking reviews.

- [ ] **Step 1: Write failing ranking guardrail tests**

```python
def test_same_lotcd_and_terms_rank_existing_topic_first():
    candidates = rank_topic_candidates(item("4SA D1 불량 감소", "4SA"), [topic("4SA D1 불량 증가", "4SA")])
    assert candidates[0].topic_id == "T-001"


def test_same_lotcd_alone_does_not_auto_attach():
    proposal = link_agenda(item("4SA 출하 일정", "4SA"), [topic("4SA D1 불량", "4SA")], fake_decider("attach"))
    assert proposal.action == "review"
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_topic_linker.py -q`

Expected: collection fails because `topic_linker` does not exist.

- [ ] **Step 3: Implement normalized lexical ranking**

```python
def rank_topic_candidates(item: ClassificationItem, topics: Sequence[WikiTopic], limit: int = 5) -> list[TopicCandidate]:
    item_tokens = _tokens(" ".join((item.summary, item.topic_hint, item.classification_context)))
    ranked = []
    for topic in topics:
        if not _taxonomy_compatible(item.decision.target_path, topic.target_paths):
            continue
        topic_tokens = _tokens(f"{topic.title} {topic.primary_area}")
        overlap = len(item_tokens & topic_tokens) / max(1, len(item_tokens | topic_tokens))
        path_bonus = 4.0 if item.decision.target_path in topic.target_paths else 0.0
        hint_bonus = 2.0 if item.topic_hint and item.topic_hint.casefold() in topic.title.casefold() else 0.0
        ranked.append(TopicCandidate(topic_id=topic.topic_id, score=path_bonus + hint_bonus + 4.0 * overlap))
    return sorted(ranked, key=lambda value: (-value.score, value.topic_id))[:limit]
```

- [ ] **Step 4: Implement attach/create/review policy**

Rules:

```text
no candidate score >= 2.0                         -> create
LLM says attach and top score >= 5.0 and margin>=2 -> attach
LLM says create with no identity contradiction     -> create
all other outcomes                                  -> review
aggregate item against LOTCD-specific candidate     -> review
```

The LLM sees only the Agenda and five candidates and returns strict `TopicLinkDecision(action, topic_id, title, rationale, confidence)`.

- [ ] **Step 5: Run gold fixture evaluation and commit**

Run: `pytest tests/test_topic_linker.py -q`

Expected: all tests pass, including zero incorrect auto-merges in the fixture.

```bash
git add topic_linker.py tests/test_topic_linker.py fixtures/wiki/expected-topic-links.json
git commit -m "feat(wiki): add reviewable topic linking"
```

### Task 5: Generate and validate cited Topic revisions

**Files:**
- Create: `topic_wiki_builder.py`
- Create: `tests/test_topic_wiki_builder.py`

**Interfaces:**
- Consumes: accepted `TopicAssignment` records, approved `ClassificationItem` evidence, previous Topic revision, `AnalysisFn`, and `DraftFn`.
- Produces: `TopicAnalysis`, `TopicDraft`, `build_analysis_fn()`, `build_draft_fn()`, `validate_topic_draft()`, and `build_topic_revision()`.

- [ ] **Step 1: Write failing citation and state tests**

```python
def test_uncited_factual_sentence_is_rejected():
    with pytest.raises(ValueError, match="uncited factual claim"):
        validate_topic_draft(draft(sections=[TopicSection(key="observations", title="관찰", body="4SA 수율이 하락했다.")]), {"A-001": item()})


def test_resolved_state_requires_terminal_evidence():
    with pytest.raises(ValueError, match="terminal evidence"):
        build_topic_revision(topic(state="investigating"), [item(state_hint="open")], analysis(state="resolved"), draft())
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_topic_wiki_builder.py -q`

Expected: collection fails because `topic_wiki_builder` does not exist.

- [ ] **Step 3: Add two structured LLM stages**

```python
class TopicAnalysis(StrictModel):
    title: str
    topic_kind: TopicKind
    primary_area: KnowledgeArea
    secondary_areas: list[KnowledgeArea]
    next_state: TopicState
    importance: Literal["low", "medium", "high", "critical"]
    claims: list[SupportedClaim]
    stale_claims: list[str]
    relation_proposals: list[RelationProposal]
    open_questions: list[str]


class TopicDraft(StrictModel):
    sections: list[TopicSection]
```

Create `build_wiki_llm()` from `agenda_extract.llm_connection()` and assert the returned connection model is recorded on every revision. Use `temperature=0` and `with_structured_output()` for both stages.

- [ ] **Step 4: Add deterministic validation**

Require citation syntax `[agenda:<agenda_id>]` inside section bodies. Extract every citation, reject missing Agenda IDs, reject claims outside compatible taxonomy paths, and require `state_hint` in `resolved|closed|completed|stable|positive|normal` for a resolved transition. Application code renders `body_markdown` deterministically from ordered `TopicSection.title` and `TopicSection.body`; the LLM does not return an arbitrary full document.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_topic_wiki_builder.py -q`

Expected: all tests pass.

```bash
git add topic_wiki_builder.py tests/test_topic_wiki_builder.py
git commit -m "feat(wiki): generate cited topic revisions"
```

### Task 6: Orchestrate approved-week builds and projections

**Files:**
- Modify: `topic_wiki_builder.py`
- Create: `wiki_projections.py`
- Create: `tests/test_wiki_projections.py`
- Create: `fixtures/wiki/approved-week.json`

**Interfaces:**
- Consumes: `JsonClassificationStore.approved_week()`, `JsonWikiStore`, linker and generator functions.
- Produces: `build_week()`, `list_topics()`, `build_topic_detail()`, `build_lotcd_view()`, `build_team_view()`, and versioned `build_week_view()`.

- [ ] **Step 1: Write failing orchestration tests**

```python
def test_unapproved_week_cannot_build(stores):
    with pytest.raises(ValueError, match="not approved"):
        build_week("2026-W30", *stores, fake_link_decider, fake_analysis, fake_draft)


def test_four_views_share_canonical_topic_ids(fixture_store):
    lotcd = build_lotcd_view(fixture_store, "DRAM", "Spica", "4SA")
    team = build_team_view(fixture_store, "Yield")
    week = build_week_view(fixture_store, "2026-W30")
    assert "T-001" in lotcd.topic_ids
    assert "T-001" in team.topic_ids
    assert "T-001" in week.changed_topic_ids
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_topic_wiki_builder.py::test_unapproved_week_cannot_build tests/test_wiki_projections.py -q`

Expected: missing functions fail collection.

- [ ] **Step 3: Implement idempotent build orchestration**

```python
TOPIC_PROMPT_VERSION = "topic-wiki-v1"
WIKI_BUILDER_VERSION = "json-wiki-v1"

def build_week(week, classification_store, wiki_store, link_decider, analysis_fn, draft_fn) -> WikiBuildRun:
    document = classification_store.approved_week(week)
    model = llm_connection().model
    with wiki_store.build_lock():
        eligible = [item for item in document.items.values() if item.decision.status in {"confirmed", "manually_corrected", "aggregate"}]
        for item in eligible:
            if wiki_store.assignment(item.agenda_id) is not None:
                continue
            proposal = link_agenda(item, wiki_store.topics(), link_decider)
            persist_link_proposal(wiki_store, item, proposal)
        input_hash = build_input_hash(
            document,
            taxonomy_version=classification_store.taxonomy.version,
            assignment_digest=wiki_store.assignment_digest(),
            prompt_version=TOPIC_PROMPT_VERSION,
            builder_version=WIKI_BUILDER_VERSION,
            model=model,
        )
        if prior := wiki_store.successful_build(input_hash):
            return prior
        run = wiki_store.start_build(week, document.active_run_id, input_hash, model)
        eligible_ids = {item.agenda_id for item in eligible}
        if any(review.kind == "assignment" and review.agenda_id in eligible_ids for review in wiki_store.reviews("pending")):
            return wiki_store.finish_build(run, status="review_required")
        grouped = group_assigned_items(eligible, wiki_store)
        failed = []
        for topic_id, items in grouped.items():
            try:
                topic, revision, relations = build_topic_revision(topic_id, week, items, wiki_store, analysis_fn, draft_fn)
                wiki_store.publish_topic(topic, revision)
                for relation in relations:
                    wiki_store.save_relation(relation)
            except Exception:
                failed.append(topic_id)
        wiki_store.rebuild_catalog()
        wiki_store.save_week(build_week_view(wiki_store, week, run.run_id))
        status = "partially_failed" if failed else "published"
        return wiki_store.finish_build(run, status=status, failed_topic_ids=failed)
```

Exclude `unclassified`, `conflict`, `review_required`, and `excluded`; accept `confirmed`, `manually_corrected`, and scoped `aggregate` items.

- [ ] **Step 4: Implement fixed LOTCD table of contents**

Return sections in exact order:

```python
LOTCD_SECTION_ORDER = (
    "summary", "recent_changes", "active_topics", "knowledge_areas",
    "actions_and_decisions", "related_lotcds", "closed_topics", "activity",
)
```

Group each Topic once by `primary_area`; rank by importance, unresolved state, recent change, team count, evidence count, and accepted relation count. Return the rank reasons.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_topic_wiki_builder.py tests/test_wiki_projections.py -q`

Expected: all tests pass.

```bash
git add topic_wiki_builder.py wiki_projections.py tests/test_topic_wiki_builder.py tests/test_wiki_projections.py fixtures/wiki/approved-week.json
git commit -m "feat(wiki): build approved topic wiki views"
```

### Task 7: Expose Wiki APIs and explicit CLI

**Files:**
- Create: `process_wiki.py`
- Modify: `knowledge_api.py`
- Modify: `tests/test_knowledge_api.py`
- Modify: `run_pipeline.py`

**Interfaces:**
- Consumes: stores and builders from Tasks 1-6.
- Produces: stable `/api/knowledge/wiki/*` endpoints and `python process_wiki.py --week YYYY-WNN`.

- [ ] **Step 1: Write failing API permission and route tests**

```python
def test_topic_and_lotcd_routes_read_same_topic(api_app):
    topic = request(api_app, "/api/knowledge/wiki/topics/T-001").json()
    lotcd = request(api_app, "/api/knowledge/wiki/lotcd/DRAM/Spica/4SA").json()
    assert topic["topic_id"] in lotcd["topic_ids"]


def test_build_requires_editor_and_approved_week(api_app):
    response = post(api_app, "/api/knowledge/wiki/builds/2026-W30")
    assert response.status_code in {403, 409}
```

- [ ] **Step 2: Verify tests fail**

Run: `pytest tests/test_knowledge_api.py -q`

Expected: new Wiki routes return 404.

- [ ] **Step 3: Add cached Wiki store dependency and read routes**

```python
@lru_cache(maxsize=1)
def get_wiki_store() -> JsonWikiStore:
    return JsonWikiStore(Path(os.getenv("WIKI_DATA_DIR", "wiki_data")))

@router.get("/wiki/topics", response_model=list[TopicListItem])
def wiki_topics(q: str | None = None, state: TopicState | None = None, area: KnowledgeArea | None = None, team: str | None = None, lotcd: str | None = None) -> list[TopicListItem]:
    return list_topics(get_wiki_store(), q=q, state=state, area=area, team=team, lotcd=lotcd)

@router.get("/wiki/topics/{topic_id}", response_model=WikiTopicDetail)
def wiki_topic(topic_id: str) -> WikiTopicDetail:
    try:
        return build_topic_detail(get_wiki_store(), get_store(), topic_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown Topic: {topic_id}") from exc

@router.get("/wiki/lotcd/{domain}/{tech}/{lotcd}", response_model=LotcdWikiView)
def wiki_lotcd(domain: DomainName, tech: str, lotcd: str) -> LotcdWikiView:
    try:
        return build_lotcd_view(get_wiki_store(), get_store(), domain, tech, lotcd)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown LOTCD: {domain}/{tech}/{lotcd}") from exc

@router.get("/wiki/teams/{team}", response_model=TeamWikiView)
def wiki_team(team: str) -> TeamWikiView:
    return build_team_view(get_wiki_store(), get_store(), team)

@router.get("/wiki/weeks/{week}", response_model=WeekWikiView)
def wiki_week(week: str) -> WeekWikiView:
    try:
        return get_wiki_store().week(week)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Unknown Wiki week: {week}") from exc

@router.get("/wiki/reviews", response_model=list[WikiReview])
def wiki_reviews(status: str | None = "pending") -> list[WikiReview]:
    return get_wiki_store().reviews(status)

@router.post("/wiki/reviews/{review_id}/resolve", response_model=WikiReview)
def resolve_review(review_id: str, resolution: WikiReviewResolution, user: UserContext = Depends(require_editor)) -> WikiReview:
    return resolve_wiki_review(get_wiki_store(), review_id, resolution, user.user_id)

@router.post("/wiki/builds/{week}", response_model=WikiBuildRun)
def start_wiki_build(week: str, user: UserContext = Depends(require_editor)) -> WikiBuildRun:
    return build_week(week, get_store(), get_wiki_store(), build_link_decider(), build_analysis_fn(), build_draft_fn())
```

Add `GET /wiki/builds/{run_id}` using `JsonWikiStore.build(run_id)` and return 404 on missing IDs. The review and build mutations depend on `require_editor`; all read routes retain the router's authentication dependency.

- [ ] **Step 4: Add the explicit CLI and disabled pipeline hook**

`process_wiki.py` accepts `--week`, `--classification-data-dir`, `--rules`, `--wiki-data-dir`, and `--allow-external-llm`; it rejects missing policy acknowledgement through the shared LLM connection.

In `run_pipeline.py`, do not auto-build by default. Only invoke the CLI-equivalent function when `ENABLE_TOPIC_WIKI=true`; an unapproved week logs `Wiki build skipped: week not approved` rather than corrupting or auto-approving classification.

- [ ] **Step 5: Run tests and commit**

Run: `pytest tests/test_knowledge_api.py tests/test_process_agendas.py tests/test_topic_wiki_builder.py -q`

Expected: all tests pass.

```bash
git add process_wiki.py knowledge_api.py run_pipeline.py tests/test_knowledge_api.py
git commit -m "feat(wiki): expose topic wiki API and CLI"
```

### Task 8: Add SPA routes, recovery verification, and operator docs

**Files:**
- Modify: `knowledge_web.py`
- Modify: `tests/test_knowledge_web.py`
- Create: `docs/topic_wiki_operations.md`

**Interfaces:**
- Consumes: backend routes and `web/dist/index.html`.
- Produces: direct-browser support for all `/wiki/*` routes and a reproducible operator runbook.

- [ ] **Step 1: Write failing SPA fallback tests**

```python
def test_topic_wiki_deep_links_serve_spa(tmp_path):
    app = FastAPI()
    assert mount_knowledge_web(app, build_dist(tmp_path))
    for path in ("/wiki/topics/T-001", "/wiki/lotcd/DRAM/Spica/4SA", "/wiki/teams/Yield", "/wiki/weeks/2026-W30"):
        assert request(app, path).status_code == 200
```

- [ ] **Step 2: Verify test fails**

Run: `pytest tests/test_knowledge_web.py::test_topic_wiki_deep_links_serve_spa -q`

Expected: deep links return 404.

- [ ] **Step 3: Add exact catch-all routes and operations document**

After the existing `/classification` route, register:

```python
app.add_api_route(
    "/wiki",
    serve_index,
    methods=["GET"],
    include_in_schema=False,
    name="knowledge-wiki-root",
)
app.add_api_route(
    "/wiki/{path:path}",
    serve_index,
    methods=["GET"],
    include_in_schema=False,
    name="knowledge-wiki-route",
)
```

Document:

```bash
export KNOWLEDGE_LLM_MODEL='z-ai/glm-5.2'
export KNOWLEDGE_LLM_DATA_POLICY_ACK='true'
python process_wiki.py --week 2026-W30 --allow-external-llm
pytest tests/test_topic_wiki_models.py tests/test_wiki_store.py tests/test_topic_linker.py tests/test_topic_wiki_builder.py tests/test_wiki_projections.py tests/test_knowledge_api.py -q
```

- [ ] **Step 4: Run complete backend verification**

Run: `pytest -q`

Expected: all Python tests pass.

Run: `git status --short`

Expected: only Task 8 files are modified before commit.

- [ ] **Step 5: Commit**

```bash
git add knowledge_web.py tests/test_knowledge_web.py docs/topic_wiki_operations.md
git commit -m "docs(wiki): add topic wiki operations"
```

## Backend completion gate

Before starting the Web plan, verify:

```bash
pytest -q
python -m compileall agenda_extract.py classification_store.py wiki_store.py topic_linker.py topic_wiki_builder.py wiki_projections.py knowledge_api.py process_wiki.py
git status --short
```

Expected: tests and compilation pass, and the worktree is clean.
