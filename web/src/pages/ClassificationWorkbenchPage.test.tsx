import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import {
  approveClassificationWeek,
  fetchClassificationItem,
  fetchClassificationItems,
  fetchClassificationWeeks,
  fetchLotcdWiki,
  fetchSession,
  fetchTaxonomy,
  fetchTeamWiki,
  fetchWeekWiki,
  fetchWikiBuild,
} from '../api/knowledge'
import { ClassificationWorkbenchPage } from './ClassificationWorkbenchPage'
import { LotcdWikiPage } from './LotcdWikiPage'
import { TeamWikiPage } from './TeamWikiPage'
import { WeekWikiPage } from './WeekWikiPage'
import type {
  ClassificationItem, ClassificationWeek, LotcdWikiView, Taxonomy,
  TeamWikiView, TopicListItem, WeekWikiView,
} from '../types'

vi.mock('../api/knowledge', () => ({
  fetchClassificationWeeks: vi.fn(), fetchClassificationItems: vi.fn(),
  fetchClassificationItem: vi.fn(), fetchTaxonomy: vi.fn(), fetchSession: vi.fn(),
  correctClassificationItem: vi.fn(), setClassificationDisposition: vi.fn(),
  splitClassificationItem: vi.fn(), createClassificationAlias: vi.fn(),
  runClassificationWeek: vi.fn(), approveClassificationWeek: vi.fn(),
  fetchLotcdWiki: vi.fn(), fetchTeamWiki: vi.fn(), fetchWeekWiki: vi.fn(),
  fetchWikiBuild: vi.fn(),
}))

const taxonomy: Taxonomy = { version: 1, is_dummy: false, notice: '', group_aliases: [], domains: [
  { id: 'dram', name: 'DRAM', techs: [{ id: 'spica', name: 'Spica', aliases: [], lotcds: [
    { code: '4SA', fab_id: '4', product_code: 'SA', product: 'LPDDR5', aliases: [] },
  ] }] },
] }
const item: ClassificationItem = {
  agenda_id: 'agenda-1', mail_id: 'mail-1', summary: 'Edge defect 증가', source_quote: '4SA Edge defect 증가',
  classification_context: '주간 품질', item_kind: 'lotcd_specific', revision_count: 0,
  decision: { status: 'conflict', target_path: null, matches: [], diagnostics: ['MULTIPLE_LOTCD_CONFLICT'], confidence: 0.4 },
}
const week: ClassificationWeek = { week: '2026-01', workflow_state: 'review_in_progress', active_run_id: 'run-1', counts: { conflict: 1 } }
const topic: TopicListItem = {
  topic_id: 'T-001', title: '4SA D1 불량', state: 'investigating', importance: 'high',
  primary_area: 'yield_defect', target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  teams: ['Yield'], last_updated_week: '2026-W30', evidence_count: 1, rank_reasons: [],
}
const lotcdView: LotcdWikiView = {
  domain: 'DRAM', tech: 'Spica', lotcd: '4SA', summary: '4SA: 1 Topic',
  scope_level: 'lotcd', breadcrumb: ['DRAM', 'Spica', '4SA'], direct_activity: [], rolled_up_activity: [],
  recent_changes: [topic], active_topics: [], knowledge_areas: {},
  actions_and_decisions: [], related_lotcds: [], closed_topics: {}, activity: [], topic_ids: ['T-001'],
}
const teamView: TeamWikiView = {
  team: 'Yield', topics: [topic], topic_ids: ['T-001'], recent_activity: [], partner_teams: [],
  target_paths: topic.target_paths, actions_and_decisions: [],
}
const weekView: WeekWikiView = {
  week: '2026-W30', revision_id: 'WREV-001', published_at: '2026-07-20T00:00:00Z',
  build_run_id: '', new_topic_ids: [], changed_topic_ids: [], resolved_topic_ids: [],
  reopened_topic_ids: [], actions_and_decisions: [topic], new_relation_ids: [],
  pending_assignment_count: 0, contradictions: [], teams: ['Yield'],
}

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

describe('ClassificationWorkbenchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchTaxonomy).mockResolvedValue(taxonomy)
    vi.mocked(fetchSession).mockResolvedValue({ user_id: 'reviewer', roles: ['knowledge-editor'], can_edit: true })
    vi.mocked(fetchClassificationWeeks).mockResolvedValue([week])
    vi.mocked(fetchClassificationItems).mockResolvedValue({ items: [item], total: 1 })
    vi.mocked(fetchClassificationItem).mockResolvedValue(item)
    vi.mocked(approveClassificationWeek).mockResolvedValue(week)
    vi.mocked(fetchLotcdWiki).mockResolvedValue(lotcdView)
    vi.mocked(fetchTeamWiki).mockResolvedValue(teamView)
    vi.mocked(fetchWeekWiki).mockResolvedValue(weekView)
    vi.mocked(fetchWikiBuild).mockRejectedValue(new Error('build metadata not expected'))
  })

  it('loads the earliest open week and shows selected item details', async () => {
    render(<ClassificationWorkbenchPage />)
    expect(await screen.findByText('2026-W01')).toBeInTheDocument()
    fireEvent.click(await screen.findByText(item.summary))
    expect(await screen.findByText(item.source_quote)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '검수 완료' })).toBeDisabled()
  })

  it('approves a resolved week and refreshes summaries and items', async () => {
    const ready = { ...week, workflow_state: 'ready_for_approval' as const, counts: { confirmed: 3 } }
    vi.mocked(fetchClassificationWeeks).mockResolvedValue([ready])
    vi.mocked(approveClassificationWeek).mockResolvedValue(ready)
    render(<ClassificationWorkbenchPage />)
    fireEvent.click(await screen.findByRole('button', { name: '검수 완료' }))
    await waitFor(() => expect(approveClassificationWeek).toHaveBeenCalledWith('2026-01'))
    await waitFor(() => expect(fetchClassificationWeeks).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(fetchClassificationItems).toHaveBeenCalledTimes(2))
  })

  it('opens one canonical Topic from LOTCD, Team, and Week modes', async () => {
    for (const route of ['/wiki/lotcd/DRAM/Spica/4SA', '/wiki/teams/Yield', '/wiki/weeks/2026-W30']) {
      const { unmount } = renderApp(route)
      const link = await screen.findByRole('link', { name: '4SA D1 불량' })
      expect(link.getAttribute('href')).toContain('/wiki/topics/T-001?from=')
      unmount()
    }
  })
})
