import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { ClassificationWeekNav } from './ClassificationWeekNav'
import type { ClassificationWeek } from '../types'

const weeks: ClassificationWeek[] = [
  {
    week: '2026-02',
    workflow_state: 'approved',
    active_run_id: 'run-2',
    counts: { confirmed: 30 },
  },
  {
    week: '2026-01',
    workflow_state: 'review_in_progress',
    active_run_id: 'run-1',
    counts: { confirmed: 28, conflict: 2, unclassified: 1 },
  },
]

describe('ClassificationWeekNav', () => {
  it('renders chronological Korean week states and unresolved counts', () => {
    render(
      <ClassificationWeekNav
        weeks={weeks}
        selectedWeek="2026-01"
        lotcdCounts={{ '4SA': 12, '6SA': 9 }}
        selectedLotcd=""
        selectedStatus=""
        onWeekChange={() => undefined}
        onLotcdChange={() => undefined}
        onStatusChange={() => undefined}
      />,
    )

    const weekButtons = screen.getAllByRole('button', { name: /2026-W0[12]/ })
    expect(weekButtons[0]).toHaveAccessibleName(/2026-W01.*검토 중.*미해결 3건/)
    expect(weekButtons[0]).toHaveAttribute('aria-pressed', 'true')
    expect(weekButtons[1]).toHaveAccessibleName(/2026-W02.*승인 완료/)
  })

  it('reports LOTCD and review status filter changes without Multi-LOTCD', () => {
    const onLotcdChange = vi.fn()
    const onStatusChange = vi.fn()
    render(
      <ClassificationWeekNav
        weeks={weeks}
        selectedWeek="2026-01"
        lotcdCounts={{ '4SA': 12, 'Multi-LOTCD': 4, multi_lotcd: 3, '6SA': 9 }}
        selectedLotcd="4SA"
        selectedStatus=""
        onWeekChange={vi.fn()}
        onLotcdChange={onLotcdChange}
        onStatusChange={onStatusChange}
      />,
    )

    expect(screen.getByRole('button', { name: '4SA 12건' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    fireEvent.click(screen.getByRole('button', { name: '6SA 9건' }))
    expect(onLotcdChange).toHaveBeenCalledWith('6SA')

    fireEvent.click(screen.getByRole('button', { name: '검토 필요' }))
    expect(onStatusChange).toHaveBeenCalledWith('review_required')
    expect(screen.queryByText(/multi[-_\s]?lotcd/i)).not.toBeInTheDocument()
  })
})
