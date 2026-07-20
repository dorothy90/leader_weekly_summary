# Three-Pane Topic Wiki Demo Design

## Goal

Turn the existing four-mode Topic Wiki Web from a filter dashboard into a readable,
interlinked Wiki. Seed deterministic synthetic data from the repository's dummy mail
fixtures so the running application immediately demonstrates Topic, LOTCD, Team, and
Week navigation without calling an LLM.

The default LLM model becomes exactly `z-ai/glm-4.7-flash`. Existing
`KNOWLEDGE_LLM_MODEL` and `LLM_MODEL` overrides and the external-data policy gate remain
unchanged.

## Audience and primary job

The audience is engineers and technical leaders reading weekly reports from more than
30 teams. The page's primary job is to move from a metadata path or recent change to a
canonical Topic document, read the accumulated explanation, and inspect its supporting
Agenda evidence without losing navigation context.

## Approaches considered

### A. Document-centered three-pane Wiki with linked graph — selected

Utility rail: Wiki, Evidence, Search, Graph, and Review. Left: knowledge/evidence tree.
Center: Docs or Graph for the selected collection. Right: canonical Wiki document. This
preserves the useful `nashsu/llm_wiki` navigation, graph, and preview flow while matching
this product's Topic/LOTCD/Team/Week data model.

### B. Reference-faithful Chat-centered workspace

Left: trees, center: AI chat, right: preview. This is valuable for question answering,
but chat, streaming, citations, and conversation persistence are outside the requested
weekly Wiki scope and would obscure the document-reading workflow.

### C. Obsidian-style single document with a collapsible sidebar

This is compact, but hides the relationship between a selected metadata collection and
its member Topics. It performs poorly when users compare many teams and weeks.

## Information architecture

The global shell is one persistent workspace rather than four dashboard tabs. Topic,
LOTCD, Team, and Week are metadata perspectives inside the knowledge tree; Docs and
Graph are the two center-pane views:

```text
┌────┬────────────────┬────────────────────────┬──────────────────────────────┐
│RAIL│ KNOWLEDGE TREE │ WIKI DOCS / WIKI GRAPH │ WIKI DOCUMENT                │
│    │ [지식] [근거]  │                        │                              │
│Wiki│ ▾ Topics       │ Docs: collection rows  │ breadcrumbs · state · week   │
│Src │ ▾ LOTCD        │ Graph: topic network   │ title · abstract · TOC       │
│Find│ ▾ Teams        │ search · filters       │ ┃ accumulated sections       │
│Graph│▾ Weeks        │ legend · insights      │ ┃ Agenda evidence            │
│Rev │ build activity │                        │ relations · backlinks        │
└────┴────────────────┴────────────────────────┴──────────────────────────────┘
```

### Left pane: Knowledge Tree

- A compact utility rail exposes Wiki, Evidence, Search, Graph, and Review; Graph opens
  the center graph without discarding the selected metadata path or Topic.
- The tree has `지식` and `근거` tabs. `근거` groups archived Agenda evidence by Week and
  Team and opens the existing evidence drawer.
- Four permanent roots: `주제`, `LOTCD`, `팀`, `주차`.
- Topic children group by controlled knowledge area.
- LOTCD children use `Domain → Tech → LOTCD` from the taxonomy endpoint.
- Team and Week children use their persisted Wiki index endpoints.
- Expansion is local UI state; selection is represented in the URL.
- The active path remains visible and keyboard reachable.

### Center pane: Wiki Docs

- Shows a concise title, item count, search field, and contextual facets.
- Topic rows contain title, state, importance, updated week, teams, paths, and one-line
  evidence summary. They do not contain duplicated Topic narrative.
- LOTCD, Team, and Week selections adapt the same explorer to their projection data.
- Selecting a row keeps the collection route in the `from` query and opens the Topic in
  the right pane.

### Center pane: Wiki Graph

- Docs and Graph are persistent center-view choices, not separate metadata modes.
- Topic nodes and typed `TopicRelation` edges use the production Wiki API data. Accepted
  edges are solid; pending edges are amber and dashed; rejected edges are omitted.
- Node size reflects connectivity and evidence count. Color can switch between knowledge
  area, Topic state, and deterministic graph community.
- Search, collection filters, node-type/state filters, zoom, fit, reload, a collapsible
  legend, and visible page/link counts are required controls.
- Hover focuses a node and its neighbors. Clicking a node opens the same canonical Topic
  in the right pane. Selecting an edge shows relation kind, review state, confidence, and
  Agenda evidence.
- Deterministic insights identify isolated Topics, bridge Topics, cross-LOTCD links,
  evidence-poor Topics, and pending relations. Insights highlight affected nodes and link
  to the existing Review surface when an operator decision is required.
- The graph is lazy-loaded. Use a graph renderer suitable for a year of weekly reports
  from more than 30 teams; do not implement the production view as a fixed-position SVG.

### Right pane: Wiki Document

- When no Topic is selected, shows a generated overview document for the current
  collection rather than a blank state.
- A selected Topic shows breadcrumbs, title, abstract, table of contents, accumulated
  revision sections, inline Agenda citation buttons, relations, backlinks, and source
  trail.
- The document is the only canonical narrative surface.
- Evidence opens in the existing modal drawer and restores focus to the citation.

### Responsive behavior

- `>= 1180px`: utility rail and all three panes are visible. Left and right panes are
  collapsible and mouse-resizable; the graph responds to size changes.
- `760–1179px`: left tree becomes a slide-over; explorer and article remain split.
- `< 760px`: route-driven single pane with a compact back trail; no content is hidden.
- Panel widths and collapsed state persist locally with a versioned storage key.

## Visual design

The visual language is a semiconductor process notebook: precise, calm, and traceable.
It avoids a generic analytics dashboard and avoids decorative cards.

### Tokens

- `Wafer paper` `#F7FAFB`: article surface.
- `Oxide field` `#E8EEF1`: navigation background.
- `Graphite` `#17212B`: primary text.
- `Etch teal` `#0C6B73`: active path and evidence links.
- `Signal blue` `#2B64D8`: selected Topic and cross-reference.
- `Review amber` `#A96512`: review and provisional state.

Typography uses `Pretendard/SUIT` for Korean body and headings, `IBM Plex Mono` for
Agenda IDs, weeks, revision IDs, and taxonomy paths. Heading scale is deliberately
compact so technical prose remains the focus.

### Signature element: evidence spine

The right document has one continuous vertical evidence spine. Each section and inline
Agenda citation connects to this spine with a small indexed node. It visually explains
that the Wiki is accumulated from weekly evidence, not a free-form generated article.
The spine is structural, not decorative, and collapses to a simple left rule on mobile.

Motion is limited to pane selection and evidence-drawer entry. It is disabled under
`prefers-reduced-motion`.

## Synthetic data

Create `scripts/seed_demo_wiki.py`, a deterministic, offline synthesizer.

- Input: `fixtures/knowledge/mails.json`, `fixtures/wiki/approved-week.json`, and
  `config/classification_rules.json`.
- Output: valid JSON under an explicit `--wiki-data-dir` and
  `--classification-data-dir`.
- Default demo output targets the repository's current empty `wiki_data/` and a
  dedicated `classification_data/demo/` only when explicitly invoked.
- Safety: refuse to overwrite a non-empty Wiki store unless `--replace-demo` is passed;
  replace only files marked by a demo manifest.
- Generate at least 10 Topics across 4 teams, 3 weeks, 2 Techs, and 5 LOTCD paths.
- Include one cross-LOTCD Topic, one resolved Topic, one reopened Topic, one pending
  relation, one blocking assignment review, accepted relations, a partial build status,
  immutable evidence archives, versioned Week snapshots, one isolated Topic, and one
  bridge Topic so every graph state is visible.
- Use realistic semiconductor weekly-report prose derived from dummy mail themes, but
  mark every generated record with deterministic `DEMO-` IDs.
- Do not invoke the LLM, network, embeddings, SQLite, or OpenSearch.

The running development server reads the generated `wiki_data/` through the existing
API, so the demo exercises production contracts rather than a frontend-only mock.

## Components and boundaries

- `WikiShell`: global header and responsive workspace frame.
- `WikiUtilityRail`: Wiki, Evidence, Search, Graph, Review, and build-status navigation.
- `KnowledgeTree`: metadata hierarchy and selection only.
- `CollectionExplorer`: collection header, search/facets, and reusable Topic rows.
- `WikiGraph`: lazy-loaded graph canvas, controls, legend, relation detail, and insights.
- `WikiDocument`: canonical Topic or collection overview rendering.
- `EvidenceDrawer`: existing source-evidence modal, reused.
- Existing projection pages become route/data adapters; they do not each own a separate
  full-page visual system.
- Existing API contracts remain authoritative. Add only a small overview field or index
  endpoint if the current contract cannot render a pane without recomputation.

## Data flow

1. URL identifies mode, collection, and optional Topic.
2. Left pane loads taxonomy/team/week indexes and evidence indexes in parallel.
3. Center Docs loads the selected projection or Topic list; Graph loads the same scoped
   Topic set and relation details without changing the collection URL.
4. Right pane loads canonical Topic detail or derives a deterministic collection
   overview from the projection response.
5. Inline citations resolve against immutable archived evidence through the backend.
6. Selecting another tree node cancels stale requests and updates all panes from the URL.

## Empty and error states

- A genuinely empty store explains how to run the demo seed command; it never presents
  an unexplained `0 TOPICS` dashboard.
- Pane-specific failures render an alert and retry action without removing already
  loaded neighboring panes.
- Missing Topic routes retain the selected collection and offer a return link.
- Review-required assignments appear as a count and an operator action, not as Topic
  prose.

## Testing and visual verification

- Python tests validate deterministic output, strict-model loading, overwrite safety,
  cross-LOTCD data, evidence archives, and Week history.
- React tests validate three-pane landmarks, tree navigation, collection-to-Topic
  selection, Docs/Graph switching, graph filtering and node-to-document selection,
  relation styling, insight generation, `from` return context, overview fallback,
  evidence buttons, panel persistence, error states, and responsive navigation semantics.
- Preserve all Classification Workbench tests.
- Run full Python and Web suites plus the Vite production build.
- Inspect the seeded desktop and mobile pages in a real browser. Iterate on overflow,
  hierarchy, contrast, empty states, focus order, and reduced motion until the layout is
  visually coherent.

## Success criteria

1. Opening `/wiki/topics` with seeded data immediately looks and behaves like a Wiki,
   not a filter dashboard.
2. Users can navigate Topic, LOTCD, Team, and Week from one persistent tree.
3. Users can switch the center between Wiki Docs and Wiki Graph without losing the
   selected collection or Topic.
4. Selecting any projected row or graph node opens the same canonical document in the
   right pane.
5. At least 10 realistic synthetic Topics demonstrate multiple teams, weeks, paths,
   states, relations, and evidence.
6. Inline citations and graph relation evidence open immutable source evidence.
7. The default model is `z-ai/glm-4.7-flash` with existing overrides and policy gates.
8. `/classification` remains unchanged and all existing tests pass.
9. Desktop and mobile browser inspection confirms readable hierarchy, no clipped text,
   visible focus, and appropriate contrast.

## Explicit exclusions

AI Chat, Deep Research, generated-page editing, automatic Wiki lint repair, LLM settings,
and project switching are not part of this implementation. They are separate product
capabilities in the reference application and have no current weekly-report Wiki contract.
