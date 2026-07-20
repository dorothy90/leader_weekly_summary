import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, vi } from 'vitest'

import { fetchTaxonomy, fetchWikiTeams, fetchWikiWeeks } from '../../api/knowledge'
import { KnowledgeTree } from './KnowledgeTree'

vi.mock('../../api/knowledge', () => ({
  fetchTaxonomy: vi.fn(), fetchWikiTeams: vi.fn(), fetchWikiWeeks: vi.fn(),
}))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTaxonomy).mockResolvedValue({
    version: 1, is_dummy: false, notice: '', group_aliases: [],
    domains: [{ id: 'dram', name: 'DRAM', techs: [{
      id: 'spica', name: 'Spica', aliases: [],
      lotcds: [{ code: '4SA', fab_id: '4', product_code: 'SA', product: 'LPDDR5', aliases: [] }],
    }] }],
  })
  vi.mocked(fetchWikiTeams).mockResolvedValue({ values: ['Spica수율'] })
  vi.mocked(fetchWikiWeeks).mockResolvedValue({ values: ['2026-W30'] })
})

function renderTree(kind: 'topics' | 'lotcd' | 'team' | 'week') {
  return render(<MemoryRouter><KnowledgeTree activePath="/wiki/topics" kind={kind} collapsed={false} onCollapse={vi.fn()} /></MemoryRouter>)
}

it('shows only the selected Library mode', async () => {
  renderTree('team')

  expect(await screen.findByRole('navigation', { name: '팀 Library' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Spica수율' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '전체 주제' })).not.toBeInTheDocument()
  expect(screen.queryByRole('link', { name: '2026-W30' })).not.toBeInTheDocument()
  expect(fetchWikiTeams).toHaveBeenCalledOnce()
  expect(fetchTaxonomy).not.toHaveBeenCalled()
  expect(fetchWikiWeeks).not.toHaveBeenCalled()
})

it('makes Domain, Tech, and LOTCD nodes navigable documents', async () => {
  renderTree('lotcd')

  await waitFor(() => expect(fetchTaxonomy).toHaveBeenCalledOnce())
  expect(screen.getByRole('link', { name: 'DRAM' })).toHaveAttribute('href', '/wiki/lotcd/DRAM')
  expect(screen.getByRole('link', { name: 'Spica' })).toHaveAttribute('href', '/wiki/lotcd/DRAM/Spica')
  expect(screen.getByRole('link', { name: '4SA' })).toHaveAttribute('href', '/wiki/lotcd/DRAM/Spica/4SA')
  expect(screen.queryByRole('link', { name: '전체 주제' })).not.toBeInTheDocument()
})
