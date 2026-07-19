import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { fetchTopics, fetchWeekWiki, fetchWikiBuild, fetchWikiWeeks } from '../api/knowledge'
import type { TopicListItem, WeekWikiView, WikiBuildRun } from '../types'
import { WeekWikiPage } from './WeekWikiPage'

vi.mock('../api/knowledge', () => ({
  fetchTopics: vi.fn(), fetchWeekWiki: vi.fn(), fetchWikiBuild: vi.fn(),
  fetchWikiWeeks: vi.fn(),
}))

function makeTopic(topic_id: string, title: string, overrides: Partial<TopicListItem> = {}): TopicListItem {
  return {
    topic_id, title, state: 'investigating', importance: 'high', primary_area: 'yield_defect',
    target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }], teams: ['Yield'],
    last_updated_week: '2026-W30', evidence_count: 1, rank_reasons: [], ...overrides,
  }
}

const weekView: WeekWikiView = {
  week: '2026-W30', revision_id: 'WREV-ABC123', published_at: '2026-07-20T01:02:03Z',
  build_run_id: 'RUN-030', new_topic_ids: ['T-001'],
  changed_topic_ids: ['T-001', 'T-002', 'T-003', 'T-004'], resolved_topic_ids: ['T-003'],
  reopened_topic_ids: ['T-004'], new_relation_ids: ['REL-001'], pending_assignment_count: 2,
  actions_and_decisions: [makeTopic('T-002', '스냅샷 당시 조치', { primary_area: 'yield_defect' })],
  contradictions: ['REL-009'], teams: ['Process', 'Yield'],
}

const buildRun: WikiBuildRun = {
  run_id: 'RUN-030', week: '2026-W30', classification_run_id: 'CLASS-030', taxonomy_version: 7,
  status: 'partially_failed', input_hash: 'hash', model: 'test-model', affected_topic_ids: ['T-001'],
  failed_topic_ids: ['T-009'], started_at: '2026-07-20T01:00:00Z',
  completed_at: '2026-07-20T01:02:03Z', error: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchWeekWiki).mockResolvedValue(weekView)
  vi.mocked(fetchTopics).mockResolvedValue([
    makeTopic('T-001', '변경된 현재 제목'),
    makeTopic('T-002', '변경된 현재 조치'),
  ])
  vi.mocked(fetchWikiBuild).mockResolvedValue(buildRun)
  vi.mocked(fetchWikiWeeks).mockResolvedValue({ values: ['2026-W29', '2026-W30'] })
})

it('shows persisted week choices at the root route', async () => {
  render(<MemoryRouter initialEntries={['/wiki/weeks']}>
    <Routes><Route path="/wiki/weeks/*" element={<WeekWikiPage />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByRole('button', { name: '2026-W30' })).toBeInTheDocument()
  expect(fetchWikiWeeks).toHaveBeenCalled()
})

function renderWeek(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes><Route path="/wiki/weeks/:week" element={<WeekWikiPage />} /></Routes>
    </MemoryRouter>,
  )
}

it('renders stored change IDs as canonical links without mutable current titles', async () => {
  renderWeek('/wiki/weeks/2026-W30')

  for (const label of ['새 Topic', '변경된 Topic', '해결된 Topic', '재발한 Topic']) {
    expect(await screen.findByRole('heading', { name: label })).toBeInTheDocument()
  }
  expect(screen.getAllByRole('link', { name: 'T-001' })[0]).toHaveAttribute(
    'href', '/wiki/topics/T-001?from=%2Fwiki%2Fweeks%2F2026-W30',
  )
  expect(screen.queryByText('변경된 현재 제목')).not.toBeInTheDocument()
  expect(fetchTopics).not.toHaveBeenCalled()
  expect(screen.getByRole('link', { name: '스냅샷 당시 조치' })).toBeInTheDocument()
})

it('shows snapshot provenance, partial build status, and audit counters', async () => {
  renderWeek('/wiki/weeks/2026-W30')

  const provenance = await screen.findByLabelText('스냅샷 빌드 출처')
  expect(within(provenance).getByText('WREV-ABC123')).toBeInTheDocument()
  expect(within(provenance).getByText('RUN-030')).toBeInTheDocument()
  expect(within(provenance).getByText('부분 실패')).toBeInTheDocument()
  expect(within(provenance).getByText('T-009')).toBeInTheDocument()
  for (const value of [
    'CLASS-030', '7', 'hash', 'test-model', '2026-07-20T01:00:00Z',
    '2026-07-20T01:02:03Z', '2026-07-20T01:02:03Z',
  ]) {
    expect(within(provenance).getAllByText(value).length).toBeGreaterThan(0)
  }
  expect(screen.getByText('미해결 배정 2건')).toBeInTheDocument()
  expect(screen.getByText('REL-001')).toBeInTheDocument()
  expect(screen.getByText('REL-009')).toBeInTheDocument()
  expect(screen.getByText('Process, Yield')).toBeInTheDocument()
})

it('uses an accessible native week control for navigation', async () => {
  renderWeek('/wiki/weeks/2026-W30')

  const control = await screen.findByLabelText('주차 선택')
  expect(control).toHaveAttribute('type', 'week')
  fireEvent.change(control, { target: { value: '2026-W29' } })
  await waitFor(() => expect(fetchWeekWiki).toHaveBeenLastCalledWith('2026-W29', expect.any(AbortSignal)))
})
