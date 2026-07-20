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

### A. Document-centered three-pane Wiki — selected

Left: knowledge tree. Center: pages in the selected collection. Right: canonical Wiki
document. This preserves the useful `nashsu/llm_wiki` three-column mental model while
matching this product's Topic/LOTCD/Team/Week data model.

### B. Reference-faithful Chat-centered workspace

Left: trees, center: AI chat, right: preview. This is valuable for question answering,
but chat, streaming, citations, and conversation persistence are outside the requested
weekly Wiki scope and would obscure the document-reading workflow.

### C. Obsidian-style single document with a collapsible sidebar

This is compact, but hides the relationship between a selected metadata collection and
its member Topics. It performs poorly when users compare many teams and weeks.

## Information architecture

The global shell is one persistent workspace rather than four dashboard tabs:

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Weekly Knowledge Wiki   search                         review  workbench    │
├───────────────┬──────────────────────┬──────────────────────────────────────┤
│ KNOWLEDGE TREE│ COLLECTION           │ WIKI PAGE                            │
│               │                      │                                      │
│ ▾ Topics      │ path / title         │ breadcrumbs · state · updated week   │
│   Yield       │ page row             │ title                                │
│   Process     │ page row             │ compact abstract                     │
│ ▾ LOTCD       │ page row             │ ┃ section                            │
│   DRAM        │                      │ ┃ section + [Agenda evidence]        │
│    Spica      │ recent changes       │ related Topics · source trail        │
│ ▾ Teams       │                      │                                      │
│ ▾ Weeks       │                      │                                      │
└───────────────┴──────────────────────┴──────────────────────────────────────┘
```

### Left pane: Knowledge Tree

- Four permanent roots: `주제`, `LOTCD`, `팀`, `주차`.
- Topic children group by controlled knowledge area.
- LOTCD children use `Domain → Tech → LOTCD` from the taxonomy endpoint.
- Team and Week children use their persisted Wiki index endpoints.
- Expansion is local UI state; selection is represented in the URL.
- The active path remains visible and keyboard reachable.

### Center pane: Collection Explorer

- Shows a concise title, item count, search field, and contextual facets.
- Topic rows contain title, state, importance, updated week, teams, paths, and one-line
  evidence summary. They do not contain duplicated Topic narrative.
- LOTCD, Team, and Week selections adapt the same explorer to their projection data.
- Selecting a row keeps the collection route in the `from` query and opens the Topic in
  the right pane.

### Right pane: Wiki Document

- When no Topic is selected, shows a generated overview document for the current
  collection rather than a blank state.
- A selected Topic shows breadcrumbs, title, abstract, table of contents, accumulated
  revision sections, inline Agenda citation buttons, relations, and source trail.
- The document is the only canonical narrative surface.
- Evidence opens in the existing modal drawer and restores focus to the citation.

### Responsive behavior

- `>= 1180px`: all three panes visible; left and center have bounded widths and the
  article owns remaining space.
- `760–1179px`: left tree becomes a slide-over; explorer and article remain split.
- `< 760px`: route-driven single pane with a compact back trail; no content is hidden.
- Pane resizing is not part of this release.

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
  immutable evidence archives, and versioned Week snapshots.
- Use realistic semiconductor weekly-report prose derived from dummy mail themes, but
  mark every generated record with deterministic `DEMO-` IDs.
- Do not invoke the LLM, network, embeddings, SQLite, or OpenSearch.

The running development server reads the generated `wiki_data/` through the existing
API, so the demo exercises production contracts rather than a frontend-only mock.

## Components and boundaries

- `WikiShell`: global header and responsive workspace frame.
- `KnowledgeTree`: metadata hierarchy and selection only.
- `CollectionExplorer`: collection header, search/facets, and reusable Topic rows.
- `WikiDocument`: canonical Topic or collection overview rendering.
- `EvidenceDrawer`: existing source-evidence modal, reused.
- Existing projection pages become route/data adapters; they do not each own a separate
  full-page visual system.
- Existing API contracts remain authoritative. Add only a small overview field or index
  endpoint if the current contract cannot render a pane without recomputation.

## Data flow

1. URL identifies mode, collection, and optional Topic.
2. Left pane loads taxonomy/team/week indexes in parallel.
3. Center pane loads the selected projection or Topic list.
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
  selection, `from` return context, overview fallback, evidence buttons, error states,
  and responsive navigation semantics.
- Preserve all Classification Workbench tests.
- Run full Python and Web suites plus the Vite production build.
- Inspect the seeded desktop and mobile pages in a real browser. Iterate on overflow,
  hierarchy, contrast, empty states, focus order, and reduced motion until the layout is
  visually coherent.

## Success criteria

1. Opening `/wiki/topics` with seeded data immediately looks and behaves like a Wiki,
   not a filter dashboard.
2. Users can navigate Topic, LOTCD, Team, and Week from one persistent tree.
3. Selecting any projected Topic opens the same canonical document in the right pane.
4. At least 10 realistic synthetic Topics demonstrate multiple teams, weeks, paths,
   states, relations, and evidence.
5. Inline citations open immutable source evidence.
6. The default model is `z-ai/glm-4.7-flash` with existing overrides and policy gates.
7. `/classification` remains unchanged and all existing tests pass.
8. Desktop and mobile browser inspection confirms readable hierarchy, no clipped text,
   visible focus, and appropriate contrast.

