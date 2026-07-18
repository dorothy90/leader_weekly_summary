import { fireEvent, render, screen, within } from '@testing-library/react'
import { vi } from 'vitest'

import { ClassificationItemList } from './ClassificationItemList'
import type { ClassificationItem } from '../types'

const baseItem: ClassificationItem = {
  agenda_id: 'agenda-confirmed',
  mail_id: 'mail-1',
  summary: 'Edge defect 증가',
  source_quote: '4SA Edge defect 증가',
  classification_context: '4SA Edge defect 증가',
  item_kind: 'lotcd_specific',
  decision: {
    status: 'confirmed',
    target_path: { domain: 'DRAM', tech: 'Spica', lotcd: '4SA' },
    matches: [
      {
        phrase: '4SA',
        lotcd: '4SA',
        match_type: 'canonical',
        rule_id: 'canonical:4SA',
        score: 1,
      },
    ],
    diagnostics: [],
    confidence: 1,
  },
  revision_count: 0,
}

const aggregateItem: ClassificationItem = {
  ...baseItem,
  agenda_id: 'agenda-aggregate',
  summary: '전체 수율 현황',
  item_kind: 'aggregate',
  decision: {
    status: 'aggregate',
    target_path: null,
    matches: [],
    diagnostics: ['AGGREGATE_METRIC'],
    confidence: 1,
  },
}

const conflictItem: ClassificationItem = {
  ...baseItem,
  agenda_id: 'agenda-conflict',
  summary: '공정 조건 확인',
  decision: {
    status: 'conflict',
    target_path: null,
    matches: [],
    diagnostics: ['MULTIPLE_LOTCD_CONFLICT'],
    confidence: 0,
  },
}

describe('ClassificationItemList', () => {
  it('renders a semantic table with one classification label and trace reason', () => {
    render(
      <ClassificationItemList
        items={[baseItem, aggregateItem, conflictItem]}
        selectedId="agenda-conflict"
        query=""
        onQueryChange={() => undefined}
        onSelect={() => undefined}
      />,
    )

    expect(screen.getByRole('table', { name: '분류 항목 목록' })).toBeInTheDocument()
    expect(screen.getByRole('columnheader', { name: 'LOTCD' })).toBeInTheDocument()
    expect(screen.getByText('4SA · canonical:4SA')).toBeInTheDocument()
    expect(
      within(screen.getByRole('row', { name: /전체 수율 현황/ })).getAllByText(
        '종합지표',
      ),
    ).toHaveLength(2)
    expect(screen.getAllByText('미분류')).toHaveLength(1)
    expect(screen.getByText('MULTIPLE_LOTCD_CONFLICT')).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /공정 조건 확인/ })).toHaveAttribute(
      'aria-selected',
      'true',
    )
  })

  it('exposes controlled search and keyboard-accessible row selection', () => {
    const onQueryChange = vi.fn()
    const onSelect = vi.fn()
    render(
      <ClassificationItemList
        items={[baseItem]}
        selectedId={null}
        query="Edge"
        onQueryChange={onQueryChange}
        onSelect={onSelect}
      />,
    )

    const search = screen.getByRole('searchbox', { name: '분류 항목 검색' })
    expect(search).toHaveValue('Edge')
    fireEvent.change(search, { target: { value: '수율' } })
    expect(onQueryChange).toHaveBeenCalledWith('수율')

    const row = screen.getByRole('row', { name: /Edge defect 증가/ })
    expect(row).toHaveAttribute('tabindex', '0')
    fireEvent.keyDown(row, { key: 'Enter' })
    fireEvent.keyDown(row, { key: ' ' })
    expect(onSelect).toHaveBeenNthCalledWith(1, 'agenda-confirmed')
    expect(onSelect).toHaveBeenNthCalledWith(2, 'agenda-confirmed')
  })
})
