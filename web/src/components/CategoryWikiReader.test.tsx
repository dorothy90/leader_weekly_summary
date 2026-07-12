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
  it('renders stored markdown and opens its source agenda', () => {
    const onSelectAgenda = vi.fn()
    render(
      <CategoryWikiReader
        page={page}
        loading={false}
        error={null}
        onSelectAgenda={onSelectAgenda}
      />,
    )

    expect(screen.getByRole('heading', { name: '진행 중 이슈' })).toBeInTheDocument()
    expect(screen.queryByText('category_id: lotcd:4sa')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'agenda-w27' }))
    expect(onSelectAgenda).toHaveBeenCalledWith('agenda-w27')
  })
})
