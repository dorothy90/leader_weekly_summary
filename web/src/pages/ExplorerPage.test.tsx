import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchAgendaDetail,
  fetchAgendaRevisions,
  fetchAgendas,
  fetchCounts,
  fetchFacets,
  fetchReviewCount,
  fetchSession,
  fetchTaxonomy,
  fetchWikiCitation,
  fetchWikiPage,
  fetchWikiPageSummaries,
} from '../api/knowledge'
import type {
  CategoryWikiPage,
  Taxonomy,
  WikiCitationDetail,
  WikiPageSummary,
} from '../types'
import { ExplorerPage } from './ExplorerPage'

vi.mock('../api/knowledge', () => ({
  confirmClassification: vi.fn(),
  fetchAgendaDetail: vi.fn(),
  fetchAgendaRevisions: vi.fn(),
  fetchAgendas: vi.fn(),
  fetchCounts: vi.fn(),
  fetchFacets: vi.fn(),
  fetchReviewCount: vi.fn(),
  fetchSession: vi.fn(),
  fetchTaxonomy: vi.fn(),
  fetchWikiCitation: vi.fn(),
  fetchWikiPage: vi.fn(),
  fetchWikiPageSummaries: vi.fn(),
  holdClassification: vi.fn(),
}))

const taxonomy: Taxonomy = {
  version: 1,
  is_dummy: false,
  notice: '',
  group_aliases: [],
  domains: [
    {
      id: 'dram',
      name: 'DRAM',
      techs: [{
        id: 'spica',
        name: 'Spica',
        aliases: ['SP'],
        lotcds: [{
          code: '4SA',
          fab_id: '4',
          product_code: 'SA',
          product: 'LPDDR5 24G',
          aliases: ['SP LPDDR5 24G'],
        }],
      }],
    },
    {
      id: 'nand',
      name: 'NAND',
      techs: [{
        id: 'v9',
        name: 'V9',
        aliases: [],
        lotcds: [{
          code: 'N9A',
          fab_id: 'N',
          product_code: '9A',
          product: 'TLC 1T',
          aliases: [],
        }],
      }],
    },
  ],
}

const page: CategoryWikiPage = {
  category_id: 'lotcd:4sa',
  page_kind: 'latest',
  doc_type: 'canonical',
  canonical_id: 'dram/spica/4sa',
  level: 'lotcd',
  domain: 'DRAM',
  tech: 'Spica',
  lotcd: '4SA',
  title: '4SA canonical',
  product: 'LPDDR5 24G',
  fab_id: '4',
  aliases: ['SP LPDDR5 24G'],
  as_of_week: '2026-W28',
  current_body_markdown: '## 개요\n\n근거 [mail:mail-1] 및 [mail:mail-2]',
  weekly_history: [],
  body_markdown: '# 4SA canonical',
  citation_map: [
    { mail_id: 'mail-1', agenda_ids: [], used_in_sections: ['개요'], category_paths: ['dram/spica/4sa'] },
    { mail_id: 'mail-2', agenda_ids: [], used_in_sections: ['개요'], category_paths: ['dram/spica/4sa'] },
  ],
  child_page_ids: ['nand/v9/n9a'],
  confidence: 'high',
  agenda_count: 2,
  open_issue_ids: ['issue-1'],
  resolved_issue_ids: [],
  open_issue_count: 1,
  resolved_issue_count: 0,
  contradictions: [],
  generation_review_items: [],
  review_agenda_ids: [],
  source_agenda_ids: [],
  source_doc_ids: [],
  source_hash: 'hash',
  taxonomy_version: 1,
  generated_at: '2026-07-12T00:00:00Z',
  updated_at: '2026-07-12T00:00:00Z',
}

const summary: WikiPageSummary = {
  category_id: page.category_id,
  canonical_id: page.canonical_id,
  level: page.level,
  domain: page.domain,
  tech: page.tech,
  lotcd: page.lotcd,
  title: page.title,
  as_of_week: page.as_of_week,
  open_issue_count: 3,
  resolved_issue_count: 0,
  confidence: page.confidence,
  review_item_count: 0,
}

function citationDetail(mailId: string, subject: string): WikiCitationDetail {
  return {
    mail: {
      id: mailId,
      subject,
      sender_team: '공정기술팀',
      sender: '홍길동',
      received_at: '2026-07-10T03:00:00Z',
      body: '검증 근거',
      reply_to: null,
    },
    agendas: [],
    used_in_sections: ['개요'],
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((nextResolve) => {
    resolve = nextResolve
  })
  return { promise, resolve }
}

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="current path">{location.pathname}</output>
}

function renderPage(initialEntry: string, classic = false) {
  const route = classic
    ? '/explorer/:domain?/:tech?/:lotcd?'
    : '/wiki/docs/:domain?/:tech?/:lotcd?'
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <Routes>
        <Route
          path={route}
          element={
            <>
              <ExplorerPage classic={classic} />
              <LocationProbe />
            </>
          }
        />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTaxonomy).mockResolvedValue(taxonomy)
  vi.mocked(fetchCounts).mockResolvedValue([{
    path: { domain: 'DRAM', tech: 'Spica', lotcd: '4SA' },
    direct: 9,
    descendants: 9,
  }])
  vi.mocked(fetchReviewCount).mockResolvedValue(0)
  vi.mocked(fetchFacets).mockResolvedValue({
    topics: [],
    states: [],
    sender_teams: [],
    date_min: null,
    date_max: null,
  })
  vi.mocked(fetchSession).mockResolvedValue({
    user_id: 'reader@example.com',
    roles: ['reader'],
    can_edit: false,
  })
  vi.mocked(fetchAgendas).mockResolvedValue({ items: [], total: 0 })
  vi.mocked(fetchAgendaDetail).mockRejectedValue(new Error('not requested'))
  vi.mocked(fetchAgendaRevisions).mockRejectedValue(new Error('not requested'))
  vi.mocked(fetchWikiPage).mockResolvedValue(page)
  vi.mocked(fetchWikiPageSummaries).mockResolvedValue({ items: [summary] })
})

describe('ExplorerPage canonical reader integration', () => {
  it('coordinates summaries, document context, citation requests, and canonical routes', async () => {
    const staleCitation = deferred<WikiCitationDetail>()
    vi.mocked(fetchWikiCitation).mockImplementation((_categoryId, mailId) => (
      mailId === 'mail-1'
        ? staleCitation.promise
        : Promise.resolve(citationDetail('mail-2', '최신 메일 근거'))
    ))

    renderPage('/wiki/docs/dram/spica/4sa?q=SP+LPDDR5+24G')

    expect(await screen.findByRole('heading', { name: page.title })).toBeInTheDocument()
    expect(fetchWikiPageSummaries).toHaveBeenCalledTimes(1)
    expect(within(screen.getByRole('button', { name: /4SA/ })).getByText('3')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Metrics' })).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: '개요' })).toHaveAttribute('href', '#개요')

    fireEvent.click(screen.getByRole('button', { name: 'mail:mail-1' }))
    await waitFor(() => {
      expect(fetchWikiCitation).toHaveBeenCalledWith(
        page.category_id,
        'mail-1',
        expect.any(AbortSignal),
      )
    })
    expect(screen.getByRole('dialog', { name: '메일 근거' })).toHaveTextContent('메일 근거 불러오는 중')

    fireEvent.click(screen.getByRole('button', { name: 'mail:mail-2' }))
    expect(await screen.findByText('최신 메일 근거')).toBeInTheDocument()

    await act(async () => {
      staleCitation.resolve(citationDetail('mail-1', '오래된 메일 근거'))
      await staleCitation.promise
    })
    expect(screen.queryByText('오래된 메일 근거')).not.toBeInTheDocument()
    expect(screen.getByText('최신 메일 근거')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '메일 근거 닫기' }))
    expect(screen.queryByRole('dialog', { name: '메일 근거' })).not.toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'current path' })).toHaveTextContent('/wiki/docs/dram/spica/4sa')

    fireEvent.click(screen.getByRole('button', { name: 'nand/v9/n9a' }))
    await waitFor(() => {
      expect(screen.getByRole('status', { name: 'current path' })).toHaveTextContent('/wiki/docs/nand/v9/n9a')
      expect(fetchWikiPage).toHaveBeenLastCalledWith(
        { domain: 'NAND', tech: 'V9', lotcd: 'N9A' },
        expect.any(AbortSignal),
      )
    })
  })

  it('keeps summary loading and summary props out of classic Explorer', async () => {
    renderPage('/explorer/dram/spica/4sa', true)

    expect(await screen.findByRole('button', { name: /4SA/ })).toBeInTheDocument()
    expect(within(screen.getByRole('button', { name: /4SA/ })).getByText('9')).toBeInTheDocument()
    expect(fetchWikiPageSummaries).not.toHaveBeenCalled()
  })
})
