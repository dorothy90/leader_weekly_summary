import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { ScopeToggle } from './ScopeToggle'

describe('ScopeToggle', () => {
  it('reports direct scope selection', () => {
    const onChange = vi.fn()
    render(
      <ScopeToggle value="descendants" onChange={onChange} disabled={false} />,
    )

    fireEvent.click(screen.getByRole('button', { name: '직접 언급' }))
    expect(onChange).toHaveBeenCalledWith('direct')
  })

  it('disables direct scope without a selected category', () => {
    render(
      <ScopeToggle value="descendants" onChange={() => undefined} disabled />,
    )

    expect(screen.getByRole('button', { name: '직접 언급' })).toBeDisabled()
  })
})
