# Integrated Narrative Wiki Web Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present each canonical Domain, Tech, or LOTCD page as a readable three-pane Wiki article with inline mail evidence, a synchronized outline, and collapsible lifetime weekly history.

**Architecture:** Consume the canonical page, page-summary, and citation-detail contracts from the backend plan. Keep the existing taxonomy rail and stable `/wiki/docs/...` routes, replace the generated page reader with structured current-body/history rendering, use a dedicated document context pane when no Agenda is selected, and open citations in an overlay drawer without navigating away.

**Tech Stack:** React 19, TypeScript, React Router, React Markdown, remark-gfm, Vite 8, Vitest, Testing Library, existing CSS token system.

## Global Constraints

- Preserve the existing `/wiki/docs/dram[/tech[/lotcd]]` routes.
- Keep Explorer, Mapping, Review, Graph, Agenda detail, and classification-edit behavior unchanged.
- Use the existing three-column shell and visual language from `wiki_frontend`; do not introduce a new UI library.
- Render prose as the primary content; use tables only when supplied in Markdown.
- Expand the latest weekly entry and collapse older entries by default.
- Resolve `[mail:<mail_id>]` through the page citation map and backend citation-detail endpoint.
- Keep older weekly history in the page data; UI collapsing must never delete it.
- At widths below the existing desktop breakpoint, hide the right context pane as the current CSS already does.

---

## File structure

- Modify `web/src/types.ts`: canonical page, summary, history, citation-map, and citation-detail types.
- Modify `web/src/api/knowledge.ts`: page-summary and citation-detail requests.
- Modify `web/src/components/CategoryWikiReader.tsx`: structured prose, heading IDs, inline mail buttons, and weekly disclosures.
- Modify `web/src/components/CategoryWikiReader.test.tsx`: reader interaction coverage.
- Create `web/src/components/WikiDocumentMetaPane.tsx`: outline, metrics, parent/child navigation.
- Create `web/src/components/WikiDocumentMetaPane.test.tsx`: context-pane coverage.
- Create `web/src/components/WikiCitationDrawer.tsx`: source mail and agenda evidence overlay.
- Create `web/src/components/WikiCitationDrawer.test.tsx`: drawer coverage.
- Modify `web/src/components/TaxonomyTree.tsx`: alias filtering and canonical pending counts.
- Create `web/src/components/TaxonomyTree.test.tsx`: filtering/count coverage.
- Modify `web/src/pages/ExplorerPage.tsx`: fetch and coordinate page summaries, outline, and citation drawer.
- Modify `web/src/styles/wiki.css`: approved reader, disclosure, context-pane, and drawer presentation.

### Task 1: Add canonical Wiki API types and clients

**Files:**
- Modify: `web/src/types.ts:130-151`
- Modify: `web/src/api/knowledge.ts:1-51`

**Interfaces:**
- Consumes: backend `CategoryWikiPage`, `WikiPageSummaryResponse`, and `WikiCitationDetail` JSON.
- Produces: TypeScript types, `fetchWikiPageSummaries`, and `fetchWikiCitation`.

- [ ] **Step 1: Extend the TypeScript contracts**

Replace the existing `CategoryWikiPage` interface and add the supporting interfaces:

```ts
export interface WeeklyHistoryRecord {
  week: string
  body_markdown: string
  source_mail_ids: string[]
}

export interface WikiCitationRecord {
  mail_id: string
  agenda_ids: string[]
  used_in_sections: string[]
  category_paths: string[]
}

export interface CategoryWikiPage {
  category_id: string
  page_kind: 'latest' | 'snapshot'
  doc_type: 'canonical' | 'snapshot'
  canonical_id: string
  level: 'domain' | 'tech' | 'lotcd'
  domain: DomainName
  tech: string | null
  lotcd: string | null
  title: string
  product: string | null
  fab_id: string | null
  aliases: string[]
  as_of_week: string
  current_body_markdown: string
  weekly_history: WeeklyHistoryRecord[]
  body_markdown: string
  citation_map: WikiCitationRecord[]
  child_page_ids: string[]
  confidence: 'low' | 'medium' | 'high'
  agenda_count: number
  open_issue_ids: string[]
  resolved_issue_ids: string[]
  open_issue_count: number
  resolved_issue_count: number
  contradictions: string[]
  generation_review_items: string[]
  review_agenda_ids: string[]
  source_agenda_ids: string[]
  source_doc_ids: string[]
  source_hash: string
  taxonomy_version: number
  generated_at: string
  updated_at: string | null
}

export interface WikiPageSummary {
  category_id: string
  canonical_id: string
  level: 'domain' | 'tech' | 'lotcd'
  domain: DomainName
  tech: string | null
  lotcd: string | null
  title: string
  as_of_week: string
  open_issue_count: number
  resolved_issue_count: number
  confidence: 'low' | 'medium' | 'high'
  review_item_count: number
}

export interface WikiPageSummaryResponse {
  items: WikiPageSummary[]
}

export interface WikiCitationDetail {
  mail: Mail
  agendas: Agenda[]
  used_in_sections: string[]
}
```

- [ ] **Step 2: Add API clients**

```ts
export function fetchWikiPageSummaries(
  signal?: AbortSignal,
): Promise<WikiPageSummaryResponse> {
  return getJson<WikiPageSummaryResponse>('/api/knowledge/wiki/pages', signal)
}

export function fetchWikiCitation(
  categoryId: string,
  mailId: string,
  signal?: AbortSignal,
): Promise<WikiCitationDetail> {
  const params = new URLSearchParams({ category_id: categoryId })
  return getJson<WikiCitationDetail>(
    `/api/knowledge/wiki/citations/${encodeURIComponent(mailId)}?${params}`,
    signal,
  )
}
```

Import the three new response types at the top of `knowledge.ts`.

- [ ] **Step 3: Run the TypeScript compiler**

Run: `cd web && npx tsc --noEmit`

Expected: existing page fixtures fail because newly required canonical fields are missing.

- [ ] **Step 4: Update existing `CategoryWikiPage` fixtures with explicit canonical defaults**

Add these fields to every frontend page fixture:

```ts
doc_type: 'canonical',
canonical_id: 'dram/spica/4sa',
aliases: ['SP LPDDR5 24G'],
current_body_markdown: '## 개요\n\n현재 상태 [mail:mail-1]',
weekly_history: [],
citation_map: [],
child_page_ids: [],
confidence: 'high',
open_issue_count: 1,
resolved_issue_count: 0,
contradictions: [],
generation_review_items: [],
updated_at: '2026-07-12T00:00:00Z',
```

- [ ] **Step 5: Verify types and commit**

Run: `cd web && npx tsc --noEmit`

Expected: exit code 0.

```bash
git add web/src/types.ts web/src/api/knowledge.ts web/src/components/CategoryWikiReader.test.tsx
git commit -m "feat(web): add canonical wiki contracts"
```

### Task 2: Render current narrative, inline citations, and weekly history

**Files:**
- Modify: `web/src/components/CategoryWikiReader.tsx`
- Modify: `web/src/components/CategoryWikiReader.test.tsx`

**Interfaces:**
- Consumes: structured `CategoryWikiPage`.
- Produces: `WikiHeading`, `onOutlineChange(headings)`, and `onSelectCitation(mailId)` interactions.

- [ ] **Step 1: Replace the old reader test with approved behavior tests**

```tsx
it('renders narrative sections and opens an inline mail citation', () => {
  const onSelectCitation = vi.fn()
  render(
    <CategoryWikiReader
      page={page}
      loading={false}
      error={null}
      onSelectCitation={onSelectCitation}
      onOutlineChange={vi.fn()}
    />,
  )
  expect(screen.getByRole('heading', { name: '개요' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'mail:mail-1' }))
  expect(onSelectCitation).toHaveBeenCalledWith('mail-1')
})

it('expands the newest week and collapses older weeks', () => {
  const historyPage = {
    ...page,
    weekly_history: [
      { week: '2026-W28', body_markdown: '이번 주 [mail:mail-1]', source_mail_ids: ['mail-1'] },
      { week: '2026-W27', body_markdown: '지난 주 [mail:mail-1]', source_mail_ids: ['mail-1'] },
    ],
  }
  const { container } = render(
    <CategoryWikiReader
      page={historyPage} loading={false} error={null}
      onSelectCitation={vi.fn()} onOutlineChange={vi.fn()}
    />,
  )
  const details = Array.from(container.querySelectorAll('details'))
  expect(details).toHaveLength(2)
  expect(details[0]).toHaveAttribute('open')
  expect(details[1]).not.toHaveAttribute('open')
})
```

- [ ] **Step 2: Run the reader test and verify prop/type failures**

Run: `cd web && npm test -- CategoryWikiReader.test.tsx`

Expected: FAIL because the new callback props and history rendering do not exist.

- [ ] **Step 3: Implement the structured reader**

Use these public types and helpers in `CategoryWikiReader.tsx`:

```tsx
export interface WikiHeading {
  id: string
  label: string
}

interface CategoryWikiReaderProps {
  page: CategoryWikiPage | null
  loading: boolean
  error: string | null
  onSelectCitation: (mailId: string) => void
  onOutlineChange: (headings: WikiHeading[]) => void
}

function citationMarkdown(markdown: string) {
  return markdown.replace(/\[mail:([^\]]+)\]/g, '[mail:$1](#mail:$1)')
}

function headingId(label: string) {
  return label.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^\p{L}\p{N}-]/gu, '')
}
```

Build `headings` with `useMemo` from the fixed six section labels plus `주차별 업데이트 이력` when history is non-empty, and call `onOutlineChange(headings)` in `useEffect`. Render `page.current_body_markdown || readerMarkdown(page.body_markdown)` through `ReactMarkdown`. Override `h2` to assign `id={headingId(String(children))}`. Override `a` so `#mail:` renders:

```tsx
<button
  className="wiki-mail-citation"
  type="button"
  onClick={() => onSelectCitation(href.slice('#mail:'.length))}
>
  {children}
</button>
```

Render history after the current body:

```tsx
{page.weekly_history.length ? (
  <section className="wiki-weekly-history" aria-labelledby="weekly-history-title">
    <h2 id="weekly-history-title">주차별 업데이트 이력</h2>
    {page.weekly_history.map((entry, index) => (
      <details open={index === 0} key={entry.week}>
        <summary>{entry.week}</summary>
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {citationMarkdown(entry.body_markdown)}
        </ReactMarkdown>
      </details>
    ))}
  </section>
) : null}
```

Remove the old `[[agenda-id]]` conversion and `onSelectAgenda` prop from this component only; Agenda selection remains available elsewhere.

- [ ] **Step 4: Run the reader test**

Run: `cd web && npm test -- CategoryWikiReader.test.tsx`

Expected: both new tests pass.

- [ ] **Step 5: Commit the reader**

```bash
git add web/src/components/CategoryWikiReader.tsx web/src/components/CategoryWikiReader.test.tsx
git commit -m "feat(web): render narrative wiki history"
```

### Task 3: Add the document context and outline pane

**Files:**
- Create: `web/src/components/WikiDocumentMetaPane.tsx`
- Create: `web/src/components/WikiDocumentMetaPane.test.tsx`

**Interfaces:**
- Consumes: current page, `WikiHeading[]`, and route navigation callback.
- Produces: right-pane outline, metrics, and parent/child links.

- [ ] **Step 1: Write the failing context-pane test**

```tsx
it('shows outline, page metrics, and related page navigation', () => {
  const onNavigate = vi.fn()
  render(
    <WikiDocumentMetaPane
      page={{ ...page, child_page_ids: ['dram/spica/4sa'] }}
      headings={[{ id: '개요', label: '개요' }]}
      onNavigate={onNavigate}
    />,
  )
  expect(screen.getByRole('link', { name: '개요' })).toHaveAttribute('href', '#개요')
  expect(screen.getByText('진행 이슈')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'dram/spica/4sa' }))
  expect(onNavigate).toHaveBeenCalledWith('dram/spica/4sa')
})
```

- [ ] **Step 2: Run and confirm missing component failure**

Run: `cd web && npm test -- WikiDocumentMetaPane.test.tsx`

Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement the context pane**

Create `WikiDocumentMetaPane` with this interface:

```tsx
interface WikiDocumentMetaPaneProps {
  page: CategoryWikiPage
  headings: WikiHeading[]
  onNavigate: (canonicalId: string) => void
}
```

Render an `<aside className="wiki-meta-pane wiki-document-meta">` containing:

- metrics: `open_issue_count`, `resolved_issue_count`, `citation_map.length`, and `as_of_week`;
- confidence and canonical path fields;
- outline anchors using `href={'#' + heading.id}`;
- a review section listing `contradictions` and `generation_review_items`, hidden when both are empty;
- a parent button derived by removing the final path segment when one exists;
- one button per `child_page_ids`.

The exact navigation button accessible name is the canonical ID text so the test and keyboard users can identify its destination.

- [ ] **Step 4: Run the component test**

Run: `cd web && npm test -- WikiDocumentMetaPane.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit the context pane**

```bash
git add web/src/components/WikiDocumentMetaPane.tsx web/src/components/WikiDocumentMetaPane.test.tsx
git commit -m "feat(web): add wiki document outline pane"
```

### Task 4: Add the inline mail evidence drawer

**Files:**
- Create: `web/src/components/WikiCitationDrawer.tsx`
- Create: `web/src/components/WikiCitationDrawer.test.tsx`

**Interfaces:**
- Consumes: `WikiCitationDetail | null`, loading/error state, and close callback.
- Produces: accessible overlay drawer with raw mail metadata and supporting agenda quotes.

- [ ] **Step 1: Write the failing drawer test**

```tsx
it('shows source mail and every supporting agenda quote', () => {
  render(
    <WikiCitationDrawer
      detail={{
        mail,
        agendas: [agenda],
        used_in_sections: ['원인과 영향 관계'],
      }}
      loading={false}
      error={null}
      onClose={vi.fn()}
    />,
  )
  expect(screen.getByRole('dialog', { name: '메일 근거' })).toBeInTheDocument()
  expect(screen.getByText(mail.subject)).toBeInTheDocument()
  expect(screen.getByText(agenda.source_quote)).toBeInTheDocument()
  expect(screen.getByText('원인과 영향 관계')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run and confirm missing component failure**

Run: `cd web && npm test -- WikiCitationDrawer.test.tsx`

Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement the accessible drawer**

Create a fixed backdrop and `<aside role="dialog" aria-label="메일 근거" aria-modal="true">`. The header displays mail subject and a close button. Metadata displays sender team, sender, received date, and used sections. For each agenda render summary, state, classification path chips, and `<EvidenceText body={detail.mail.body} quote={agenda.source_quote} />`. Loading and error states remain inside the same dialog so opening a citation never shifts the main page.

Close on backdrop click and stop propagation inside the drawer. Do not add send, edit, or classification controls.

- [ ] **Step 4: Run the drawer test**

Run: `cd web && npm test -- WikiCitationDrawer.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit the drawer**

```bash
git add web/src/components/WikiCitationDrawer.tsx web/src/components/WikiCitationDrawer.test.tsx
git commit -m "feat(web): add inline mail evidence drawer"
```

### Task 5: Show canonical pending counts and alias-filtered taxonomy

**Files:**
- Modify: `web/src/components/TaxonomyTree.tsx`
- Create: `web/src/components/TaxonomyTree.test.tsx`

**Interfaces:**
- Consumes: optional `wikiPages: WikiPageSummary[]` and `query: string`.
- Produces: filtered Wiki taxonomy tree using canonical open-issue counts; Explorer retains existing agenda counts when `wikiPages` is omitted.

- [ ] **Step 1: Write failing filtering and count tests**

```tsx
it('finds a LOTCD by alias and shows its canonical pending count', () => {
  render(
    <TaxonomyTree
      taxonomy={taxonomy}
      counts={[]}
      wikiPages={[{
        category_id: 'lotcd:4sa', canonical_id: 'dram/spica/4sa', level: 'lotcd',
        domain: 'DRAM', tech: 'Spica', lotcd: '4SA', title: '4SA',
        as_of_week: '2026-W28', open_issue_count: 3,
        resolved_issue_count: 1, confidence: 'high', review_item_count: 0,
      }]}
      query="SP LPDDR5 24G"
      selection={{ domain: null, tech: null, lotcd: null }}
      scopeMode="descendants"
      onSelect={vi.fn()}
    />,
  )
  expect(screen.getByRole('button', { name: /4SA/ })).toBeInTheDocument()
  expect(screen.getByText('3')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run and verify prop/type failure**

Run: `cd web && npm test -- TaxonomyTree.test.tsx`

Expected: FAIL because the optional Wiki props do not exist.

- [ ] **Step 3: Implement optional Wiki filtering**

Extend props:

```ts
wikiPages?: WikiPageSummary[]
query?: string
```

Build a `wikiCountMap` keyed with the existing `pathKey`. For each domain, keep a Tech if its name, aliases, any matching LOTCD code/product/aliases, or domain name includes the case-folded query. Keep the complete matching Tech branch so users retain hierarchy context. In the count span use `wikiCountMap.get(key) ?? existingAgendaCount` when `wikiPages` is provided, and retain current behavior when it is not.

- [ ] **Step 4: Run the taxonomy test**

Run: `cd web && npm test -- TaxonomyTree.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit taxonomy enhancements**

```bash
git add web/src/components/TaxonomyTree.tsx web/src/components/TaxonomyTree.test.tsx
git commit -m "feat(web): filter wiki taxonomy by aliases"
```

### Task 6: Coordinate the canonical reader in ExplorerPage

**Files:**
- Modify: `web/src/pages/ExplorerPage.tsx:1-32,66-105,419-535`
- Modify: `web/src/styles/wiki.css:32-120,490-570,560-720`
- Modify: `web/src/pages/WikiGraphPage.test.ts` only if its shared fixtures require the new page fields.

**Interfaces:**
- Consumes: all components and API functions from Tasks 1-5.
- Produces: complete three-pane canonical Wiki interaction.

- [ ] **Step 1: Add ExplorerPage state and data loading**

Import `fetchWikiPageSummaries`, `fetchWikiCitation`, `WikiDocumentMetaPane`, `WikiCitationDrawer`, `WikiHeading`, `WikiPageSummary`, and `WikiCitationDetail`. Add state:

```ts
const [wikiPages, setWikiPages] = useState<WikiPageSummary[]>([])
const [wikiOutline, setWikiOutline] = useState<WikiHeading[]>([])
const [citationMailId, setCitationMailId] = useState<string | null>(null)
const [citationDetail, setCitationDetail] = useState<WikiCitationDetail | null>(null)
const [citationLoading, setCitationLoading] = useState(false)
const [citationError, setCitationError] = useState<string | null>(null)
```

Fetch summaries in an abortable effect on `refreshVersion`. Fetch citation detail in another effect only when both `wikiPage` and `citationMailId` exist; clear detail on close and abort stale requests.

- [ ] **Step 2: Wire taxonomy, reader, context pane, and drawer**

Pass `wikiPages={wikiPages}` and `query={query}` only to the Wiki-mode `TaxonomyTree`. Change the reader props to:

```tsx
<CategoryWikiReader
  page={wikiPage}
  loading={wikiPageLoading}
  error={wikiPageError}
  onSelectCitation={setCitationMailId}
  onOutlineChange={setWikiOutline}
/>
```

When `selectedId` is false and `wikiPage` exists, render `WikiDocumentMetaPane` in the third column instead of `CategoryMetaPane`. Implement `navigateCanonical(canonicalId)` by splitting the ID into Domain/Tech/LOTCD and calling `changeSelection` with uppercase Domain and LOTCD. Preserve `WikiMetaPane` when an Agenda is selected.

Render `WikiCitationDrawer` after the shell whenever `citationMailId` is non-null. Closing it clears mail ID, detail, and error.

- [ ] **Step 3: Add the approved visual behavior**

Add CSS using existing variables only:

- `.wiki-mail-citation`: small inline violet link-button, baseline aligned;
- `.wiki-weekly-history details`: bordered disclosure with newest entry open;
- `.wiki-document-outline a`: block links with active hover state;
- `.wiki-related-pages button`: full-width path buttons;
- `.wiki-citation-backdrop`: fixed translucent layer at `z-index: 60`;
- `.wiki-citation-drawer`: right-aligned `min(680px, 92vw)` panel with scrollable body;
- keep `.category-note` max width between 760px and 820px;
- reuse the existing `@media (max-width: ...)` rule that hides `.wiki-meta-pane`.

Do not change global app tokens, Explorer list styles, or Graph styles.

- [ ] **Step 4: Run focused frontend tests**

Run: `cd web && npm test -- CategoryWikiReader.test.tsx WikiDocumentMetaPane.test.tsx WikiCitationDrawer.test.tsx TaxonomyTree.test.tsx`

Expected: all focused tests pass.

- [ ] **Step 5: Run the complete frontend verification**

Run: `cd web && npm test`

Expected: all Vitest tests pass.

Run: `cd web && npm run build`

Expected: TypeScript and Vite build complete with exit code 0.

- [ ] **Step 6: Commit the integrated Web reader**

```bash
git add web/src/pages/ExplorerPage.tsx web/src/styles/wiki.css web/src/pages/WikiGraphPage.test.ts
git commit -m "feat(web): integrate canonical wiki reader"
```

### Task 7: Perform end-to-end Web smoke verification

**Files:**
- Modify only when a verified defect is caused by this feature: files listed in Tasks 1-6.

**Interfaces:**
- Consumes: a running API with generated canonical dummy pages.
- Produces: verified stable navigation, narrative reading, history disclosure, and mail evidence interaction.

- [ ] **Step 1: Start the existing knowledge server**

Run: `python knowledge_web.py`

Expected: the server starts on its configured local port without schema errors.

- [ ] **Step 2: Open the canonical LOTCD route**

Open: `/wiki/docs/dram/spica/4sa`

Expected: left tree selects 4SA; center shows one coherent article; right pane shows outline and canonical metrics.

- [ ] **Step 3: Verify history and evidence interactions**

Expected:

- newest weekly entry is open;
- older weeks are closed and expand without a network request;
- clicking `[mail:...]` opens the evidence drawer;
- drawer subject, team, date, supporting agenda, and highlighted quote match the cited source;
- closing the drawer returns to the same scroll position and URL.

- [ ] **Step 4: Verify parent navigation**

Open Tech and Domain parent links from the right pane.

Expected: URL changes to `/wiki/docs/dram/spica` and `/wiki/docs/dram`; each page is an independently readable synthesis, not a list of child fragments.

- [ ] **Step 5: Re-run automated verification after smoke fixes**

Run: `pytest -q && cd web && npm test && npm run build`

Expected: all backend tests, frontend tests, and production build pass.

- [ ] **Step 6: Commit only smoke-test fixes if required**

```bash
git add web/src
git commit -m "fix(web): close wiki reader smoke gaps"
```

Skip this commit when no smoke-test fix was needed.
