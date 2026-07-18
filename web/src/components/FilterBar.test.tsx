import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { FilterBar } from './FilterBar'

const facets = {
  topics: ['yield'],
  states: ['open'],
  sender_teams: ['Spica수율'],
  date_min: '2026-07-01',
  date_max: '2026-07-31',
}

const emptyFilters = {
  topic: '',
  state: '',
  senderTeam: '',
  dateFrom: '',
  dateTo: '',
  reviewStatus: '' as const,
}

describe('FilterBar', () => {
  it('reports filter changes by stable field name', () => {
    const onChange = vi.fn()
    render(
      <FilterBar
        facets={facets}
        filters={emptyFilters}
        onChange={onChange}
        onReset={() => undefined}
      />,
    )

    fireEvent.change(screen.getByRole('combobox', { name: '발신팀' }), {
      target: { value: 'Spica수율' },
    })

    expect(onChange).toHaveBeenCalledWith('senderTeam', 'Spica수율')
  })

  it('enables reset when a filter is active', () => {
    const onReset = vi.fn()
    render(
      <FilterBar
        facets={facets}
        filters={{ ...emptyFilters, topic: 'yield' }}
        onChange={() => undefined}
        onReset={onReset}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '초기화 1' }))
    expect(onReset).toHaveBeenCalledOnce()
  })
})
