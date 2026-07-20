import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { vi } from 'vitest'

import { fetchTopic, fetchTopics } from './api/knowledge'
import { AppRoutes } from './AppRoutes'
import type { WikiTopicDetail } from './types'

vi.mock('./api/knowledge', () => ({
  fetchTopic: vi.fn(),
  fetchTopics: vi.fn(),
  fetchTaxonomy: vi.fn().mockResolvedValue({ version: 1, is_dummy: true, notice: '', domains: [], group_aliases: [] }),
  fetchWikiTeams: vi.fn().mockResolvedValue({ values: [] }),
  fetchWikiWeeks: vi.fn().mockResolvedValue({ values: [] }),
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

it('redirects /wiki to the persistent Wiki workspace', async () => {
  renderRoute('/wiki')

  expect(await screen.findByRole('navigation', { name: 'Wiki 도구' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '지식 탐색' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Wiki 탐색' })).toBeInTheDocument()
  expect(screen.getByRole('article', { name: 'Wiki 문서' })).toBeInTheDocument()
})

it('loads a topic route without leaving the Wiki workspace', async () => {
  renderRoute('/wiki/topics/T-001')

  expect(await screen.findByRole('article', { name: 'Wiki 문서' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '지식 탐색' })).toBeInTheDocument()
})

it('keeps the operator review route available', async () => {
  renderRoute('/wiki/reviews')

  expect(await screen.findByRole('heading', { name: '분류 검토' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: /Weekly Knowledge Wiki/ })).toHaveAttribute('href', '/wiki/topics')
})
