import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { fetchTopic, fetchTopics } from './api/knowledge'
import { AppRoutes } from './AppRoutes'
import type { WikiTopicDetail } from './types'

vi.mock('./api/knowledge', () => ({
  fetchTopic: vi.fn(),
  fetchTopics: vi.fn(),
  fetchSession: vi.fn().mockResolvedValue({ user_id: 'reviewer', roles: [], can_edit: false }),
  fetchWikiReviews: vi.fn().mockResolvedValue([]),
}))

const topicDetail: WikiTopicDetail = {
  topic: {
    topic_id: 'T-001', title: '4SA D1 불량', topic_kind: 'issue', primary_area: 'yield_defect',
    secondary_areas: [], state: 'investigating', importance: 'high', first_seen_week: '2026-W30',
    last_updated_week: '2026-W30', target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
    teams: ['Yield'], source_agenda_ids: [], related_topic_ids: [], current_revision_id: 'R-001',
  },
  body_markdown: '', sections: [], claims: [], evidence: [], relations: [],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTopics).mockResolvedValue([])
  vi.mocked(fetchTopic).mockResolvedValue(topicDetail)
})

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  )
}

it('redirects /wiki to the topic index and shows exactly four Wiki modes', async () => {
  renderRoute('/wiki')

  expect(await screen.findByRole('heading', { name: '주제 색인' })).toBeInTheDocument()
  const navigation = screen.getByRole('navigation', { name: 'Wiki 탐색 모드' })
  expect(within(navigation).getAllByRole('link')).toHaveLength(4)
  expect(within(navigation).queryByRole('link', { name: '분류 검토' })).not.toBeInTheDocument()
})

it('loads a topic detail route through the Wiki outlet', async () => {
  renderRoute('/wiki/topics/T-001')

  expect(await screen.findByRole('heading', { name: '4SA D1 불량' })).toBeInTheDocument()
})

it('keeps the review route outside the four-mode navigation', async () => {
  renderRoute('/wiki/reviews')

  expect(await screen.findByRole('heading', { name: '분류 검토' })).toBeInTheDocument()
  const navigation = screen.getByRole('navigation', { name: 'Wiki 탐색 모드' })
  expect(within(navigation).queryByRole('link', { name: '분류 검토' })).not.toBeInTheDocument()
})
