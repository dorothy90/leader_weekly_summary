import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import {
  approveClassificationWeek,
  fetchClassificationItem,
  fetchClassificationItems,
  fetchClassificationWeeks,
  fetchSession,
  fetchTaxonomy,
} from '../api/knowledge'
import { ClassificationWorkbenchPage } from './ClassificationWorkbenchPage'
import type { ClassificationItem, ClassificationWeek, Taxonomy } from '../types'

vi.mock('../api/knowledge', () => ({
  fetchClassificationWeeks: vi.fn(), fetchClassificationItems: vi.fn(),
  fetchClassificationItem: vi.fn(), fetchTaxonomy: vi.fn(), fetchSession: vi.fn(),
  correctClassificationItem: vi.fn(), setClassificationDisposition: vi.fn(),
  splitClassificationItem: vi.fn(), createClassificationAlias: vi.fn(),
  runClassificationWeek: vi.fn(), approveClassificationWeek: vi.fn(),
}))

const taxonomy: Taxonomy = { version: 1, is_dummy: false, notice: '', group_aliases: [], domains: [
  { id: 'dram', name: 'DRAM', techs: [{ id: 'spica', name: 'Spica', aliases: [], lotcds: [
    { code: '4SA', fab_id: '4', product_code: 'SA', product: 'LPDDR5', aliases: [] },
  ] }] },
] }
const item: ClassificationItem = {
  agenda_id: 'agenda-1', mail_id: 'mail-1', summary: 'Edge defect 증가', source_quote: '4SA Edge defect 증가',
  classification_context: '주간 품질', item_kind: 'lotcd_specific', revision_count: 0,
  decision: { status: 'conflict', target_path: null, matches: [], diagnostics: ['MULTIPLE_LOTCD_CONFLICT'], confidence: 0.4 },
}
const week: ClassificationWeek = { week: '2026-01', workflow_state: 'review_in_progress', active_run_id: 'run-1', counts: { conflict: 1 } }

describe('ClassificationWorkbenchPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchTaxonomy).mockResolvedValue(taxonomy)
    vi.mocked(fetchSession).mockResolvedValue({ user_id: 'reviewer', roles: ['knowledge-editor'], can_edit: true })
    vi.mocked(fetchClassificationWeeks).mockResolvedValue([week])
    vi.mocked(fetchClassificationItems).mockResolvedValue({ items: [item], total: 1 })
    vi.mocked(fetchClassificationItem).mockResolvedValue(item)
    vi.mocked(approveClassificationWeek).mockResolvedValue(week)
  })

  it('loads the earliest open week and shows selected item details', async () => {
    render(<ClassificationWorkbenchPage />)
    expect(await screen.findByText('2026-W01')).toBeInTheDocument()
    fireEvent.click(await screen.findByText(item.summary))
    expect(await screen.findByText(item.source_quote)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '검수 완료' })).toBeDisabled()
  })

  it('approves a resolved week and refreshes summaries and items', async () => {
    const ready = { ...week, workflow_state: 'ready_for_approval' as const, counts: { confirmed: 3 } }
    vi.mocked(fetchClassificationWeeks).mockResolvedValue([ready])
    vi.mocked(approveClassificationWeek).mockResolvedValue(ready)
    render(<ClassificationWorkbenchPage />)
    fireEvent.click(await screen.findByRole('button', { name: '검수 완료' }))
    await waitFor(() => expect(approveClassificationWeek).toHaveBeenCalledWith('2026-01'))
    await waitFor(() => expect(fetchClassificationWeeks).toHaveBeenCalledTimes(2))
    await waitFor(() => expect(fetchClassificationItems).toHaveBeenCalledTimes(2))
  })
})
