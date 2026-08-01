import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { RequestPanel } from '../RequestPanel'

describe('RequestPanel', () => {
  it('submits only explicit Deep mode with normalized facets', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<RequestPanel disabled={false} onSubmit={onSubmit} />)

    await user.type(screen.getByLabelText('user_id'), ' kim ')
    await user.click(screen.getByRole('button', { name: 'Deep' }))
    await user.type(screen.getByLabelText('팀 필터'), 'YIELD, 품질')
    await user.type(screen.getByLabelText('주차 필터'), '2026-31, 2026-32')
    await user.selectOptions(screen.getByLabelText('메일 유형'), 'weekly')
    await user.type(screen.getByLabelText('질문'), '4주 보고서')
    await user.click(screen.getByRole('button', { name: '실행' }))

    expect(onSubmit).toHaveBeenCalledWith({
      userId: 'kim',
      mode: 'deep',
      question: '4주 보고서',
      filters: {
        teams: ['YIELD', '품질'],
        weeks: ['2026-31', '2026-32'],
        mail_type: 'weekly',
      },
    })
    expect(screen.queryByRole('button', { name: 'Auto' })).not.toBeInTheDocument()
  })

  it('requires owner and question and rejects invalid weeks', async () => {
    const user = userEvent.setup()
    const onSubmit = vi.fn()
    render(<RequestPanel disabled={false} onSubmit={onSubmit} />)

    await user.type(screen.getByLabelText('주차 필터'), '2026-W31')
    await user.click(screen.getByRole('button', { name: '실행' }))

    expect(screen.getByRole('alert')).toHaveTextContent('user_id를 입력해 주세요.')
    expect(onSubmit).not.toHaveBeenCalled()

    await user.type(screen.getByLabelText('user_id'), 'kim')
    await user.type(screen.getByLabelText('질문'), '질문')
    await user.click(screen.getByRole('button', { name: '실행' }))
    expect(screen.getByRole('alert')).toHaveTextContent('YYYY-WW')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('explains that team is not authorization and never writes the draft to storage', async () => {
    const user = userEvent.setup()
    const setItem = vi.spyOn(window.localStorage, 'setItem')
    render(<RequestPanel disabled={false} onSubmit={vi.fn()} />)

    expect(screen.getByText(/팀은 검색 범위만 좁히며/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('user_id'), 'sensitive-owner')
    await user.type(screen.getByLabelText('질문'), 'sensitive-question')

    expect(setItem).not.toHaveBeenCalled()
  })
})
