# LOTCD Classification Workbench Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Add a simple three-column web Workbench for sequential weekly LOTCD review, trace inspection, corrections, alias learning, reruns, and approval.

**Architecture:** Add one route and one focused page that consumes the additive classification API. Keep state in the selected week, LOTCD/status filters, and selected item; use existing taxonomy and authentication behavior. Reuse existing visual tokens and classification patterns without changing Wiki Docs, Explorer, Review, or Mapping Admin behavior.

**Tech Stack:** React 19, TypeScript 7, React Router 7, native fetch, Vitest, Testing Library, Vite 8.

## Global Constraints

- The left column contains week and LOTCD/status navigation.
- The center column lists all classification items and their trace summary.
- The right column shows source context, candidates, reasons, revisions, and correction actions.
- Multi-LOTCD is not a selectable taxonomy category; aggregate is a status filter.
- LOTCD selection derives and displays Tech and Device/Domain from taxonomy.
- Alias creation and one-item correction are separate actions.
- Approved prior weeks are never automatically rerun.
- Do not add Wiki preview, graph features, analytics dashboards, or new UI libraries.
- Preserve current Wiki, Explorer, Review, and Mapping routes and tests.

---

## File structure

- Modify web/src/types.ts: classification run, week, item, decision, trace, comparison, and mutation types.
- Modify web/src/api/knowledge.ts: Workbench fetch and mutation functions.
- Create web/src/pages/ClassificationWorkbenchPage.tsx: page orchestration and three-column layout.
- Create web/src/components/ClassificationWeekNav.tsx: weeks and LOTCD/status filters.
- Create web/src/components/ClassificationItemList.tsx: searchable item table.
- Create web/src/components/ClassificationItemDetail.tsx: trace and correction actions.
- Create matching component/page tests.
- Modify web/src/main.tsx and knowledge_web.py: /classification route and SPA fallback.
- Modify web/src/styles/wiki.css: scoped Workbench layout styles only.

### Task 1: Add typed API client

**Files:**
- Modify: web/src/types.ts
- Modify: web/src/api/knowledge.ts
- Create: web/src/api/knowledge.classification.test.ts

**Interfaces:**
- Consumes: backend /api/knowledge/classification endpoints.
- Produces: fetchClassificationWeeks(), fetchClassificationItems(), fetchClassificationItem(), runClassificationWeek(), approveClassificationWeek(), correctClassificationItem(), setClassificationDisposition(), splitClassificationItem(), and createClassificationAlias().

- [ ] **Step 1: Write failing API serialization tests**

~~~~typescript
// web/src/api/knowledge.classification.test.ts
import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  correctClassificationItem,
  fetchClassificationItems,
  setClassificationDisposition,
} from './knowledge'

describe('classification API', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))

  it('serializes week, LOTCD, status, and query filters', async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 })),
    )
    await fetchClassificationItems('2026-01', {
      lotcd: '4SA',
      status: 'confirmed',
      q: '수율',
    })
    expect(vi.mocked(fetch).mock.calls[0][0]).toContain(
      '/classification/weeks/2026-01/items?lotcd=4SA&status=confirmed&q='
    )
  })

  it('keeps correction separate from aggregate disposition', async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({})))
    await correctClassificationItem('agenda-1', '4SA', '원문 확인')
    await setClassificationDisposition('agenda-2', 'aggregate', '종합지표')
    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBe('PATCH')
    expect(vi.mocked(fetch).mock.calls[1][0]).toContain('/disposition')
  })
})
~~~~

- [ ] **Step 2: Verify missing exports**

Run: cd web && npm test -- src/api/knowledge.classification.test.ts

Expected: TypeScript/Vitest fails because the Workbench API functions are not exported.

- [ ] **Step 3: Add TypeScript contracts**

~~~~typescript
export type DecisionStatus =
  | 'confirmed'
  | 'aggregate'
  | 'unclassified'
  | 'conflict'
  | 'review_required'
  | 'manually_corrected'
  | 'excluded'

export interface CandidateMatch {
  phrase: string
  lotcd: string
  match_type: 'canonical' | 'alias'
  rule_id: string
  score: number
}

export interface ClassificationDecision {
  status: DecisionStatus
  target_path: CategoryPath | null
  matches: CandidateMatch[]
  diagnostics: string[]
  confidence: number
}

export interface ClassificationWeek {
  week: string
  workflow_state:
    | 'not_started'
    | 'processing'
    | 'review_in_progress'
    | 'ready_for_approval'
    | 'approved'
    | 'revalidation_required'
    | 'failed'
  active_run_id: string | null
  counts: Partial<Record<DecisionStatus, number>>
}

export interface ClassificationItem {
  agenda_id: string
  mail_id: string
  summary: string
  source_quote: string
  classification_context: string
  item_kind: 'lotcd_specific' | 'aggregate' | 'unknown'
  decision: ClassificationDecision
  revision_count: number
}

export interface ClassificationItemList {
  items: ClassificationItem[]
  total: number
}
~~~~

- [ ] **Step 4: Implement the API functions**

Use URLSearchParams for list filters and one shared mutation helper that throws response text on non-2xx. Use encodeURIComponent for week, run, agenda, and LOTCD path segments. Keep the exact request bodies:

~~~~typescript
{ lotcd, reason }
{ status: 'aggregate' | 'excluded', reason }
{ parts: [{ source_quote, summary, lotcd }], reason }
{ value, lotcd, origin_agenda_id, context_domain, context_tech }
~~~~

- [ ] **Step 5: Verify and commit**

Run: cd web && npm test -- src/api/knowledge.classification.test.ts

Expected: all tests pass.

~~~~bash
git add web/src/types.ts web/src/api/knowledge.ts web/src/api/knowledge.classification.test.ts
git commit -m "feat(web): add classification API client"
~~~~

### Task 2: Build weekly navigation and classification list

**Files:**
- Create: web/src/components/ClassificationWeekNav.tsx
- Create: web/src/components/ClassificationWeekNav.test.tsx
- Create: web/src/components/ClassificationItemList.tsx
- Create: web/src/components/ClassificationItemList.test.tsx

**Interfaces:**
- Consumes: ClassificationWeek[], ClassificationItem[], selected week, LOTCD, status, and query.
- Produces: presentational callbacks only; no direct fetch calls.

- [ ] **Step 1: Write failing navigation test**

~~~~tsx
render(
  <ClassificationWeekNav
    weeks={[
      {
        week: '2026-01',
        workflow_state: 'review_in_progress',
        active_run_id: 'run-1',
        counts: { confirmed: 28, conflict: 2 },
      },
    ]}
    selectedWeek="2026-01"
    lotcdCounts={{ '4SA': 12, '6SA': 9 }}
    selectedLotcd=""
    selectedStatus=""
    onWeekChange={onWeekChange}
    onLotcdChange={onLotcdChange}
    onStatusChange={onStatusChange}
  />,
)
expect(screen.getByRole('button', { name: /2026-W01/ })).toHaveAttribute(
  'aria-pressed', 'true'
)
fireEvent.click(screen.getByRole('button', { name: /검토 필요/ }))
expect(onStatusChange).toHaveBeenCalledWith('review_required')
~~~~

- [ ] **Step 2: Write failing item-list test**

~~~~tsx
render(
  <ClassificationItemList
    items={[confirmedItem, aggregateItem, conflictItem]}
    selectedId="agenda-conflict"
    query=""
    onQueryChange={vi.fn()}
    onSelect={onSelect}
  />,
)
expect(screen.getByText('MULTIPLE_LOTCD_CONFLICT')).toBeInTheDocument()
expect(screen.getByText('종합지표')).toBeInTheDocument()
fireEvent.click(screen.getByText('Edge defect 증가'))
expect(onSelect).toHaveBeenCalledWith('agenda-confirmed')
~~~~

- [ ] **Step 3: Verify component imports fail**

Run: cd web && npm test -- src/components/ClassificationWeekNav.test.tsx src/components/ClassificationItemList.test.tsx

Expected: failures because both components are missing.

- [ ] **Step 4: Implement ClassificationWeekNav**

Render weeks chronologically with Korean labels, state text, and unresolved counts. Render filters for each taxonomy LOTCD plus aggregate, unclassified, conflict, and review_required. Do not render Multi-LOTCD as a category.

- [ ] **Step 5: Implement ClassificationItemList**

Use a semantic table with columns LOTCD, Agenda, Match reason, and Status. Display target_path.lotcd, 종합지표, or 미분류. Show the first match phrase/rule or first diagnostic code. Add a controlled search input. Row buttons must expose aria-selected.

- [ ] **Step 6: Verify and commit**

Run: cd web && npm test -- src/components/ClassificationWeekNav.test.tsx src/components/ClassificationItemList.test.tsx

Expected: all tests pass.

~~~~bash
git add web/src/components/ClassificationWeekNav.tsx web/src/components/ClassificationWeekNav.test.tsx web/src/components/ClassificationItemList.tsx web/src/components/ClassificationItemList.test.tsx
git commit -m "feat(web): add classification browser"
~~~~

### Task 3: Build item detail and correction actions

**Files:**
- Create: web/src/components/ClassificationItemDetail.tsx
- Create: web/src/components/ClassificationItemDetail.test.tsx
- Modify: web/src/components/ClassificationEditor.tsx
- Modify: web/src/components/ClassificationEditor.test.tsx

**Interfaces:**
- Consumes: selected ClassificationItem, taxonomy, canEdit, and mutation callbacks.
- Produces: one-LOTCD correction, split, aggregate, exclude, and learned-alias actions.

- [ ] **Step 1: Write failing detail action tests**

~~~~tsx
render(
  <ClassificationItemDetail
    item={conflictItem}
    taxonomy={taxonomy}
    canEdit
    onCorrect={onCorrect}
    onDisposition={onDisposition}
    onCreateAlias={onCreateAlias}
  />,
)
expect(screen.getByText(conflictItem.source_quote)).toBeInTheDocument()
expect(screen.getByText('canonical:4SA')).toBeInTheDocument()

fireEvent.change(screen.getByRole('combobox', { name: 'LOTCD' }), {
  target: { value: '4SA' },
})
fireEvent.change(screen.getByLabelText('수정 사유'), {
  target: { value: '원문 확인' },
})
fireEvent.click(screen.getByRole('button', { name: '이 항목만 수정' }))
await waitFor(() => expect(onCorrect).toHaveBeenCalledWith('4SA', '원문 확인'))
~~~~

Add separate assertions that 종합지표로 전환 calls onDisposition('aggregate', reason) and 유의어 등록 calls onCreateAlias() without first calling onCorrect().

~~~~tsx
fireEvent.click(screen.getByRole('button', { name: '항목 분할' }))
fireEvent.change(screen.getByLabelText('첫 번째 원문'), {
  target: { value: '4SA 수율 91.2' },
})
fireEvent.change(screen.getByLabelText('첫 번째 LOTCD'), {
  target: { value: '4SA' },
})
fireEvent.change(screen.getByLabelText('두 번째 원문'), {
  target: { value: '6SA 수율 92.4' },
})
fireEvent.change(screen.getByLabelText('두 번째 LOTCD'), {
  target: { value: '6SA' },
})
fireEvent.click(screen.getByRole('button', { name: '분할 저장' }))
await waitFor(() => expect(onSplit).toHaveBeenCalledWith(
  expect.arrayContaining([
    expect.objectContaining({ source_quote: '4SA 수율 91.2', lotcd: '4SA' }),
    expect.objectContaining({ source_quote: '6SA 수율 92.4', lotcd: '6SA' }),
  ]),
  expect.any(String),
))
~~~~

- [ ] **Step 2: Verify the missing detail component**

Run: cd web && npm test -- src/components/ClassificationItemDetail.test.tsx

Expected: import failure.

- [ ] **Step 3: Restrict ClassificationEditor to one LOTCD**

Remove multi-path selection from the Workbench use case. Preserve the existing component API for Explorer/Review, but add singleLotcdOnly?: boolean. When true, hide 경로 추가 and submit exactly one LOTCD-derived CategoryPath. Add a regression test proving the normal multi-path mode still works.

- [ ] **Step 4: Implement ClassificationItemDetail**

Show source quote, classification context, all candidate phrases/rules/scores, diagnostics, derived Device/Tech/LOTCD, and revision count. Keep four separate forms/actions:

1. 이 항목만 수정: required LOTCD and reason.
2. 항목 분할: at least two exact source quotes, summaries, LOTCDs, and reason.
3. 종합지표 or 제외: required reason.
4. 유의어 규칙 추가: phrase, LOTCD, optional context, and source agenda ID.

Disable every mutation when canEdit is false. Show the server error in role=alert and preserve form values after failure.

- [ ] **Step 5: Verify and commit**

Run: cd web && npm test -- src/components/ClassificationItemDetail.test.tsx src/components/ClassificationEditor.test.tsx

Expected: all tests pass.

~~~~bash
git add web/src/components/ClassificationItemDetail.tsx web/src/components/ClassificationItemDetail.test.tsx web/src/components/ClassificationEditor.tsx web/src/components/ClassificationEditor.test.tsx
git commit -m "feat(web): add classification corrections"
~~~~

### Task 4: Assemble the Workbench page and route

**Files:**
- Create: web/src/pages/ClassificationWorkbenchPage.tsx
- Create: web/src/pages/ClassificationWorkbenchPage.test.tsx
- Modify: web/src/main.tsx
- Modify: knowledge_web.py
- Modify: web/src/styles/wiki.css

**Interfaces:**
- Consumes: all API and presentational interfaces from Tasks 1-3.
- Produces: /classification page with sequential week review.

- [ ] **Step 1: Write a failing page workflow test**

~~~~tsx
vi.mock('../api/knowledge', () => ({
  fetchClassificationWeeks: vi.fn().mockResolvedValue([week]),
  fetchClassificationItems: vi.fn().mockResolvedValue({
    items: [conflictItem],
    total: 1,
  }),
  fetchClassificationItem: vi.fn().mockResolvedValue(conflictItem),
  fetchTaxonomy: vi.fn().mockResolvedValue(taxonomy),
  fetchSession: vi.fn().mockResolvedValue({
    user_id: 'reviewer',
    roles: ['knowledge-editor'],
    can_edit: true,
  }),
  correctClassificationItem: vi.fn().mockResolvedValue(correctedItem),
  approveClassificationWeek: vi.fn(),
}))

render(<ClassificationWorkbenchPage />)
expect(await screen.findByText('2026-W01')).toBeInTheDocument()
fireEvent.click(screen.getByText(conflictItem.summary))
expect(await screen.findByText(conflictItem.source_quote)).toBeInTheDocument()
expect(screen.getByRole('button', { name: /검수 완료/ })).toBeDisabled()
~~~~

~~~~tsx
it('approves a resolved week and refreshes the workbench', async () => {
  const ready = {
    ...week,
    workflow_state: 'ready_for_approval',
    counts: { confirmed: 3 },
  }
  vi.mocked(fetchClassificationWeeks).mockResolvedValue([ready])
  render(<ClassificationWorkbenchPage />)
  fireEvent.click(await screen.findByRole('button', { name: /검수 완료/ }))
  await waitFor(() => expect(approveClassificationWeek).toHaveBeenCalledWith('2026-01'))
  expect(fetchClassificationWeeks).toHaveBeenCalledTimes(2)
  expect(fetchClassificationItems).toHaveBeenCalledTimes(2)
})
~~~~

- [ ] **Step 2: Verify page import failure**

Run: cd web && npm test -- src/pages/ClassificationWorkbenchPage.test.tsx

Expected: import failure.

- [ ] **Step 3: Implement page orchestration**

On mount, fetch taxonomy, session, and week summaries. Select the earliest non-approved week, otherwise the latest week. On week/filter/query change, abort the previous list request. Select the first returned item only when the current selection is absent.

After correction, disposition, alias creation, rerun, or approval, refetch week summaries, current list, and current detail. Alias creation calls the current-week run endpoint only after alias creation succeeds; never call run for an approved prior week.

- [ ] **Step 4: Add route and SPA fallback**

In web/src/main.tsx:

~~~~tsx
<Route path="/classification" element={<ClassificationWorkbenchPage />} />
~~~~

In knowledge_web.py add /classification to serve_index. Do not change /wiki, /review, /mappings, or /explorer behavior.

- [ ] **Step 5: Add scoped responsive styles**

Use .classification-workbench as a three-column grid: 180px navigation, minmax(0, 1fr) list, 320px detail. At 1100px hide the detail into a full-width lower section; at 720px stack all columns. Reuse existing --wiki-* tokens and do not modify global element selectors.

- [ ] **Step 6: Run UI verification**

~~~~bash
cd web
npm test
npm run build
~~~~

Expected: all Vitest tests pass, TypeScript has zero errors, and Vite production build succeeds.

- [ ] **Step 7: Run backend route regression**

Run: pytest tests/test_knowledge_web.py -q

Expected: existing routes pass and GET /classification returns the built SPA when dist exists.

- [ ] **Step 8: Commit**

~~~~bash
git add web/src/pages/ClassificationWorkbenchPage.tsx web/src/pages/ClassificationWorkbenchPage.test.tsx web/src/main.tsx knowledge_web.py web/src/styles/wiki.css tests/test_knowledge_web.py
git commit -m "feat(web): add classification workbench"
~~~~

### Task 5: End-to-end verification

**Files:**
- Modify only files required by failures directly caused by Tasks 1-4.

**Interfaces:**
- Produces: a locally runnable Workbench with no Wiki dependency.

- [ ] **Step 1: Build the frontend**

Run: cd web && npm run build

Expected: production assets are generated successfully.

- [ ] **Step 2: Start the preview API**

Run: python -m uvicorn knowledge_preview:app --host 127.0.0.1 --port 8002

Expected: server starts and GET /api/knowledge/classification/weeks returns 200.

- [ ] **Step 3: Verify the main workflow manually**

At /classification:

1. Select 2026-W01.
2. Filter 4SA and inspect its source quote and rule.
3. Open a conflict item and correct it to one LOTCD.
4. Add a reusable alias from a different unresolved item.
5. Confirm current week rerun comparison is visible.
6. Confirm aggregate items have no LOTCD.
7. Confirm approval remains disabled until unresolved count is zero.
8. Approve the week and confirm 2026-W02 becomes the next active week.

Expected: every action persists after browser reload.

- [ ] **Step 4: Run complete verification**

~~~~bash
pytest tests/test_classification_workbench.py \
  tests/test_agenda_extract.py \
  tests/test_process_agendas.py \
  tests/test_seed_knowledge_db.py \
  tests/test_agenda_opensearch.py \
  tests/test_knowledge_api.py \
  tests/test_knowledge_web.py -q
cd web && npm test && npm run build
git diff --check
~~~~

Expected: all Python and web tests pass, build succeeds, and diff check is clean.

- [ ] **Step 5: Confirm verification made no uncommitted repair changes**

Run: git status --short

Expected: only pre-existing user-owned changes are listed. If verification
exposes a defect, return to the task that introduced it, add a failing regression
test there, implement the minimal fix, rerun Step 4, and commit with that task's
file list before completing this plan.
