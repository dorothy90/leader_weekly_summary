import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { RequestPanel } from '../RequestPanel'

describe('RequestPanel', () => {
  it('keeps request settings on the left without chat input controls', async () => {
    const onSettingsChange = vi.fn()
    render(
      <RequestPanel
        conversationId=""
        onConversationIdChange={vi.fn()}
        onSettingsChange={onSettingsChange}
      />,
    )

    expect(screen.queryByLabelText('질문')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '실행' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Auto' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
    await waitFor(() =>
      expect(onSettingsChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ mode: 'auto', userId: '' }),
      ),
    )
  })

  it('emits normalized owner, mode, and retrieval filters', async () => {
    const user = userEvent.setup()
    const onSettingsChange = vi.fn()
    render(
      <RequestPanel
        conversationId=""
        onConversationIdChange={vi.fn()}
        onSettingsChange={onSettingsChange}
      />,
    )

    await user.type(screen.getByLabelText('user_id'), ' kim ')
    await user.click(screen.getByRole('button', { name: 'Deep 강제' }))
    await user.type(screen.getByLabelText('팀 필터'), 'YIELD, 품질')
    await user.type(screen.getByLabelText('주차 필터'), '2026-31, 2026-32')
    await user.selectOptions(screen.getByLabelText('메일 유형'), 'weekly_report')

    await waitFor(() =>
      expect(onSettingsChange).toHaveBeenLastCalledWith({
        userId: 'kim',
        mode: 'deep',
        filters: {
          teams: ['YIELD', '품질'],
          weeks: ['2026-31', '2026-32'],
          mail_type: 'weekly_report',
        },
      }),
    )
  })

  it('withholds invalid week settings and explains the format', async () => {
    const user = userEvent.setup()
    const onSettingsChange = vi.fn()
    render(
      <RequestPanel
        conversationId=""
        onConversationIdChange={vi.fn()}
        onSettingsChange={onSettingsChange}
      />,
    )

    await user.type(screen.getByLabelText('주차 필터'), '2026-W31')

    expect(await screen.findByRole('alert')).toHaveTextContent('YYYY-WW')
    expect(screen.getByLabelText('주차 필터')).toHaveAttribute('aria-invalid', 'true')
    expect(onSettingsChange).toHaveBeenLastCalledWith(undefined)
  })

  it('clears a carried conversation when the owner changes', async () => {
    const user = userEvent.setup()
    const onConversationIdChange = vi.fn()
    render(
      <RequestPanel
        conversationId="conversation-kim"
        conversationOwner="kim"
        onConversationIdChange={onConversationIdChange}
        onSettingsChange={vi.fn()}
      />,
    )

    await user.type(screen.getByLabelText('user_id'), 'lee')

    expect(onConversationIdChange).toHaveBeenCalledWith('')
  })

  it('explains team filtering and never stores sensitive settings', async () => {
    const user = userEvent.setup()
    const setItem = vi.spyOn(window.localStorage, 'setItem')
    render(
      <RequestPanel
        conversationId=""
        onConversationIdChange={vi.fn()}
        onSettingsChange={vi.fn()}
      />,
    )

    expect(screen.getByText(/팀은 검색 범위만 좁히며/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('user_id'), 'sensitive-owner')

    expect(setItem).not.toHaveBeenCalled()
  })
})
