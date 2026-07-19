import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { fetchTopics } from '../api/knowledge'
import type { TopicListItem } from '../types'
import { TopicIndexPage } from './TopicIndexPage'

vi.mock('../api/knowledge', () => ({ fetchTopics: vi.fn() }))

const topic: TopicListItem = {
  topic_id: 'T-001',
  title: '4SA D1 불량 추적',
  state: 'investigating',
  importance: 'high',
  primary_area: 'yield_defect',
  target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  teams: ['수율개선팀', '분석팀'],
  last_updated_week: '2026-W04',
  evidence_count: 3,
  rank_reasons: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTopics).mockResolvedValue([topic])
})

it('loads URL filters and preserves the index location in Topic links', async () => {
  render(
    <MemoryRouter initialEntries={['/wiki/topics?q=D1&state=investigating&area=yield_defect&team=%EC%88%98%EC%9C%A8%EA%B0%9C%EC%84%A0%ED%8C%80&lotcd=4SA']}>
      <TopicIndexPage />
    </MemoryRouter>,
  )

  await waitFor(() => expect(fetchTopics).toHaveBeenCalledWith({
    q: 'D1',
    state: 'investigating',
    area: 'yield_defect',
    team: '수율개선팀',
    lotcd: '4SA',
  }, expect.any(AbortSignal)))
  expect(await screen.findByRole('link', { name: topic.title })).toHaveAttribute(
    'href',
    '/wiki/topics/T-001?from=%2Fwiki%2Ftopics%3Fq%3DD1%26state%3Dinvestigating%26area%3Dyield_defect%26team%3D%25EC%2588%2598%25EC%259C%25A8%25EA%25B0%259C%25EC%2584%25A0%25ED%258C%2580%26lotcd%3D4SA',
  )
  expect(screen.getByText('DRAM / Spica / 4SA')).toBeInTheDocument()
  expect(screen.getByText('수율개선팀 · 분석팀')).toBeInTheDocument()
  expect(screen.getByText('근거 3건')).toBeInTheDocument()
})
