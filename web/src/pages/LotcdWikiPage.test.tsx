import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { fetchLotcdWiki, fetchTaxonomy } from '../api/knowledge'
import type { LotcdWikiView, Taxonomy, TopicListItem } from '../types'
import { LotcdWikiPage } from './LotcdWikiPage'

vi.mock('../api/knowledge', () => ({ fetchLotcdWiki: vi.fn(), fetchTaxonomy: vi.fn() }))

const activeTopic: TopicListItem = {
  topic_id: 'T-001',
  title: '4SA D1 불량 추적',
  state: 'investigating',
  importance: 'high',
  primary_area: 'yield_defect',
  target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  teams: ['Yield'],
  last_updated_week: '2026-W30',
  evidence_count: 2,
  rank_reasons: ['최근 2주 내 갱신', '중요도 HIGH'],
}

const closedTopic: TopicListItem = {
  ...activeTopic,
  topic_id: 'T-002',
  title: '4SA 계측 상관성 확인',
  state: 'closed',
  importance: 'medium',
  primary_area: 'quality_analysis',
  last_updated_week: '2026-W24',
  rank_reasons: ['2026-Q2 종료'],
}

const lotcdView: LotcdWikiView = {
  domain: 'DRAM',
  tech: 'Spica',
  lotcd: '4SA',
  summary: '4SA: 2 Topics',
  recent_changes: [activeTopic],
  active_topics: [activeTopic],
  knowledge_areas: { yield_defect: [activeTopic], quality_analysis: [closedTopic] },
  actions_and_decisions: [activeTopic],
  related_lotcds: ['8HBM'],
  closed_topics: { '2026-Q2': [closedTopic] },
  activity: [
    {
      agenda_id: 'A-001', mail_id: 'M-001', team: 'Yield', week: '2026-W30',
      subject: '4SA 수율 점검', source_quote: 'D1 불량을 점검했다.', source_path: 'mail/M-001',
    },
    {
      agenda_id: 'A-002', mail_id: 'M-002', team: 'Process', week: '2026-W30',
      subject: '4SA 공정 점검', source_quote: '공정 조건을 점검했다.', source_path: null,
    },
  ],
  topic_ids: ['T-001', 'T-002'],
}

const taxonomy: Taxonomy = {
  version: 7,
  is_dummy: false,
  notice: '',
  domains: [{
    id: 'dram', name: 'DRAM', techs: [{
      id: 'spica', name: 'Spica', aliases: [], lotcds: [
        { code: '4SA', fab_id: 'F4', product_code: '4SA', product: '4SA Product', aliases: [] },
        { code: '8HBM', fab_id: 'F8', product_code: '8HBM', product: '8HBM Product', aliases: [] },
      ],
    }],
  }],
  group_aliases: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTaxonomy).mockResolvedValue(taxonomy)
  vi.mocked(fetchLotcdWiki).mockResolvedValue(lotcdView)
})

function renderPage(path = '/wiki/lotcd/DRAM/Spica/4SA', route = '/wiki/lotcd/:domain/:tech/:lotcd') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path={route} element={<LotcdWikiPage />} /></Routes>
    </MemoryRouter>,
  )
}

it('renders the fixed LOTCD sections in backend order', async () => {
  renderPage()

  const headings = (await screen.findAllByRole('heading', { level: 2 })).map((node) => node.textContent)
  expect(headings).toEqual([
    '현황 요약', '주요 변화', '진행 중 Topic', '지식 영역', '조치와 의사결정',
    '연관 LOTCD', '해결·종료된 Topic', '출처와 활동 이력',
  ])
})

it('uses backend projection fields without duplicating Topic narrative', async () => {
  renderPage()

  expect(await screen.findByText('4SA: 2 Topics')).toBeInTheDocument()
  expect(screen.getAllByText('최근 2주 내 갱신').length).toBeGreaterThan(0)
  expect(screen.getByRole('heading', { level: 3, name: '수율·불량' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 3, name: '품질·분석' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { level: 3, name: '2026-Q2' })).toBeInTheDocument()
  expect(screen.queryByText(/D1 불량 원인을 분석/)).not.toBeInTheDocument()

  const topicLink = screen.getAllByRole('link', { name: activeTopic.title })[0]
  expect(topicLink).toHaveAttribute(
    'href',
    '/wiki/topics/T-001?from=%2Fwiki%2Flotcd%2FDRAM%2FSpica%2F4SA',
  )
  expect(screen.getByRole('link', { name: '8HBM' })).toHaveAttribute(
    'href',
    '/wiki/lotcd/DRAM/Spica/8HBM',
  )
})

it('groups activity first by week and then by team', async () => {
  renderPage()

  const activity = await screen.findByLabelText('출처와 활동 이력')
  const week = within(activity).getByRole('heading', { level: 3, name: '2026-W30' })
  const weekGroup = week.closest('section')
  expect(weekGroup).not.toBeNull()
  expect(within(weekGroup as HTMLElement).getByRole('heading', { level: 4, name: 'Yield' })).toBeInTheDocument()
  expect(within(weekGroup as HTMLElement).getByRole('heading', { level: 4, name: 'Process' })).toBeInTheDocument()
})

it('loads the wildcard route used by the application shell', async () => {
  renderPage('/wiki/lotcd/DRAM/Spica/4SA', '/wiki/lotcd/*')

  await waitFor(() => expect(fetchLotcdWiki).toHaveBeenCalledWith(
    'DRAM', 'Spica', '4SA', expect.any(AbortSignal),
  ))
})
