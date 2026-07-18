import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import { ClassificationItemDetail } from './ClassificationItemDetail'
import type { ClassificationItem, Taxonomy } from '../types'

const taxonomy: Taxonomy = {
  version: 1,
  is_dummy: false,
  notice: '',
  group_aliases: [],
  domains: [{ id: 'dram', name: 'DRAM', techs: [{ id: 'spica', name: 'Spica', aliases: [], lotcds: [
    { code: '4SA', fab_id: '4', product_code: 'SA', product: 'LPDDR5', aliases: [] },
    { code: '6SA', fab_id: '6', product_code: 'SA', product: 'LPDDR5', aliases: [] },
  ] }] }],
}

const item: ClassificationItem = {
  agenda_id: 'agenda-1', mail_id: 'mail-1', summary: '4SA 수율',
  source_quote: '4SA 수율 91.2, 6SA 수율 92.4',
  classification_context: '주간 수율 현황', item_kind: 'lotcd_specific', revision_count: 2,
  decision: { status: 'conflict', target_path: null, confidence: 0.5,
    matches: [{ phrase: '4SA', lotcd: '4SA', match_type: 'canonical', rule_id: 'canonical:4SA', score: 1 }],
    diagnostics: ['MULTIPLE_LOTCD_CONFLICT'] },
}

describe('ClassificationItemDetail', () => {
  it('shows trace and keeps correction separate from alias learning', async () => {
    const onCorrect = vi.fn().mockResolvedValue(undefined)
    const onCreateAlias = vi.fn().mockResolvedValue(undefined)
    render(<ClassificationItemDetail item={item} taxonomy={taxonomy} canEdit
      onCorrect={onCorrect} onDisposition={vi.fn()} onSplit={vi.fn()} onCreateAlias={onCreateAlias} />)

    expect(screen.getByText(item.source_quote)).toBeInTheDocument()
    expect(screen.getByText('canonical:4SA')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('combobox', { name: 'LOTCD' }), { target: { value: '4SA' } })
    fireEvent.change(screen.getByLabelText('수정 사유'), { target: { value: '원문 확인' } })
    fireEvent.click(screen.getByRole('button', { name: '이 항목만 수정' }))
    await waitFor(() => expect(onCorrect).toHaveBeenCalledWith('4SA', '원문 확인'))

    fireEvent.change(screen.getByLabelText('유의어 문구'), { target: { value: '엣지 수율' } })
    fireEvent.change(screen.getByLabelText('유의어 LOTCD'), { target: { value: '4SA' } })
    fireEvent.click(screen.getByRole('button', { name: '유의어 등록' }))
    await waitFor(() => expect(onCreateAlias).toHaveBeenCalled())
    expect(onCorrect).toHaveBeenCalledTimes(1)
  })

  it('preserves entered values after a server failure', async () => {
    render(<ClassificationItemDetail item={item} taxonomy={taxonomy} canEdit
      onCorrect={vi.fn().mockRejectedValue(new Error('저장 실패'))}
      onDisposition={vi.fn()} onSplit={vi.fn()} onCreateAlias={vi.fn()} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'LOTCD' }), { target: { value: '4SA' } })
    fireEvent.change(screen.getByLabelText('수정 사유'), { target: { value: '원문 확인' } })
    fireEvent.click(screen.getByRole('button', { name: '이 항목만 수정' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('저장 실패')
    expect(screen.getByLabelText('수정 사유')).toHaveValue('원문 확인')
  })
})
