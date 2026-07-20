# Content-First Roll-up Wiki Design

**Date:** 2026-07-20

**Status:** Approved design, pending written-spec review

**Scope:** Wiki projections, reading UI, graph integration, and source-mail references

## 1. Objective

Replace the current operations-dashboard-like Wiki surface with a quiet,
content-first reading experience inspired by Medium. Keep Topic, LOTCD, Team,
Week, evidence, and graph capabilities, but make the selected synthesized
document the visual center of the application.

The Wiki serves nearly one year of weekly reports from more than 30 teams. A
report may contain several unrelated Agendas, and an Agenda may be classified
at Domain, Tech, or LOTCD scope. The design must therefore synthesize documents
at every classified taxonomy level instead of treating Domain and Tech as
navigation-only folders.

This specification supersedes the three-pane collection-row design in
`2026-07-20-three-pane-topic-wiki-demo-design.md` where the two conflict. The
canonical Topic and immutable Agenda evidence model remain unchanged.

## 2. Decisions already approved

1. Use a Medium-like content-first document layout.
2. Show one Library mode at a time: Topic, LOTCD, Team, or Week.
3. In LOTCD mode, Domain, Tech, and LOTCD are all synthesized documents.
4. Domain and Tech documents use inclusive roll-up aggregation.
5. Separate directly classified evidence from descendant roll-up evidence.
6. Keep Docs and Graph as alternate center views.
7. Only Graph node selection opens a right-side document preview.
8. Every synthesized document provides inline citations and a reference section.
9. “View original mail” opens a safe HTML mail viewer in a new browser tab.
10. Keep JSON as the authoritative application store; do not reintroduce SQLite
    or OpenSearch for this work.

## 3. Approaches considered

### 3.1 Restyle only

Change CSS and leave the current APIs and projections intact. This is quick but
cannot represent Domain and Tech documents, distinguish direct and roll-up
evidence, or open source mail HTML reliably.

### 3.2 JSON projections plus content-first UI — selected

Keep canonical Topic and Agenda records once, extend deterministic JSON
projections to Domain, Tech, LOTCD, Team, and Week, and render those projections
as articles. Summaries are produced during the approved Wiki build and cached;
opening a page does not call an LLM.

This approach keeps reads fast, prevents projection drift, and makes provenance
auditable.

### 3.3 Regenerate every page with an LLM on every read

This can produce fluid prose but adds latency and cost, changes output between
reads, and makes citations harder to validate. It is rejected.

## 4. Information architecture

```text
Weekly Wiki                              Docs · Graph · Search
─────────────────────────────────────────────────────────────
Library                    Selected synthesized document
[Topic LOTCD Team Week]

LOTCD mode
▾ DRAM                    Domain document
  ▾ Spica                 Tech document
      4SA                 LOTCD document
      4SB                 LOTCD document
  ▸ Heraion               Tech document
▸ NAND                    Domain document
```

The Library shows only the active mode. Switching modes changes only the
Library dataset; it does not replace the center document until the user selects
an item.

### 4.1 Topic mode

Shows canonical Topic documents, grouped or filtered by controlled knowledge
area. Selecting a Topic opens that Topic article in the center.

### 4.2 LOTCD mode

Shows a `Domain → Tech → LOTCD` hierarchy. Every label is a document link. A
separate caret expands or collapses children so opening a document and changing
tree expansion are unambiguous.

Routes are stable and encoded:

```text
/wiki/lotcd/{domain}
/wiki/lotcd/{domain}/{tech}
/wiki/lotcd/{domain}/{tech}/{lotcd}
```

### 4.3 Team mode

Shows one entry per contributing team. With 30 teams, this produces 30 Team
projection documents, not 30 entries inside the LOTCD document count.

### 4.4 Week mode

Shows one immutable projection per published Wiki week. A week document states
its build revision, classification run, model, and partial-failure status in a
quiet provenance section rather than a dashboard banner.

### 4.5 Docs and Graph

- Docs renders exactly one selected article in the center.
- Clicking an article wikilink navigates the center to the linked document.
- Graph replaces the center article with the knowledge graph.
- The Graph has no empty right panel.
- Clicking a Graph node keeps the Graph visible and opens that Topic document in
  a right preview panel.

## 5. Projection and roll-up semantics

`CategoryPath` remains hierarchical:

```yaml
domain: DRAM
tech: Spica       # nullable for Domain-level Agenda
lotcd: 4SA        # nullable for Domain- or Tech-level Agenda
```

### 5.1 Direct membership

- Domain-direct: `domain` matches and both `tech` and `lotcd` are null.
- Tech-direct: `domain` and `tech` match and `lotcd` is null.
- LOTCD-direct: all three path fields match.

### 5.2 Inclusive roll-up

- Domain document includes Domain-direct evidence plus every matching Tech and
  LOTCD descendant.
- Tech document includes Tech-direct evidence plus every matching LOTCD
  descendant.
- LOTCD document includes exact LOTCD evidence.
- Team and Week projections include their matching evidence regardless of
  taxonomy depth.

Evidence is deduplicated by immutable `agenda_id`. A Topic appears once in a
projection when at least one of its source Agendas belongs to the projection
scope. The projection records which source Agenda IDs were direct and which
were rolled up.

### 5.3 Document counts

If the taxonomy has 2 Domains, 10 Techs, and 50 LOTCDs, LOTCD mode has 62
synthesized documents. Thirty Team documents remain in Team mode.

### 5.4 Projection contract

Use one shared projection shape for Domain, Tech, LOTCD, Team, and Week:

```yaml
kind: domain | tech | lotcd | team | week
key: DRAM/Spica/4SA
title: 4SA 운영 로그
summary: "..."
direct_topic_ids: []
rolled_up_topic_ids: []
direct_evidence: []
rolled_up_evidence: []
knowledge_areas: {}
child_documents: []
related_documents: []
revision_id: REV-...
published_at: "..."
```

The build persists or deterministically derives this JSON from the latest valid
Topic revisions and approved immutable evidence. Failed Topic drafts do not
enter a projection; the last valid Topic revision remains visible.

## 6. Synthesized article structure

Projection documents use a common readable structure, with headings omitted
when there is no content:

1. Title, scope breadcrumb, short synthesized deck
2. Quiet metadata line: direct Topic count, roll-up count, teams, last update
3. Current summary
4. Directly classified findings
5. Descendant roll-up, grouped by immediate child document
6. Major Topics and changes
7. Knowledge areas
8. Connected Wiki documents
9. References
10. Build and revision provenance

Topic documents retain their validated narrative sections, claims, relations,
and evidence. They adopt the same typography and reference interactions.

## 7. Reference documents and original mail

Every factual statement remains traceable to immutable Agenda evidence.

### 7.1 In-article references

- Render compact inline citation buttons beside supported claims.
- Citation labels include team, week, and Agenda ID without exposing a local
  filesystem path.
- Clicking a citation opens the existing evidence drawer with subject, team,
  week, mail ID, exact quote, and classification scope.

### 7.2 Reference section

- Group large reference sets by Team, then Week.
- Separate `Directly classified` from `Rolled up from descendants`.
- Deduplicate repeated Agenda IDs.
- Keep groups collapsed by default when a projection contains many references.
- Show connected Wiki documents and backlinks in a separate section; they are
  not source evidence.

### 7.3 Source-mail data

Extend immutable evidence with source-mail metadata required for a safe lookup:

```yaml
mail_html_path: data/2026-W30/Spica수율/mail_001/body.html
source_start: 1240
source_end: 1318
source_quote: "..."
```

`mail_html_path` is stored internally and never returned as an arbitrary file
URL. Existing historical evidence without these fields remains readable but
shows original-mail navigation only when the mail can be resolved from
`week/team/mail_id`.

### 7.4 Safe new-tab viewer

Add an authenticated endpoint:

```text
GET /api/knowledge/evidence/{agenda_id}/mail
```

The endpoint resolves the Agenda through the classification/evidence store,
allows files only below the configured mail data root, and returns a sanitized
HTML wrapper. It must:

- remove scripts, event handlers, forms, iframes, and active embedded content;
- block remote network loads and allow local inline images only;
- apply a restrictive Content Security Policy;
- preserve readable original mail formatting;
- locate the normalized `source_quote` in mail text when possible;
- inject a stable Agenda anchor and highlight, then scroll to it on load;
- show a clear banner and the expected quote when exact positioning fails;
- return 404 for unknown Agenda or missing mail HTML without exposing paths.

The Wiki action uses `target="_blank"` and `rel="noopener noreferrer"`. Missing
HTML disables only `View original mail`; the evidence drawer still works.

## 8. Visual design

The interface uses the editorial restraint demonstrated by the
`awesome-design-md` references rather than copying a brand-specific marketing
surface.

### 8.1 Palette

- Paper `#FFFFFF`: primary article canvas
- Soft paper `#FAFAF8`: Library background
- Ink `#242424`: primary text
- Muted ink `#6B6B6B`: metadata
- Hairline `#E6E6E2`: structure
- Reference green `#1A8917`: links, citations, and active taxonomy path

Use no gradients, decorative shadows, colored status cards, or dark utility
rail. Graph colors remain semantic inside the graph only.

### 8.2 Typography

- Headings and navigation: restrained system sans stack suitable for Korean.
- Article prose: `Georgia`, `Noto Serif KR`, and system serif fallbacks.
- IDs and revisions: system monospace, used sparingly.
- Article width: approximately 680–760 px.
- Body: 17–19 px with 1.7–1.8 line height.

Typography and whitespace carry the hierarchy. Remove decorative numbered
section nodes because the document sections are not a sequence.

### 8.3 Layout and responsive behavior

- Desktop: quiet 220–260 px Library plus centered article.
- The Library is collapsible; its last width and open state may persist.
- Tablet: Library becomes an overlay.
- Mobile: Library becomes a modal sheet and the article uses a 20 px gutter.
- Keyboard focus is visible and all tree, citation, and mode controls are
  reachable without a pointer.
- Respect `prefers-reduced-motion`.

## 9. Components and boundaries

- `WikiShell`: minimal product header and application content boundary.
- `WikiLibrary`: active-mode selector and mode-specific navigation.
- `LotcdDocumentTree`: taxonomy expansion and document selection only.
- `WikiDocument`: common projection or Topic article frame.
- `ProjectionDocument`: Domain, Tech, LOTCD, Team, and Week sections.
- `TopicDocument`: validated canonical Topic narrative.
- `ReferenceList`: direct/roll-up groups, Team/Week grouping, and deduplication.
- `EvidenceDrawer`: quote and metadata preview.
- `SourceMailViewer`: backend-rendered safe HTML, opened in a new tab.
- `WikiGraph`: center graph view with optional right Topic preview.

Do not keep the current `CollectionExplorer` row browser in Docs mode. A Topic
list may appear as linked sections inside an article, but it is not the center
surface itself.

## 10. Data flow

1. The approved weekly classification build stores Agenda evidence and source
   mail metadata.
2. Topic linking and validation publish the latest valid Topic revisions.
3. The build refreshes Domain, Tech, LOTCD, Team, and Week projection JSON.
4. The URL identifies Library mode, selected document, and Docs or Graph view.
5. The active Library loads only its index and selected hierarchy.
6. Docs fetches one selected document and renders one center article.
7. Inline citations resolve immutable evidence without regenerating prose.
8. `View original mail` opens the authenticated sanitized HTML endpoint in a
   new tab.
9. Graph loads lazily; node selection loads a right-side Topic preview.

## 11. Empty and error behavior

- An empty mode explains that no published documents exist in that mode.
- A missing Domain/Tech/LOTCD returns a not-found article with a Library return
  link.
- Projection failures keep the last valid revision and expose stale provenance.
- A missing mail HTML file leaves the quote drawer available and disables the
  original-mail action with an explicit reason.
- A source quote that cannot be located opens the mail at the top with a
  non-blocking location warning.
- Failures in Graph or its preview do not remove the selected Docs route.

## 12. Testing and verification

### Backend

- Domain direct membership and descendant roll-up
- Tech direct membership and LOTCD roll-up
- LOTCD exact membership
- Agenda and Topic deduplication
- Team and Week projection preservation
- projection revision and partial-failure behavior
- authenticated source-mail access
- path traversal rejection
- HTML sanitization and restrictive CSP
- exact-quote highlight and no-match fallback
- missing historical HTML behavior

### Frontend

- only one Library mode is rendered at a time
- Domain, Tech, and LOTCD labels all navigate to documents
- caret expansion does not navigate
- Docs renders one center article and no right pane
- Topic links replace the center document
- direct and roll-up evidence groups remain distinct
- reference grouping and Agenda deduplication
- original-mail links open a new tab safely
- Graph occupies the center and opens a right preview only after node selection
- desktop, tablet, mobile, focus, and reduced-motion behavior

### Full verification

- run the entire Python test suite;
- run all Web tests;
- run TypeScript and Vite production build;
- run diff whitespace checks;
- inspect Topic, Domain, Tech, LOTCD, Team, Week, Graph, evidence drawer, and
  source-mail new-tab behavior in a real browser;
- confirm no browser console errors.

## 13. Success criteria

1. The Wiki reads as an article product, not an analytics dashboard.
2. Topic, LOTCD, Team, and Week are mutually exclusive Library modes.
3. Domain, Tech, and LOTCD each open a synthesized document.
4. Domain and Tech documents include correct, deduplicated descendant roll-up.
5. Direct and rolled-up content and evidence are visibly distinguishable.
6. Docs always presents one center document.
7. Graph replaces the center and uses the right pane only for node preview.
8. Every supported claim can open immutable evidence.
9. Every resolvable Agenda can open sanitized source mail HTML in a new tab,
   positioned and highlighted when possible.
10. Existing classification and Topic history integrity remain unchanged.
11. All automated and browser checks pass.

## 14. Explicit exclusions

- Chat and Deep Research
- editing generated Wiki prose in this phase
- arbitrary local file browsing
- remote images or scripts inside source mail HTML
- LLM calls on page read
- restoring legacy SQLite, OpenSearch, or Category Wiki storage
