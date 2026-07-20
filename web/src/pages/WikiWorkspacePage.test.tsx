import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import {
  fetchTaxonomy,
  fetchTopic,
  fetchTopics,
  fetchWeekWiki,
  fetchWikiBuild,
  fetchWikiGraph,
  fetchWikiReviews,
  fetchWikiTeams,
  fetchWikiWeeks,
} from '../api/knowledge'
import { parseWikiLocation } from '../components/wiki/wikiLocation'
import { WikiWorkspacePage } from './WikiWorkspacePage'

vi.mock('../api/knowledge', () => ({
  fetchTaxonomy: vi.fn(),
  fetchTopic: vi.fn(),
  fetchTopics: vi.fn(),
  fetchWeekWiki: vi.fn(),
  fetchWikiBuild: vi.fn(),
  fetchWikiGraph: vi.fn(),
  fetchWikiReviews: vi.fn(),
  fetchWikiTeams: vi.fn(),
  fetchWikiWeeks: vi.fn(),
}))

beforeEach(() => {
  vi.mocked(fetchTaxonomy).mockResolvedValue({
    version: 1, is_dummy: true, notice: '', group_aliases: [],
    domains: [{ id: 'dram', name: 'DRAM', techs: [{
      id: 'spica', name: 'Spica', aliases: [], lotcds: [{
        code: '4SA', fab_id: '4', product_code: 'SA', product: 'LPDDR5', aliases: [],
      }],
    }] }],
  })
  vi.mocked(fetchTopics).mockResolvedValue([])
  vi.mocked(fetchWikiGraph).mockResolvedValue({ topics: [], relations: [] })
  vi.mocked(fetchWikiTeams).mockResolvedValue({ values: ['Spica수율'] })
  vi.mocked(fetchWikiWeeks).mockResolvedValue({ values: ['2026-W30'] })
  vi.mocked(fetchWeekWiki).mockResolvedValue({
    week: '2026-W30', revision_id: 'REV-030', published_at: '2026-07-20T00:00:00Z',
    build_run_id: 'RUN-030', new_topic_ids: [], changed_topic_ids: [], resolved_topic_ids: [],
    reopened_topic_ids: [], actions_and_decisions: [], new_relation_ids: [],
    pending_assignment_count: 0, contradictions: [], teams: [],
  })
  vi.mocked(fetchWikiBuild).mockResolvedValue({
    run_id: 'RUN-030', week: '2026-W30', classification_run_id: 'CLASS-030', taxonomy_version: 1,
    status: 'partially_failed', input_hash: 'hash', model: 'z-ai/glm-4.7-flash',
    affected_topic_ids: [], failed_topic_ids: ['DEMO-TOPIC-12'], started_at: '2026-07-20T00:00:00Z',
    completed_at: '2026-07-20T00:01:00Z', error: null,
  })
  vi.mocked(fetchWikiReviews).mockResolvedValue([])
})

function renderWorkspace(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/wiki/*" element={<WikiWorkspacePage />} /></Routes>
    </MemoryRouter>,
  )
}

it('renders one synthesized Wiki document in the center for Docs mode', async () => {
  renderWorkspace('/wiki/topics')

  expect(await screen.findByRole('navigation', { name: '주제 Library' })).toBeInTheDocument()
  expect(screen.queryByRole('navigation', { name: 'Wiki 도구' })).not.toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Wiki 탐색' })).toBeInTheDocument()
  expect(screen.getAllByRole('article', { name: 'Wiki 문서' })).toHaveLength(1)
  expect(screen.getByRole('region', { name: 'Wiki 탐색' })).toContainElement(screen.getByRole('article', { name: 'Wiki 문서' }))
  expect(screen.queryByRole('button', { name: 'Wiki 문서 접기' })).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Docs' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Graph' })).toHaveAttribute('aria-pressed', 'false')
  expect(screen.queryByText('최근 빌드')).not.toBeInTheDocument()
})

it('keeps Graph in the center without an empty document side panel', async () => {
  renderWorkspace('/wiki/topics?view=graph')

  expect(await screen.findByRole('heading', { name: 'Wiki Graph' })).toBeInTheDocument()
  expect(screen.queryByRole('article', { name: 'Wiki 문서' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Wiki 문서 접기' })).not.toBeInTheDocument()
})

it('parses a Topic return collection without losing selection', () => {
  expect(parseWikiLocation(
    '/wiki/topics/DEMO-TOPIC-01',
    '?from=%2Fwiki%2Flotcd%2FDRAM%2FSpica%2F4SA',
  )).toMatchObject({
    topicId: 'DEMO-TOPIC-01',
    collectionPath: '/wiki/lotcd/DRAM/Spica/4SA',
    kind: 'lotcd',
  })
})

it('preserves graph mode on a selected Topic route', () => {
  expect(parseWikiLocation(
    '/wiki/topics/DEMO-TOPIC-01',
    '?from=%2Fwiki%2Ftopics&view=graph',
  )).toMatchObject({
    topicId: 'DEMO-TOPIC-01',
    collectionPath: '/wiki/topics',
    view: 'graph',
  })
})
