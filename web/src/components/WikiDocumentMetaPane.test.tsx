import { fireEvent, render, screen, within } from '@testing-library/react'
import { vi } from 'vitest'

import type { CategoryWikiPage } from '../types'
import { WikiDocumentMetaPane } from './WikiDocumentMetaPane'

const page: CategoryWikiPage = {
  category_id: 'lotcd:4sa',
  page_kind: 'latest',
  doc_type: 'canonical',
  canonical_id: 'dram/spica/4sa',
  level: 'lotcd',
  domain: 'DRAM',
  tech: 'Spica',
  lotcd: '4SA',
  title: '4SA',
  product: 'LPDDR5 24G',
  fab_id: '4',
  aliases: [],
  as_of_week: '2026-W28',
  current_body_markdown: '## 개요\n\n현재 상태',
  weekly_history: [],
  body_markdown: '# 4SA',
  citation_map: [
    { mail_id: 'mail-1', agenda_ids: ['agenda-1'], source_doc_ids: ['chunk-1'], used_in_sections: ['개요'], category_paths: ['dram/spica/4sa'] },
    { mail_id: 'mail-2', agenda_ids: ['agenda-2'], source_doc_ids: ['chunk-2'], used_in_sections: ['개요'], category_paths: ['dram/spica/4sa'] },
  ],
  child_page_ids: ['dram/spica/4sa-child'],
  confidence: 'high',
  agenda_count: 1,
  issue_ids: ['issue:4sa-yield'],
  open_issue_ids: ['legacy-open'],
  resolved_issue_ids: [],
  open_issue_count: 7,
  resolved_issue_count: 3,
  contradictions: ['메일 간 목표 수치가 다릅니다.'],
  generation_review_items: ['분류 경로 확인이 필요합니다.'],
  review_agenda_ids: [],
  source_agenda_ids: ['agenda-1'],
  source_doc_ids: ['chunk-1'],
  source_hash: 'hash',
  taxonomy_version: 1,
  schema_version: 2,
  generation_strategy: 'incremental_merge',
  generated_at: '2026-07-12T00:00:00Z',
  updated_at: '2026-07-12T00:00:00Z',
}

describe('WikiDocumentMetaPane', () => {
  it('shows outline, canonical metrics, review items, and related page navigation', () => {
    const onNavigate = vi.fn()
    render(
      <WikiDocumentMetaPane
        page={page}
        headings={[{ id: '개요', label: '개요' }]}
        onNavigate={onNavigate}
      />,
    )

    expect(screen.getByRole('link', { name: '개요' })).toHaveAttribute('href', '#개요')

    const metrics = screen.getByRole('heading', { name: 'Metrics' }).closest('section')
    expect(metrics).not.toBeNull()
    expect(within(metrics!).getByText('진행 이슈').nextSibling).toHaveTextContent('7')
    expect(within(metrics!).getByText('해결 이슈').nextSibling).toHaveTextContent('3')
    expect(within(metrics!).getByText('출처').nextSibling).toHaveTextContent('2')
    expect(within(metrics!).getByText('기준 주차').nextSibling).toHaveTextContent('2026-W28')

    expect(screen.getByText('high')).toBeInTheDocument()
    expect(screen.getByText('dram/spica/4sa')).toBeInTheDocument()
    expect(screen.getByText('메일 간 목표 수치가 다릅니다.')).toBeInTheDocument()
    expect(screen.getByText('분류 경로 확인이 필요합니다.')).toBeInTheDocument()

    const parentButton = screen.getByRole('button', { name: 'dram/spica' })
    const childButton = screen.getByRole('button', { name: 'dram/spica/4sa-child' })
    expect(within(parentButton).getByText('dram/spica', { selector: 'strong' })).toBeInTheDocument()
    expect(within(childButton).getByText('dram/spica/4sa-child', { selector: 'strong' })).toBeInTheDocument()

    fireEvent.click(parentButton)
    fireEvent.click(childButton)
    expect(onNavigate).toHaveBeenNthCalledWith(1, 'dram/spica')
    expect(onNavigate).toHaveBeenNthCalledWith(2, 'dram/spica/4sa-child')
  })

  it('hides review content when there are no contradictions or review items', () => {
    render(
      <WikiDocumentMetaPane
        page={{ ...page, contradictions: [], generation_review_items: [] }}
        headings={[]}
        onNavigate={vi.fn()}
      />,
    )

    expect(screen.queryByRole('heading', { name: 'Review' })).not.toBeInTheDocument()
  })
})
