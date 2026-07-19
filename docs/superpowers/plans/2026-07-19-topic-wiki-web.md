# Topic Wiki Four-Mode Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a tested four-mode Topic Wiki Web experience—Topic, LOTCD, Team, and Week—plus Topic-link review and build status to the existing Classification Workbench React application.

**Architecture:** Keep `/classification` intact and add a shared Wiki shell under `/wiki/*`. All modes consume the JSON backend APIs from `2026-07-19-topic-wiki-json-backend.md`; Topic detail is canonical, while LOTCD, Team, and Week screens render projection responses and link back to the same Topic route. Use plain React, React Router, existing design tokens, and CSS without adding a component library.

**Tech Stack:** React 19, TypeScript 7, React Router 7, Vite 8, Vitest 4, Testing Library.

## Global Constraints

- Execute only after the backend plan completion gate passes.
- Preserve `/classification` behavior and tests.
- Provide exactly four Wiki mode entries: `주제`, `LOTCD`, `팀`, `주차`.
- Do not duplicate Topic narrative into projection API models or local component state.
- Every Topic link routes to `/wiki/topics/{topicId}` and preserves a `from` query parameter for return navigation.
- LOTCD section order is fixed: summary, recent changes, active topics, knowledge areas, actions/decisions, related LOTCDs, closed topics, activity.
- Display pending Topic assignments as blocking reviews; pending Topic relations do not block Topic reading.
- Reuse existing auth/session endpoint, fetch error handling pattern, and `tokens.css`.
- Add no UI framework, graph library, state manager, or speculative visualization.
- All new routes support direct reload through the backend SPA fallback.

---

## File structure

- Modify `web/src/types.ts`: exact backend Topic Wiki contracts.
- Modify `web/src/api/knowledge.ts`: Wiki query and mutation clients.
- Modify `web/src/api/knowledge.classification.test.ts`: preserve existing calls while adding separate Wiki tests.
- Create `web/src/api/knowledge.wiki.test.ts`.
- Modify `web/src/main.tsx`: lazy Wiki routes while retaining `/classification`.
- Create `web/src/components/WikiShell.tsx`: common mode navigation, header, loading/error layout.
- Create `web/src/components/TopicList.tsx`: shared projection Topic rows.
- Create `web/src/components/EvidenceDrawer.tsx`: Agenda quote and source metadata.
- Create `web/src/components/TaxonomyTree.tsx`: Domain → Tech → LOTCD navigation from current rules API.
- Create `web/src/components/BuildStatusBanner.tsx`: build state and partial-failure disclosure.
- Create `web/src/pages/TopicIndexPage.tsx` and `TopicPage.tsx`.
- Create `web/src/pages/LotcdWikiPage.tsx`.
- Create `web/src/pages/TeamWikiPage.tsx`.
- Create `web/src/pages/WeekWikiPage.tsx`.
- Create `web/src/pages/WikiReviewPage.tsx`.
- Create focused component/page tests beside each file.
- Create `web/src/styles/wiki.css`; modify `web/src/styles/tokens.css` only when a missing semantic token is required.

### Task 1: Add exact Wiki TypeScript contracts and API clients

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/api/knowledge.ts`
- Create: `web/src/api/knowledge.wiki.test.ts`

**Interfaces:**
- Consumes: backend response/request models from Backend Tasks 2 and 7.
- Produces: typed `fetchTopics`, `fetchTopic`, `fetchLotcdWiki`, `fetchTeamWiki`, `fetchWeekWiki`, `fetchWikiReviews`, `resolveWikiReview`, `startWikiBuild`, and `fetchWikiBuild`.

- [ ] **Step 1: Write failing URL and mutation tests**

```ts
it('encodes all four Wiki mode routes', async () => {
  await fetchTopic('T/001')
  await fetchLotcdWiki('DRAM', 'Spica X', '4SA')
  await fetchTeamWiki('Yield & Quality')
  await fetchWeekWiki('2026-W30')

  expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
    '/api/knowledge/wiki/topics/T%2F001',
    '/api/knowledge/wiki/lotcd/DRAM/Spica%20X/4SA',
    '/api/knowledge/wiki/teams/Yield%20%26%20Quality',
    '/api/knowledge/wiki/weeks/2026-W30',
  ])
})

it('serializes a manual attach review decision', async () => {
  await resolveWikiReview('RV/1', { action: 'attach', topic_id: 'T-001' })
  expect(vi.mocked(fetch).mock.calls[0]).toEqual([
    '/api/knowledge/wiki/reviews/RV%2F1/resolve',
    expect.objectContaining({ method: 'POST', body: '{"action":"attach","topic_id":"T-001"}' }),
  ])
})
```

- [ ] **Step 2: Verify tests fail**

Run: `cd web && npm test -- src/api/knowledge.wiki.test.ts`

Expected: imports fail because Wiki API clients do not exist.

- [ ] **Step 3: Add exact shared contracts**

```ts
export type TopicKind = 'issue' | 'observation' | 'change' | 'experiment' | 'action' | 'decision' | 'plan' | 'knowledge'
export type KnowledgeArea = 'yield_defect' | 'process_equipment' | 'quality_analysis' | 'experiment_validation' | 'product_production' | 'schedule_delivery' | 'decision_action' | 'other'
export type TopicState = 'new' | 'investigating' | 'action_in_progress' | 'monitoring' | 'resolved' | 'reopened' | 'closed' | 'review_required'

export interface WikiTopic {
  topic_id: string
  title: string
  topic_kind: TopicKind
  primary_area: KnowledgeArea
  secondary_areas: KnowledgeArea[]
  state: TopicState
  importance: 'low' | 'medium' | 'high' | 'critical'
  first_seen_week: string
  last_updated_week: string
  target_paths: CategoryPath[]
  teams: string[]
  source_agenda_ids: string[]
  related_topic_ids: string[]
  current_revision_id: string
}

export type RelationKind = 'possible_cause' | 'affects' | 'measurement_effect' | 'comparison' | 'follow_up' | 'supports' | 'contradicts' | 'shares_condition'

export interface TopicRelation {
  relation_id: string
  source_topic_id: string
  target_topic_id: string
  kind: RelationKind
  agenda_ids: string[]
  confidence: number
  review_state: 'pending' | 'accepted' | 'rejected'
}

export interface WikiTopicDetail {
  topic: WikiTopic
  body_markdown: string
  sections: Array<{ key: string; title: string; body: string }>
  claims: Array<{ text: string; agenda_ids: string[] }>
  evidence: WikiEvidence[]
  relations: TopicRelation[]
}
```

```ts
export interface TopicListItem {
  topic_id: string
  title: string
  state: TopicState
  importance: 'low' | 'medium' | 'high' | 'critical'
  primary_area: KnowledgeArea
  target_paths: CategoryPath[]
  teams: string[]
  last_updated_week: string
  evidence_count: number
  rank_reasons: string[]
}

export interface WikiEvidence {
  agenda_id: string
  mail_id: string
  team: string
  week: string
  subject: string
  source_quote: string
  source_path: string | null
}

export interface LotcdWikiView {
  domain: DomainName; tech: string; lotcd: string; summary: string
  recent_changes: TopicListItem[]; active_topics: TopicListItem[]
  knowledge_areas: Partial<Record<KnowledgeArea, TopicListItem[]>>
  actions_and_decisions: TopicListItem[]; related_lotcds: string[]
  closed_topics: Record<string, TopicListItem[]>; activity: WikiEvidence[]; topic_ids: string[]
}

export interface TeamWikiView {
  team: string; topics: TopicListItem[]; topic_ids: string[]
  recent_activity: WikiEvidence[]; partner_teams: string[]
  target_paths: CategoryPath[]; actions_and_decisions: TopicListItem[]
}

export interface WeekWikiView {
  week: string; revision_id: string; published_at: string; build_run_id: string
  new_topic_ids: string[]; changed_topic_ids: string[]; resolved_topic_ids: string[]
  reopened_topic_ids: string[]; new_relation_ids: string[]
  pending_assignment_count: number; contradictions: string[]; teams: string[]
}

export interface WikiReview {
  review_id: string; kind: 'assignment' | 'relation'; agenda_id: string | null
  candidates: Array<{ topic_id: string; score: number; rank_reasons: string[] }>
  relation_id: string | null; rationale: string
  status: 'pending' | 'resolved' | 'held'
}

export interface WikiBuildRun {
  run_id: string; week: string; classification_run_id: string
  status: 'linking' | 'review_required' | 'generating' | 'validating' | 'published' | 'partially_failed' | 'failed'
  input_hash: string; model: string; affected_topic_ids: string[]; failed_topic_ids: string[]
  started_at: string; completed_at: string | null; error: string | null
}

export interface WikiReviewResolution {
  action: 'attach' | 'create' | 'hold' | 'accept' | 'reject'
  topic_id?: string
  title?: string
}
```

- [ ] **Step 4: Add API clients using existing helpers**

```ts
export function fetchTopics(filters: { q?: string; state?: TopicState; area?: KnowledgeArea; team?: string; lotcd?: string } = {}, signal?: AbortSignal) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => { if (value) params.set(key, value) })
  const query = params.toString()
  return getJson<TopicListItem[]>(`/api/knowledge/wiki/topics${query ? `?${query}` : ''}`, signal)
}

export const fetchTopic = (topicId: string, signal?: AbortSignal) =>
  getJson<WikiTopicDetail>(`/api/knowledge/wiki/topics/${encodeURIComponent(topicId)}`, signal)

export const fetchLotcdWiki = (domain: DomainName, tech: string, lotcd: string, signal?: AbortSignal) =>
  getJson<LotcdWikiView>(`/api/knowledge/wiki/lotcd/${encodeURIComponent(domain)}/${encodeURIComponent(tech)}/${encodeURIComponent(lotcd)}`, signal)

export const resolveWikiReview = (reviewId: string, input: WikiReviewResolution) =>
  mutate<WikiReview>(`/api/knowledge/wiki/reviews/${encodeURIComponent(reviewId)}/resolve`, 'POST', input)

export const fetchTeamWiki = (team: string, signal?: AbortSignal) =>
  getJson<TeamWikiView>(`/api/knowledge/wiki/teams/${encodeURIComponent(team)}`, signal)

export const fetchWeekWiki = (week: string, signal?: AbortSignal) =>
  getJson<WeekWikiView>(`/api/knowledge/wiki/weeks/${encodeURIComponent(week)}`, signal)

export const fetchWikiReviews = (signal?: AbortSignal) =>
  getJson<WikiReview[]>('/api/knowledge/wiki/reviews?status=pending', signal)

export const startWikiBuild = (week: string) =>
  mutate<WikiBuildRun>(`/api/knowledge/wiki/builds/${encodeURIComponent(week)}`, 'POST')

export const fetchWikiBuild = (runId: string, signal?: AbortSignal) =>
  getJson<WikiBuildRun>(`/api/knowledge/wiki/builds/${encodeURIComponent(runId)}`, signal)
```

- [ ] **Step 5: Run tests and commit**

Run: `cd web && npm test -- src/api/knowledge.classification.test.ts src/api/knowledge.wiki.test.ts`

Expected: both existing classification and new Wiki API tests pass.

```bash
git add web/src/types.ts web/src/api/knowledge.ts web/src/api/knowledge.wiki.test.ts
git commit -m "feat(web): add topic wiki API contracts"
```

### Task 2: Add the Wiki shell and four-mode routes

**Files:**
- Modify: `web/src/main.tsx`
- Create: `web/src/components/WikiShell.tsx`
- Create: `web/src/components/WikiShell.test.tsx`
- Create: `web/src/styles/wiki.css`

**Interfaces:**
- Consumes: React Router and existing `fetchSession()`.
- Produces: `WikiShell`, `/wiki/topics`, `/wiki/lotcd`, `/wiki/teams`, `/wiki/weeks`, and `/wiki/reviews` route outlets.

- [ ] **Step 1: Write failing navigation test**

```tsx
it('shows four Wiki modes and keeps Classification available', async () => {
  render(<MemoryRouter initialEntries={['/wiki/topics']}><WikiShell /></MemoryRouter>)
  expect(screen.getByRole('link', { name: '주제' })).toHaveAttribute('href', '/wiki/topics')
  expect(screen.getByRole('link', { name: 'LOTCD' })).toHaveAttribute('href', '/wiki/lotcd')
  expect(screen.getByRole('link', { name: '팀' })).toHaveAttribute('href', '/wiki/teams')
  expect(screen.getByRole('link', { name: '주차' })).toHaveAttribute('href', '/wiki/weeks')
  expect(screen.getByRole('link', { name: '분류 작업대' })).toHaveAttribute('href', '/classification')
})
```

- [ ] **Step 2: Verify test fails**

Run: `cd web && npm test -- src/components/WikiShell.test.tsx`

Expected: `WikiShell` does not exist.

- [ ] **Step 3: Implement accessible shell and lazy routes**

```tsx
export function WikiShell() {
  return (
    <div className="wiki-shell">
      <header className="wiki-shell__header">
        <Link to="/wiki/topics" className="wiki-shell__brand">Weekly Knowledge Wiki</Link>
        <nav aria-label="Wiki 탐색 모드">
          <NavLink to="/wiki/topics">주제</NavLink>
          <NavLink to="/wiki/lotcd">LOTCD</NavLink>
          <NavLink to="/wiki/teams">팀</NavLink>
          <NavLink to="/wiki/weeks">주차</NavLink>
        </nav>
        <Link to="/classification">분류 작업대</Link>
      </header>
      <Outlet />
    </div>
  )
}
```

In `main.tsx`, lazy-load pages and nest them under `<Route path="/wiki" element={<WikiShell />}>`. Keep `/classification` unchanged and redirect `/wiki` to `/wiki/topics`.

- [ ] **Step 4: Add minimal responsive layout CSS**

Use existing tokens for background, border, text, and focus. Define one-column layout below `900px`; do not hide navigation or evidence actions.

- [ ] **Step 5: Run tests/build and commit**

Run: `cd web && npm test -- src/components/WikiShell.test.tsx src/pages/ClassificationWorkbenchPage.test.tsx && npm run build`

Expected: tests and TypeScript/Vite build pass.

```bash
git add web/src/main.tsx web/src/components/WikiShell.tsx web/src/components/WikiShell.test.tsx web/src/styles/wiki.css
git commit -m "feat(web): add four-mode wiki shell"
```

### Task 3: Implement canonical Topic list and detail

**Files:**
- Create: `web/src/pages/TopicIndexPage.tsx`
- Create: `web/src/pages/TopicIndexPage.test.tsx`
- Create: `web/src/pages/TopicPage.tsx`
- Create: `web/src/pages/TopicPage.test.tsx`
- Create: `web/src/components/TopicList.tsx`
- Create: `web/src/components/EvidenceDrawer.tsx`
- Modify: `web/src/styles/wiki.css`

**Interfaces:**
- Consumes: `fetchTopics()`, `fetchTopic()`, `WikiTopicDetail`.
- Produces: searchable Topic index, canonical Topic document, related Topic links, and Agenda evidence drawer.

- [ ] **Step 1: Write failing evidence/navigation test**

```tsx
it('opens cited Agenda evidence without duplicating the Topic', async () => {
  vi.mocked(fetchTopic).mockResolvedValue(topicDetail)
  render(<MemoryRouter initialEntries={['/wiki/topics/T-001?from=%2Fwiki%2Flotcd%2FDRAM%2FSpica%2F4SA']}>
    <Routes><Route path="/wiki/topics/:topicId" element={<TopicPage />} /></Routes>
  </MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: '근거 A-001 보기' }))
  expect(screen.getByRole('dialog', { name: 'Agenda 근거' })).toHaveTextContent('4SA D1 불량')
  expect(screen.getByRole('link', { name: '이전 화면' })).toHaveAttribute('href', '/wiki/lotcd/DRAM/Spica/4SA')
})
```

- [ ] **Step 2: Verify tests fail**

Run: `cd web && npm test -- src/pages/TopicIndexPage.test.tsx src/pages/TopicPage.test.tsx`

Expected: Topic pages do not exist.

- [ ] **Step 3: Implement Topic index**

Read `q`, `state`, `area`, `team`, and `lotcd` from `URLSearchParams`; call `fetchTopics(filters)`. Render state, primary area, LOTCD paths, contributing teams, last update, and evidence count. `TopicList` creates links as:

```tsx
<Link to={`/wiki/topics/${encodeURIComponent(topic.topic_id)}?from=${encodeURIComponent(location.pathname + location.search)}`}>
  {topic.title}
</Link>
```

- [ ] **Step 4: Implement Topic detail and evidence drawer**

Render `WikiTopicDetail.sections` as semantic `<section><h2>{title}</h2><p>{body}</p></section>` blocks with `white-space: pre-wrap`; keep `body_markdown` for export only. Render state and importance metadata, accepted relations, and citation buttons. `EvidenceDrawer` uses `<dialog aria-label="Agenda 근거">` semantics, displays team/week/subject/source quote, and provides a source-path label without exposing an executable local link.

- [ ] **Step 5: Run tests/build and commit**

Run: `cd web && npm test -- src/pages/TopicIndexPage.test.tsx src/pages/TopicPage.test.tsx && npm run build`

Expected: tests and build pass.

```bash
git add web/src/pages/TopicIndexPage.tsx web/src/pages/TopicIndexPage.test.tsx web/src/pages/TopicPage.tsx web/src/pages/TopicPage.test.tsx web/src/components/TopicList.tsx web/src/components/EvidenceDrawer.tsx web/src/styles/wiki.css
git commit -m "feat(web): add canonical topic reader"
```

### Task 4: Implement the fixed LOTCD mode

**Files:**
- Create: `web/src/components/TaxonomyTree.tsx`
- Create: `web/src/components/TaxonomyTree.test.tsx`
- Create: `web/src/pages/LotcdWikiPage.tsx`
- Create: `web/src/pages/LotcdWikiPage.test.tsx`
- Modify: `web/src/styles/wiki.css`

**Interfaces:**
- Consumes: `fetchTaxonomy()`, `fetchLotcdWiki()`, `LotcdWikiView`, `TopicList`.
- Produces: Domain → Tech → LOTCD navigation and the exact eight-section LOTCD view.

- [ ] **Step 1: Write failing table-of-contents test**

```tsx
it('renders the fixed LOTCD sections in backend order', async () => {
  vi.mocked(fetchLotcdWiki).mockResolvedValue(lotcdView)
  render(<MemoryRouter initialEntries={['/wiki/lotcd/DRAM/Spica/4SA']}>
    <Routes><Route path="/wiki/lotcd/:domain/:tech/:lotcd" element={<LotcdWikiPage />} /></Routes>
  </MemoryRouter>)
  const headings = (await screen.findAllByRole('heading', { level: 2 })).map((node) => node.textContent)
  expect(headings).toEqual(['현황 요약', '주요 변화', '진행 중 Topic', '지식 영역', '조치와 의사결정', '연관 LOTCD', '해결·종료된 Topic', '출처와 활동 이력'])
})
```

- [ ] **Step 2: Verify test fails**

Run: `cd web && npm test -- src/components/TaxonomyTree.test.tsx src/pages/LotcdWikiPage.test.tsx`

Expected: components do not exist.

- [ ] **Step 3: Implement taxonomy navigation**

`TaxonomyTree` receives the existing `Taxonomy` response, renders nested lists with buttons/links, and routes leaf nodes to `/wiki/lotcd/{domain}/{tech}/{lotcd}`. Domain and Tech expand/collapse locally; there is no taxonomy editing in Wiki mode.

- [ ] **Step 4: Implement all eight projection sections**

Render backend-provided rank reasons on Topic rows. Group `knowledge_areas` by controlled labels, show related LOTCD links, group closed Topics by quarter, and render activity by week/team. Do not generate or recompute summaries in the browser.

- [ ] **Step 5: Run tests/build and commit**

Run: `cd web && npm test -- src/components/TaxonomyTree.test.tsx src/pages/LotcdWikiPage.test.tsx && npm run build`

Expected: tests and build pass.

```bash
git add web/src/components/TaxonomyTree.tsx web/src/components/TaxonomyTree.test.tsx web/src/pages/LotcdWikiPage.tsx web/src/pages/LotcdWikiPage.test.tsx web/src/styles/wiki.css
git commit -m "feat(web): add fixed LOTCD wiki mode"
```

### Task 5: Implement Team and Week modes

**Files:**
- Create: `web/src/pages/TeamWikiPage.tsx`
- Create: `web/src/pages/TeamWikiPage.test.tsx`
- Create: `web/src/pages/WeekWikiPage.tsx`
- Create: `web/src/pages/WeekWikiPage.test.tsx`
- Modify: `web/src/styles/wiki.css`

**Interfaces:**
- Consumes: `fetchTeamWiki()`, `fetchWeekWiki()`, shared Topic list and build status contracts.
- Produces: team contribution view and auditable week-change view.

- [ ] **Step 1: Write failing projection tests**

```tsx
function renderTeam(route: string) {
  return render(<MemoryRouter initialEntries={[route]}><Routes><Route path="/wiki/teams/:team" element={<TeamWikiPage />} /></Routes></MemoryRouter>)
}

function renderWeek(route: string) {
  return render(<MemoryRouter initialEntries={[route]}><Routes><Route path="/wiki/weeks/:week" element={<WeekWikiPage />} /></Routes></MemoryRouter>)
}

it('shows team contributions and cross-team topics', async () => {
  vi.mocked(fetchTeamWiki).mockResolvedValue(teamView)
  renderTeam('/wiki/teams/Yield')
  expect(await screen.findByText('Yield')).toBeInTheDocument()
  expect(screen.getByText('Process, Quality')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '4SA D1 불량' })).toHaveAttribute('href', expect.stringContaining('/wiki/topics/T-001'))
})

it('separates new, changed, resolved, and reopened topics', async () => {
  vi.mocked(fetchWeekWiki).mockResolvedValue(weekView)
  renderWeek('/wiki/weeks/2026-W30')
  for (const label of ['새 Topic', '변경된 Topic', '해결된 Topic', '재발한 Topic']) {
    expect(await screen.findByRole('heading', { name: label })).toBeInTheDocument()
  }
})
```

- [ ] **Step 2: Verify tests fail**

Run: `cd web && npm test -- src/pages/TeamWikiPage.test.tsx src/pages/WeekWikiPage.test.tsx`

Expected: pages do not exist.

- [ ] **Step 3: Implement Team mode**

Provide a team selector derived from backend team facets; display current/recent contributions, shared Topics with other teams, sourced owned actions, taxonomy coverage, and report evidence. All narrative links point to canonical Topic detail.

- [ ] **Step 4: Implement Week mode**

Provide week navigation, snapshot revision label, new/changed/resolved/reopened groups, new accepted relations, important actions/decisions, unresolved assignment count, contradictions, and contributing teams. A revised snapshot must display its revision ID and publish time.

- [ ] **Step 5: Run tests/build and commit**

Run: `cd web && npm test -- src/pages/TeamWikiPage.test.tsx src/pages/WeekWikiPage.test.tsx && npm run build`

Expected: tests and build pass.

```bash
git add web/src/pages/TeamWikiPage.tsx web/src/pages/TeamWikiPage.test.tsx web/src/pages/WeekWikiPage.tsx web/src/pages/WeekWikiPage.test.tsx web/src/styles/wiki.css
git commit -m "feat(web): add team and week wiki modes"
```

### Task 6: Add Topic assignment review and build status

**Files:**
- Create: `web/src/pages/WikiReviewPage.tsx`
- Create: `web/src/pages/WikiReviewPage.test.tsx`
- Create: `web/src/components/BuildStatusBanner.tsx`
- Create: `web/src/components/BuildStatusBanner.test.tsx`
- Modify: `web/src/components/WikiShell.tsx`
- Modify: `web/src/styles/wiki.css`

**Interfaces:**
- Consumes: Wiki review/build API clients and `KnowledgeSession.can_edit`.
- Produces: attach/create/hold workflow, non-blocking relation decisions, and visible partial-failure status.

- [ ] **Step 1: Write failing permission and partial-failure tests**

```tsx
it('allows editors to attach an ambiguous Agenda', async () => {
  vi.mocked(fetchWikiReviews).mockResolvedValue([assignmentReview])
  vi.mocked(fetchSession).mockResolvedValue({ user_id: 'owner', roles: ['knowledge-editor'], can_edit: true })
  render(<WikiReviewPage />)
  fireEvent.click(await screen.findByRole('button', { name: 'T-001에 연결' }))
  await waitFor(() => expect(resolveWikiReview).toHaveBeenCalledWith('RV-001', { action: 'attach', topic_id: 'T-001' }))
})

it('keeps stale topic details visible on a partial failure', () => {
  render(<BuildStatusBanner run={{ ...run, status: 'partially_failed', failed_topic_ids: ['T-009'] }} />)
  expect(screen.getByRole('status')).toHaveTextContent('일부 Topic은 이전 정상 버전을 표시합니다')
})
```

- [ ] **Step 2: Verify tests fail**

Run: `cd web && npm test -- src/pages/WikiReviewPage.test.tsx src/components/BuildStatusBanner.test.tsx`

Expected: components do not exist.

- [ ] **Step 3: Implement review actions**

For assignment reviews, render candidate title, path, score, rationale, and actions `attach`, `create`, `hold`. For relation reviews, render relation kind and evidence, with `accept` and `reject`; label them `발행 비차단`. Disable mutations when `can_edit=false`.

- [ ] **Step 4: Implement build status and shell badge**

Show `linking`, `review_required`, `generating`, `validating`, `published`, `partially_failed`, and `failed`. Poll only while an active build is in a transient state and stop on unmount or terminal state. The shell review badge shows only blocking assignment review count.

- [ ] **Step 5: Run tests/build and commit**

Run: `cd web && npm test -- src/pages/WikiReviewPage.test.tsx src/components/BuildStatusBanner.test.tsx && npm run build`

Expected: tests and build pass.

```bash
git add web/src/pages/WikiReviewPage.tsx web/src/pages/WikiReviewPage.test.tsx web/src/components/BuildStatusBanner.tsx web/src/components/BuildStatusBanner.test.tsx web/src/components/WikiShell.tsx web/src/styles/wiki.css
git commit -m "feat(web): add topic review and build status"
```

### Task 7: Verify complete navigation, regression safety, and production build

**Files:**
- Modify: `web/src/pages/ClassificationWorkbenchPage.test.tsx`
- Modify: `tests/test_knowledge_web.py`
- Modify: `docs/topic_wiki_operations.md`

**Interfaces:**
- Consumes: complete backend and Web implementation.
- Produces: cross-mode integration coverage and deployable `web/dist`.

- [ ] **Step 1: Add a cross-mode navigation regression**

```tsx
function renderApp(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/wiki/lotcd/:domain/:tech/:lotcd" element={<LotcdWikiPage />} />
        <Route path="/wiki/teams/:team" element={<TeamWikiPage />} />
        <Route path="/wiki/weeks/:week" element={<WeekWikiPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

it('opens one canonical Topic from LOTCD, Team, and Week modes', async () => {
  for (const route of ['/wiki/lotcd/DRAM/Spica/4SA', '/wiki/teams/Yield', '/wiki/weeks/2026-W30']) {
    const { unmount } = renderApp(route)
    const link = await screen.findByRole('link', { name: '4SA D1 불량' })
    expect(link.getAttribute('href')).toContain('/wiki/topics/T-001?from=')
    unmount()
  }
})
```

- [ ] **Step 2: Run all Web tests**

Run: `cd web && npm test`

Expected: all Vitest suites pass, including unchanged Classification Workbench tests.

- [ ] **Step 3: Build and verify direct routes**

Run: `cd web && npm run build`

Expected: TypeScript reports no errors and Vite creates `web/dist`.

Run: `pytest tests/test_knowledge_web.py tests/test_knowledge_api.py -q`

Expected: direct `/classification` and `/wiki/*` SPA routes plus API routes pass.

- [ ] **Step 4: Update operator verification commands**

Append:

```bash
cd web
npm test
npm run build
cd ..
pytest -q
```

Document that the configured default model shown in build metadata must be `z-ai/glm-5.2`.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ClassificationWorkbenchPage.test.tsx tests/test_knowledge_web.py docs/topic_wiki_operations.md
git commit -m "test(web): verify four-mode wiki workflow"
```

## Final completion gate

Run from `.worktrees/lotcd-classification`:

```bash
pytest -q
cd web && npm test && npm run build
cd ..
git status --short
```

Expected: all Python tests, all Vitest tests, and the production build pass; `git status --short` prints nothing.
