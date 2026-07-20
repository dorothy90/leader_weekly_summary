# Wiki Graph Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a seeded, document-centered weekly-report Wiki with a persistent Topic/LOTCD/Team/Week tree, switchable Docs/Graph center pane, and canonical Topic document pane.

**Architecture:** Add one read-only graph endpoint over the existing JSON Wiki store, then seed valid production-model JSON from repository fixtures. Replace the dashboard routes with a shared `WikiWorkspacePage` that parses the current collection from the URL, fetches independent navigation data in parallel, lazy-loads a Cytoscape.js graph, and opens the same Topic document from either a Docs row or graph node.

**Tech Stack:** Python 3, Pydantic v2, FastAPI, React 19, React Router 7, TypeScript 7, Vite 8, Vitest, Testing Library, Cytoscape.js 3.34.

## Global Constraints

- Default LLM model is exactly `z-ai/glm-4.7-flash`; `KNOWLEDGE_LLM_MODEL` and `LLM_MODEL` overrides keep precedence.
- Demo generation is deterministic and offline and writes only JSON below explicit data directories.
- Topic, LOTCD, Team, and Week are metadata perspectives; Docs and Graph are center-pane views.
- The right pane is the single canonical narrative surface.
- Accepted relations are solid, pending relations are amber/dashed, and rejected relations are omitted.
- `/classification` behavior and styling are not changed.
- AI Chat, Deep Research, Wiki editing, automatic lint repair, settings, and project switching are excluded.
- Verification is intentionally minimal per user request: targeted tests, one Web production build, and one seeded desktop browser pass.

---

### Task 1: Graph API and default model

**Files:**
- Modify: `knowledge_models.py`
- Modify: `wiki_store.py`
- Modify: `knowledge_api.py`
- Modify: `agenda_extract.py`
- Modify: `README.md`
- Test: `tests/test_knowledge_api.py`
- Test: `tests/test_agenda_extract.py`

**Interfaces:**
- Produces: `WikiGraphView(topics: list[TopicListItem], relations: list[TopicRelation])`
- Produces: `JsonWikiStore.relations() -> list[TopicRelation]`
- Produces: `GET /api/knowledge/wiki/graph`

- [ ] **Step 1: Write failing API and model-default tests**

```python
def test_wiki_graph_returns_topics_and_non_rejected_relations(client, wiki_store):
    response = client.get("/api/knowledge/wiki/graph")
    assert response.status_code == 200
    payload = response.json()
    assert {item["topic_id"] for item in payload["topics"]} == {"T-001", "T-002"}
    assert [item["review_state"] for item in payload["relations"]] == ["accepted", "pending"]


def test_default_model_is_glm_flash(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_LLM_MODEL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    assert build_llm_config().model == "z-ai/glm-4.7-flash"
```

- [ ] **Step 2: Run tests and confirm the missing endpoint/default failure**

Run: `pytest -q tests/test_knowledge_api.py -k wiki_graph tests/test_agenda_extract.py -k default_model`

Expected: FAIL because `/wiki/graph` is 404 and the default still reports `z-ai/glm-5.2`.

- [ ] **Step 3: Add the minimal graph contract and endpoint**

```python
class WikiGraphView(StrictModel):
    topics: list[TopicListItem]
    relations: list[TopicRelation]


def relations(self) -> list[TopicRelation]:
    return sorted(
        (_load(path, TopicRelation) for path in self.root.joinpath("relations").glob("*.json")),
        key=lambda item: item.relation_id,
    )


@router.get("/wiki/graph", response_model=WikiGraphView)
def wiki_graph() -> WikiGraphView:
    store = get_wiki_store()
    return WikiGraphView(
        topics=list_topics(store),
        relations=[item for item in store.relations() if item.review_state != "rejected"],
    )
```

Change only the fallback model literal and matching README examples to `z-ai/glm-4.7-flash`; leave environment lookup order unchanged.

- [ ] **Step 4: Run targeted tests**

Run: `pytest -q tests/test_knowledge_api.py -k wiki_graph tests/test_agenda_extract.py -k 'default_model or model'`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add knowledge_models.py wiki_store.py knowledge_api.py agenda_extract.py README.md tests/test_knowledge_api.py tests/test_agenda_extract.py
git commit -m "feat(wiki): expose graph data"
```

### Task 2: Deterministic demo Wiki seed

**Files:**
- Create: `scripts/seed_demo_wiki.py`
- Create: `tests/test_seed_demo_wiki.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `seed_demo(wiki_data_dir: Path, classification_data_dir: Path, replace_demo: bool = False) -> DemoSeedSummary`
- Produces: `build_demo_dataset(mails: list[dict[str, object]]) -> DemoDataset`
- Produces: `validate_demo_destination(wiki_data_dir: Path, classification_data_dir: Path, replace_demo: bool) -> set[Path]`
- Produces: `write_validated_dataset(dataset: DemoDataset, wiki_data_dir: Path, classification_data_dir: Path, owned_paths: set[Path]) -> list[str]`
- Produces: `write_manifest(wiki_data_dir: Path, classification_data_dir: Path, generated_paths: list[str]) -> None`
- Produces: `.demo-wiki-manifest.json` containing every generated relative path.

- [ ] **Step 1: Write overwrite-safety and scenario tests**

```python
def test_seed_demo_builds_graph_and_evidence_scenarios(tmp_path):
    summary = seed_demo(tmp_path / "wiki", tmp_path / "classification")
    store = JsonWikiStore(tmp_path / "wiki")
    topics = store.topics()
    relations = store.relations()
    assert summary.topic_count >= 10
    assert len({team for topic in topics for team in topic.teams}) >= 4
    assert len(store.weeks()) >= 3
    assert any(len(topic.target_paths) > 1 for topic in topics)
    assert {topic.state for topic in topics} >= {"resolved", "reopened"}
    assert {relation.review_state for relation in relations} >= {"accepted", "pending"}
    assert any(not topic.related_topic_ids for topic in topics)


def test_seed_demo_refuses_unowned_files(tmp_path):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    (wiki / "keep.json").write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="non-demo files"):
        seed_demo(wiki, tmp_path / "classification", replace_demo=True)
```

- [ ] **Step 2: Run the seed tests and confirm import failure**

Run: `pytest -q tests/test_seed_demo_wiki.py`

Expected: FAIL because `scripts.seed_demo_wiki` does not exist.

- [ ] **Step 3: Implement the seed with production models**

The script must:

```python
@dataclass(frozen=True)
class DemoSeedSummary:
    topic_count: int
    relation_count: int
    week_count: int
    generated_paths: tuple[str, ...]


def seed_demo(
    wiki_data_dir: Path,
    classification_data_dir: Path,
    replace_demo: bool = False,
) -> DemoSeedSummary:
    owned_paths = validate_demo_destination(
        wiki_data_dir, classification_data_dir, replace_demo
    )
    dataset = build_demo_dataset(load_fixture_mails())
    generated_paths = write_validated_dataset(
        dataset, wiki_data_dir, classification_data_dir, owned_paths
    )
    write_manifest(wiki_data_dir, classification_data_dir, generated_paths)
    return DemoSeedSummary(
        topic_count=len(dataset.topics),
        relation_count=len(dataset.relations),
        week_count=len(dataset.weeks),
        generated_paths=tuple(sorted(generated_paths)),
    )
```

Use the fixture mail themes to create 12 stable `DEMO-TOPIC-*` Topics across weeks `2026-W28` through `2026-W30`, DRAM Spica/Canopus and NAND Heraion/Colosseum paths, at least four teams, eight accepted relations, one pending relation, one isolated Topic, one cross-LOTCD Topic, and a bridge Topic. All timestamps, IDs, ordering, and prose are fixed constants.

- [ ] **Step 4: Run tests and seed the active worktree stores**

Run: `pytest -q tests/test_seed_demo_wiki.py`

Expected: PASS.

Run:

```bash
python scripts/seed_demo_wiki.py \
  --wiki-data-dir wiki_data \
  --classification-data-dir classification_data/demo \
  --replace-demo
```

Expected: summary reports at least 10 Topics, 3 weeks, accepted and pending relations.

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_demo_wiki.py tests/test_seed_demo_wiki.py .gitignore wiki_data classification_data/demo
git commit -m "feat(wiki): seed linked demo knowledge"
```

### Task 3: Shared Wiki workspace and navigation

**Files:**
- Create: `web/src/pages/WikiWorkspacePage.tsx`
- Create: `web/src/components/wiki/WikiUtilityRail.tsx`
- Create: `web/src/components/wiki/KnowledgeTree.tsx`
- Create: `web/src/components/wiki/ResizablePane.tsx`
- Create: `web/src/components/wiki/useWikiCollection.ts`
- Create: `web/src/components/wiki/wikiLocation.ts`
- Modify: `web/src/AppRoutes.tsx`
- Modify: `web/src/components/WikiShell.tsx`
- Modify: `web/src/styles/wiki.css`
- Test: `web/src/pages/WikiWorkspacePage.test.tsx`
- Test: `web/src/components/WikiShell.test.tsx`

**Interfaces:**
- Produces: `parseWikiLocation(pathname: string, search: string) -> WikiLocationState`
- Produces: `useWikiCollection(collectionPath: string) -> WikiCollectionState`
- Produces: `WikiWorkspacePage` with landmarks `Wiki 도구`, `지식 탐색`, `Wiki 탐색`, `Wiki 문서`.

- [ ] **Step 1: Write failing workspace landmark and navigation tests**

```tsx
it('keeps metadata roots beside Docs and the canonical document', async () => {
  renderWorkspace('/wiki/topics')
  expect(await screen.findByRole('navigation', { name: 'Wiki 도구' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '지식 탐색' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Wiki 탐색' })).toBeInTheDocument()
  expect(screen.getByRole('article', { name: 'Wiki 문서' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Docs' })).toHaveAttribute('aria-pressed', 'true')
})

it('parses a Topic return collection without losing selection', () => {
  expect(parseWikiLocation(
    '/wiki/topics/DEMO-TOPIC-01',
    '?from=%2Fwiki%2Flotcd%2FDRAM%2FSpica%2F4SA',
  )).toMatchObject({
    topicId: 'DEMO-TOPIC-01',
    collectionPath: '/wiki/lotcd/DRAM/Spica/4SA',
  })
})
```

- [ ] **Step 2: Run the workspace tests and confirm missing components**

Run: `cd web && npm test -- WikiWorkspacePage.test.tsx WikiShell.test.tsx`

Expected: FAIL because `WikiWorkspacePage` and workspace landmarks do not exist.

- [ ] **Step 3: Implement route parsing, parallel tree loading, and pane persistence**

`WikiLocationState` is:

```ts
export interface WikiLocationState {
  collectionPath: string
  topicId: string | null
  kind: 'topics' | 'lotcd' | 'team' | 'week'
  view: 'docs' | 'graph'
}
```

`KnowledgeTree` starts independent taxonomy/team/week requests together with
`Promise.allSettled`. `ResizablePane` clamps the left pane to `190–360px`, the document
pane to `320–720px`, persists under `weekly-wiki:layout:v1`, and exposes keyboard buttons
for collapse/restore in addition to the mouse separator.

Replace the four top dashboard tabs with a 48px utility rail and the persistent tree.
Retain `/wiki/reviews` and `/classification` links. Lazy route `WikiWorkspacePage` for
topics, LOTCD, teams, and weeks; keep Review as its existing route.

- [ ] **Step 4: Run workspace tests**

Run: `cd web && npm test -- WikiWorkspacePage.test.tsx WikiShell.test.tsx AppRoutes.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/WikiWorkspacePage.tsx web/src/components/wiki web/src/AppRoutes.tsx web/src/components/WikiShell.tsx web/src/styles/wiki.css web/src/pages/WikiWorkspacePage.test.tsx web/src/components/WikiShell.test.tsx web/src/AppRoutes.test.tsx
git commit -m "feat(web): add persistent wiki workspace"
```

### Task 4: Docs explorer and canonical document pane

**Files:**
- Create: `web/src/components/wiki/CollectionExplorer.tsx`
- Create: `web/src/components/wiki/WikiDocumentPane.tsx`
- Create: `web/src/components/wiki/EvidenceTree.tsx`
- Modify: `web/src/pages/WikiWorkspacePage.tsx`
- Modify: `web/src/styles/wiki.css`
- Test: `web/src/components/wiki/CollectionExplorer.test.tsx`
- Test: `web/src/components/wiki/WikiDocumentPane.test.tsx`

**Interfaces:**
- Consumes: `WikiCollectionState` from Task 3.
- Produces: `CollectionExplorer({ collection, selectedTopicId, onSelectTopic })`.
- Produces: `WikiDocumentPane({ topicId, collection, onOpenEvidence })`.

- [ ] **Step 1: Write failing row-to-document and evidence tests**

```tsx
it('opens a collection row in the right document without losing the collection', async () => {
  renderWorkspace('/wiki/teams/Spica%EC%88%98%EC%9C%A8')
  fireEvent.click(await screen.findByRole('button', { name: '4SA chamber A 편차' }))
  expect(await screen.findByRole('heading', { name: '4SA chamber A 편차' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Wiki 탐색' })).toHaveTextContent('Spica수율')
})

it('shows TOC, backlinks, and immutable Agenda evidence', async () => {
  renderDocument('DEMO-TOPIC-01')
  expect(await screen.findByRole('navigation', { name: '문서 목차' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '연결된 주제' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: /Agenda 근거/ }))
  expect(screen.getByRole('dialog', { name: 'Agenda 근거' })).toBeInTheDocument()
})
```

- [ ] **Step 2: Run the component tests and confirm failure**

Run: `cd web && npm test -- CollectionExplorer.test.tsx WikiDocumentPane.test.tsx`

Expected: FAIL because the explorer and document pane do not exist.

- [ ] **Step 3: Implement compact Docs rows and the evidence-spine document**

The center pane renders one row per canonical Topic with title, state, importance,
updated week, teams, LOTCD path, evidence count, and one rank reason. A row click navigates
to `/wiki/topics/:id?from=:collectionPath`.

The right pane fetches `WikiTopicDetail`, renders metadata, a section-anchor TOC, section
body citation tokens, claims, accepted and pending relations, bidirectional related Topic
links, evidence archive metadata, and the existing `EvidenceDrawer`. When no Topic is
selected it renders a collection overview from the loaded Topic rows rather than blank
space. The continuous evidence spine is CSS, not duplicated content.

- [ ] **Step 4: Run focused Docs tests**

Run: `cd web && npm test -- CollectionExplorer.test.tsx WikiDocumentPane.test.tsx WikiWorkspacePage.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/wiki/CollectionExplorer.tsx web/src/components/wiki/WikiDocumentPane.tsx web/src/components/wiki/EvidenceTree.tsx web/src/pages/WikiWorkspacePage.tsx web/src/styles/wiki.css web/src/components/wiki/*.test.tsx
git commit -m "feat(web): add wiki docs workspace"
```

### Task 5: Lazy Wiki Graph, filters, and insights

**Files:**
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `web/src/types.ts`
- Modify: `web/src/api/knowledge.ts`
- Create: `web/src/components/wiki/graph/graphModel.ts`
- Create: `web/src/components/wiki/graph/WikiGraph.tsx`
- Create: `web/src/components/wiki/graph/GraphInsights.tsx`
- Create: `web/src/components/wiki/graph/GraphLegend.tsx`
- Modify: `web/src/pages/WikiWorkspacePage.tsx`
- Modify: `web/src/styles/wiki.css`
- Test: `web/src/api/knowledge.wiki.test.ts`
- Test: `web/src/components/wiki/graph/graphModel.test.ts`
- Test: `web/src/components/wiki/graph/WikiGraph.test.tsx`

**Interfaces:**
- Produces: `fetchWikiGraph(signal?: AbortSignal) -> Promise<WikiGraphView>`.
- Produces: `buildGraphModel(view: WikiGraphView, scopeIds: Set<string>) -> GraphModel`.
- Produces: `buildGraphInsights(model: GraphModel) -> GraphInsight[]`.
- Produces: lazy `WikiGraph` invoked only when `view=graph`.

- [ ] **Step 1: Install the renderer and write failing pure-model tests**

Run: `cd web && npm install cytoscape@3.34.0 && npm install -D @types/cytoscape`

Add tests:

```ts
it('keeps accepted and pending edges but omits rejected edges', () => {
  const model = buildGraphModel(graphFixture, new Set(['T-1', 'T-2']))
  expect(model.edges.map((edge) => [edge.id, edge.state])).toEqual([
    ['R-accepted', 'accepted'],
    ['R-pending', 'pending'],
  ])
})

it('finds isolated, bridge, cross-LOTCD, weak-evidence, and pending insights', () => {
  expect(buildGraphInsights(buildGraphModel(graphFixture, new Set())))
    .toEqual(expect.arrayContaining([
      expect.objectContaining({ kind: 'isolated' }),
      expect.objectContaining({ kind: 'bridge' }),
      expect.objectContaining({ kind: 'cross_lotcd' }),
      expect.objectContaining({ kind: 'evidence_gap' }),
      expect.objectContaining({ kind: 'pending_relation' }),
    ]))
})
```

- [ ] **Step 2: Run graph tests and confirm missing model failure**

Run: `cd web && npm test -- graphModel.test.ts WikiGraph.test.tsx knowledge.wiki.test.ts`

Expected: FAIL because the graph API and components do not exist.

- [ ] **Step 3: Implement graph model, Cytoscape lifecycle, and UI controls**

`WikiGraph` must dynamically import Cytoscape inside its effect, create one instance with
the built-in `cose` layout, and call `cy.destroy()` in cleanup. It must expose:

```tsx
<button onClick={() => cy.zoom(cy.zoom() * 1.2)} aria-label="확대" />
<button onClick={() => cy.zoom(cy.zoom() / 1.2)} aria-label="축소" />
<button onClick={() => cy.fit(undefined, 36)} aria-label="화면 맞춤" />
```

Controls include search, state/area filters, color mode (`area`, `state`, `community`),
counts, reload, legend collapse, and insights toggle. Node hover adds a `faded` class to
non-neighbors; node tap calls `onSelectTopic(id)`; edge tap opens a relation detail panel
with kind, confidence, review state, and Agenda IDs. Pending edge styling uses an amber
dashed line. A `ResizeObserver` calls `cy.resize()` and `cy.fit()` after pane changes.

Community IDs are deterministic connected-component IDs sorted by the smallest Topic ID.
Bridge detection uses an articulation-point DFS in `graphModel.ts`; no LLM call is used.

- [ ] **Step 4: Run graph and API tests**

Run: `cd web && npm test -- graphModel.test.ts WikiGraph.test.tsx knowledge.wiki.test.ts WikiWorkspacePage.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/package.json web/package-lock.json web/src/types.ts web/src/api/knowledge.ts web/src/components/wiki/graph web/src/pages/WikiWorkspacePage.tsx web/src/styles/wiki.css web/src/api/knowledge.wiki.test.ts
git commit -m "feat(web): add linked wiki graph"
```

### Task 6: Minimal build and browser verification

**Files:**
- Modify only files directly responsible for failures found below.

**Interfaces:**
- Consumes: seeded `wiki_data/`, FastAPI on port 8002, Vite on port 5174.
- Produces: visually verified desktop Wiki workspace.

- [ ] **Step 1: Run the agreed minimal verification set**

Run:

```bash
pytest -q tests/test_seed_demo_wiki.py tests/test_knowledge_api.py -k 'seed_demo or wiki_graph'
cd web && npm test -- WikiWorkspacePage.test.tsx WikiDocumentPane.test.tsx WikiGraph.test.tsx graphModel.test.ts
cd web && npm run build
```

Expected: all targeted tests pass and Vite creates `web/dist` without TypeScript errors.

- [ ] **Step 2: Restart the current servers with seeded directories**

Backend environment:

```bash
CLASSIFICATION_DATA_DIR=classification_data/demo \
WIKI_DATA_DIR=wiki_data \
uvicorn knowledge_web:create_app --factory --host 127.0.0.1 --port 8002
```

Frontend:

```bash
cd web && npm run dev -- --host 127.0.0.1 --port 5174
```

- [ ] **Step 3: Inspect one desktop flow in the browser**

Open `http://127.0.0.1:5174/wiki/topics` and verify:

1. Utility rail, metadata tree, Docs center, and overview document are visible without scrolling the viewport.
2. Selecting a Topic opens the right document while the center collection remains.
3. Switching to Graph retains that Topic; accepted and pending edge styles differ.
4. Selecting a graph node changes the right document.
5. LOTCD selection filters Docs and Graph.
6. No pane clips Korean labels at 1440×900.

- [ ] **Step 4: Fix only observed blockers and repeat build/browser check**

Run: `cd web && npm run build`

Expected: PASS and the six desktop checks above succeed.

- [ ] **Step 5: Commit final verification fixes**

```bash
git add -A
git commit -m "fix(web): polish wiki workspace"
```

Skip the final commit when no verification fixes were necessary.
