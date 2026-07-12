import { fireEvent, render, screen, within } from '@testing-library/react'
import { vi } from 'vitest'

import type { Agenda, Mail } from '../types'
import { WikiCitationDrawer } from './WikiCitationDrawer'

const mail: Mail = {
  id: 'mail-1',
  subject: '4SA 수율 분석 결과',
  sender_team: '공정기술팀',
  sender: '홍길동',
  received_at: '2026-07-10T03:00:00Z',
  body: '4SA 수율이 하락했습니다. 원인 분석 중입니다. 후속 검증을 진행합니다.',
  reply_to: null,
}

const agenda: Agenda = {
  id: 'agenda-1',
  mail_id: mail.id,
  source_quote: '4SA 수율이 하락했습니다.',
  summary: '4SA 수율 저하 원인 분석',
  scope: 'lotcd',
  target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  candidate_paths: [],
  topic: '수율',
  state: '진행 중',
  confidence: 0.9,
  review_required: false,
  review_status: 'confirmed',
  subject: mail.subject,
  sender_team: mail.sender_team,
  received_at: mail.received_at,
}

describe('WikiCitationDrawer', () => {
  it('shows source mail and every supporting agenda quote', () => {
    render(
      <WikiCitationDrawer
        detail={{
          mail,
          agendas: [
            agenda,
            {
              ...agenda,
              id: 'agenda-2',
              summary: '후속 검증 계획',
              state: '대기',
              source_quote: '후속 검증을 진행합니다.',
              target_paths: [],
            },
          ],
          used_in_sections: ['원인과 영향 관계', '펜딩 이슈와 의사결정'],
        }}
        loading={false}
        error={null}
        onClose={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '메일 근거' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    expect(within(dialog).getByText(mail.subject)).toBeInTheDocument()
    expect(within(dialog).getByText(mail.sender_team)).toBeInTheDocument()
    expect(within(dialog).getByText(mail.sender)).toBeInTheDocument()
    expect(within(dialog).getByText(mail.received_at)).toBeInTheDocument()
    expect(within(dialog).getByText('원인과 영향 관계')).toBeInTheDocument()
    expect(within(dialog).getByText('펜딩 이슈와 의사결정')).toBeInTheDocument()
    expect(within(dialog).getByText(agenda.summary)).toBeInTheDocument()
    expect(within(dialog).getByText(agenda.state)).toBeInTheDocument()
    expect(within(dialog).getByText('DRAM / Spica / 4SA')).toBeInTheDocument()
    expect(within(dialog).getByText(agenda.source_quote).tagName).toBe('MARK')
    expect(within(dialog).getByText('후속 검증 계획')).toBeInTheDocument()
    expect(within(dialog).getByText('대기')).toBeInTheDocument()
    expect(within(dialog).getByText('미분류')).toBeInTheDocument()
    expect(within(dialog).getByText('후속 검증을 진행합니다.').tagName).toBe('MARK')
    expect(within(dialog).queryByRole('button', { name: /보내기|수정|분류/ })).not.toBeInTheDocument()
  })

  it('closes from the backdrop or close button but not from the panel', () => {
    const onClose = vi.fn()
    render(
      <WikiCitationDrawer
        detail={{ mail, agendas: [agenda], used_in_sections: [] }}
        loading={false}
        error={null}
        onClose={onClose}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '메일 근거' })
    fireEvent.mouseDown(dialog)
    expect(onClose).not.toHaveBeenCalled()

    fireEvent.mouseDown(dialog.parentElement!)
    expect(onClose).toHaveBeenCalledTimes(1)

    fireEvent.click(within(dialog).getByRole('button', { name: '메일 근거 닫기' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it.each([
    { loading: true, error: null, message: '메일 근거 불러오는 중' },
    { loading: false, error: '메일 근거를 불러오지 못했습니다.', message: '메일 근거를 불러오지 못했습니다.' },
  ])('keeps the $message state inside the dialog', ({ loading, error, message }) => {
    render(
      <WikiCitationDrawer
        detail={null}
        loading={loading}
        error={error}
        onClose={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: '메일 근거' })
    expect(within(dialog).getByText(message)).toBeInTheDocument()
  })
})
