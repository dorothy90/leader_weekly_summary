import { render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import {
  fetchTaxonomy,
  fetchTopics,
  fetchWikiReviews,
  fetchWikiTeams,
  fetchWikiWeeks,
} from '../api/knowledge'
import { parseWikiLocation } from '../components/wiki/wikiLocation'
import { WikiWorkspacePage } from './WikiWorkspacePage'

vi.mock('../api/knowledge', () => ({
  fetchTaxonomy: vi.fn(),
  fetchTopics: vi.fn(),
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
  vi.mocked(fetchWikiTeams).mockResolvedValue({ values: ['Spica수율'] })
  vi.mocked(fetchWikiWeeks).mockResolvedValue({ values: ['2026-W30'] })
  vi.mocked(fetchWikiReviews).mockResolvedValue([])
})

function renderWorkspace(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes><Route path="/wiki/*" element={<WikiWorkspacePage />} /></Routes>
    </MemoryRouter>,
  )
}

it('keeps metadata roots beside Docs and the canonical document', async () => {
  renderWorkspace('/wiki/topics')

  expect(await screen.findByRole('navigation', { name: 'Wiki 도구' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '지식 탐색' })).toBeInTheDocument()
  expect(screen.getByRole('region', { name: 'Wiki 탐색' })).toBeInTheDocument()
  expect(screen.getByRole('article', { name: 'Wiki 문서' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Docs' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Graph' })).toHaveAttribute('aria-pressed', 'false')
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
