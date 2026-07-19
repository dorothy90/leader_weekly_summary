import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { fetchTeamWiki, fetchWikiTeams } from '../api/knowledge'
import type { TeamWikiView, TopicListItem } from '../types'
import { TeamWikiPage } from './TeamWikiPage'

vi.mock('../api/knowledge', () => ({ fetchTeamWiki: vi.fn(), fetchWikiTeams: vi.fn() }))

const topic: TopicListItem = {
  topic_id: 'T-001', title: '4SA D1 불량', state: 'investigating', importance: 'high',
  primary_area: 'yield_defect',
  target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  teams: ['Yield', 'Process'], last_updated_week: '2026-W30', evidence_count: 2,
  rank_reasons: ['최근 2주 내 갱신'],
}

const teamView: TeamWikiView = {
  team: 'Yield', topics: [topic], topic_ids: ['T-001'],
  recent_activity: [{
    agenda_id: 'A-001', mail_id: 'M-001', team: 'Yield', week: '2026-W30',
    subject: '4SA 수율 점검', source_quote: 'D1 불량을 확인했다.', source_path: 'mail/M-001',
  }],
  partner_teams: ['Process', 'Quality'],
  target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  actions_and_decisions: [topic],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTeamWiki).mockResolvedValue(teamView)
  vi.mocked(fetchWikiTeams).mockResolvedValue({ values: ['Process', 'Yield'] })
})

it('shows backend-derived team choices at the root route', async () => {
  render(<MemoryRouter initialEntries={['/wiki/teams']}>
    <Routes><Route path="/wiki/teams/*" element={<TeamWikiPage />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByRole('button', { name: 'Yield' })).toBeInTheDocument()
  expect(fetchWikiTeams).toHaveBeenCalled()
})

it('shows an alert when the root team index fails', async () => {
  vi.mocked(fetchWikiTeams).mockRejectedValue(new Error('failed'))
  render(<MemoryRouter initialEntries={['/wiki/teams']}>
    <Routes><Route path="/wiki/teams/*" element={<TeamWikiPage />} /></Routes>
  </MemoryRouter>)
  expect(await screen.findByRole('alert')).toHaveTextContent('팀 기여 보기를 불러오지 못했습니다.')
})

function renderTeam(route: string) {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <Routes>
        <Route path="/wiki/teams/:team" element={<TeamWikiPage />} />
        <Route path="/wiki/teams/:team/selected" element={<p>selected</p>} />
      </Routes>
    </MemoryRouter>,
  )
}

it('shows team contributions, cross-team topics, and canonical Topic links', async () => {
  renderTeam('/wiki/teams/Yield')

  expect(await screen.findByRole('heading', { name: 'Yield' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '공동 기여 Topic' })).toBeInTheDocument()
  expect(screen.getByText('Process, Quality')).toBeInTheDocument()
  expect(screen.getAllByRole('link', { name: '4SA D1 불량' })[0]).toHaveAttribute(
    'href', '/wiki/topics/T-001?from=%2Fwiki%2Fteams%2FYield',
  )
  expect(screen.getAllByText('DRAM / Spica / 4SA').length).toBeGreaterThan(0)
})

it('renders the backend four-week activity projection as report evidence', async () => {
  renderTeam('/wiki/teams/Yield')

  const evidence = await screen.findByLabelText('최근 4주 보고 근거')
  expect(within(evidence).getByText(/2026-W30 · Yield/)).toBeInTheDocument()
  expect(within(evidence).getByText('D1 불량을 확인했다.')).toBeInTheDocument()
  expect(within(evidence).getByText('A-001 · M-001')).toBeInTheDocument()
})

it('navigates to a team selected from backend facets', async () => {
  renderTeam('/wiki/teams/Yield')

  const selector = await screen.findByRole('combobox', { name: '팀 선택' })
  expect(within(selector).getAllByRole('option').map((option) => option.textContent)).toEqual([
    'Yield', 'Process', 'Quality',
  ])
  fireEvent.change(selector, { target: { value: 'Process' } })
  await waitFor(() => expect(fetchTeamWiki).toHaveBeenLastCalledWith('Process', expect.any(AbortSignal)))
})
