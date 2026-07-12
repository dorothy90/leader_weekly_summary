import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import type { CategoryWikiPage } from '../types'
import { CategoryWikiReader } from './CategoryWikiReader'

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
  aliases: ['SP LPDDR5 24G'],
  as_of_week: '2026-28',
  current_body_markdown: '## 개요\n\n현재 상태 [mail:mail-1]',
  weekly_history: [],
  body_markdown: `---
category_id: lotcd:4sa
---

# 4SA

## 진행 중 이슈

- 조건 변경 결과 확인 대기 [[agenda-w27]]`,
  citation_map: [],
  child_page_ids: [],
  confidence: 'high',
  agenda_count: 1,
  open_issue_ids: ['issue:4sa-yield'],
  resolved_issue_ids: [],
  open_issue_count: 1,
  resolved_issue_count: 0,
  contradictions: [],
  generation_review_items: [],
  review_agenda_ids: [],
  source_agenda_ids: ['agenda-w27'],
  source_doc_ids: ['chunk-1'],
  source_hash: 'hash',
  taxonomy_version: 1,
  generated_at: '2026-07-12T00:00:00Z',
  updated_at: '2026-07-12T00:00:00Z',
}

describe('CategoryWikiReader', () => {
  it('renders narrative sections and opens an inline mail citation', () => {
    const onSelectCitation = vi.fn()
    const onOutlineChange = vi.fn()
    render(
      <CategoryWikiReader
        page={page}
        loading={false}
        error={null}
        onSelectCitation={onSelectCitation}
        onOutlineChange={onOutlineChange}
      />,
    )

    expect(screen.getByRole('heading', { name: '개요' })).toHaveAttribute('id', '개요')
    fireEvent.click(screen.getByRole('button', { name: 'mail:mail-1' }))
    expect(onSelectCitation).toHaveBeenCalledWith('mail-1')
    expect(onOutlineChange).toHaveBeenCalledWith([
      { id: '개요', label: '개요' },
      { id: '현재-상태와-주요-변화', label: '현재 상태와 주요 변화' },
      { id: '원인과-영향-관계', label: '원인과 영향 관계' },
      { id: '조치와-효과', label: '조치와 효과' },
      { id: '펜딩-이슈와-의사결정', label: '펜딩 이슈와 의사결정' },
      { id: '누적-지식', label: '누적 지식' },
    ])
  })

  it('expands the newest week and collapses older weeks', () => {
    const onOutlineChange = vi.fn()
    const historyPage = {
      ...page,
      weekly_history: [
        { week: '2026-W28', body_markdown: '이번 주 [mail:mail-1]', source_mail_ids: ['mail-1'] },
        { week: '2026-W27', body_markdown: '지난 주 [mail:mail-1]', source_mail_ids: ['mail-1'] },
      ],
    }
    const { container } = render(
      <CategoryWikiReader
        page={historyPage}
        loading={false}
        error={null}
        onSelectCitation={vi.fn()}
        onOutlineChange={onOutlineChange}
      />,
    )

    const details = Array.from(container.querySelectorAll('details'))
    expect(details).toHaveLength(2)
    expect(details[0]).toHaveAttribute('open')
    expect(details[1]).not.toHaveAttribute('open')
    expect(screen.getByRole('heading', { name: '주차별 업데이트 이력' })).toHaveAttribute(
      'id',
      'weekly-history-title',
    )
    expect(onOutlineChange).toHaveBeenCalledWith(
      expect.arrayContaining([
        { id: 'weekly-history-title', label: '주차별 업데이트 이력' },
      ]),
    )
  })

  it('keeps legacy body markdown readable without agenda buttons', () => {
    render(
      <CategoryWikiReader
        page={{ ...page, current_body_markdown: '' }}
        loading={false}
        error={null}
        onSelectCitation={vi.fn()}
        onOutlineChange={vi.fn()}
      />,
    )

    expect(screen.getByRole('heading', { name: '진행 중 이슈' })).toBeInTheDocument()
    expect(screen.getByText(/조건 변경 결과 확인 대기/)).toHaveTextContent('[[agenda-w27]]')
    expect(screen.queryByRole('button', { name: 'agenda-w27' })).not.toBeInTheDocument()
  })

  it('renders safely with the required reader callbacks', () => {
    render(
      <CategoryWikiReader
        page={page}
        loading={false}
        error={null}
        onSelectCitation={vi.fn()}
        onOutlineChange={vi.fn()}
      />,
    )

    expect(screen.getByRole('heading', { name: '개요' })).toBeInTheDocument()
  })
})
