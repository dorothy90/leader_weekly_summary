import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { WikiMetaPane } from './WikiMetaPane'
import type { Agenda, AgendaDetailResponse, Taxonomy } from '../types'

const selected: Agenda = {
  id: 'agenda-1',
  mail_id: 'mail-1',
  source_quote: '4SA 수율이 하락했습니다.',
  summary: '4SA 수율 하락',
  scope: 'lotcd',
  target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  candidate_paths: [],
  topic: 'yield',
  state: 'open',
  confidence: 0.9,
  review_required: false,
  review_status: 'confirmed',
  subject: 'Spica 주간 수율',
  sender_team: 'Spica수율',
  received_at: '2026-07-06T10:20:00+09:00',
}

const detail: AgendaDetailResponse = {
  agenda: selected,
  mail: {
    id: 'mail-1',
    subject: selected.subject,
    sender_team: selected.sender_team,
    sender: 'spica@example.invalid',
    received_at: selected.received_at,
    body: '4SA 수율이 하락했습니다. 원인 분석 중입니다.',
    reply_to: null,
  },
}

const taxonomy: Taxonomy = {
  version: 1,
  is_dummy: true,
  notice: 'test',
  group_aliases: [],
  domains: [{
    id: 'dram',
    name: 'DRAM',
    techs: [{
      id: 'spica',
      name: 'Spica',
      aliases: [],
      lotcds: [
        { code: '4SA', fab_id: '4', product_code: 'SA', product: 'LPDDR5 24G', aliases: [] },
        { code: '6SA', fab_id: '6', product_code: 'SA', product: 'LPDDR5 24G', aliases: [] },
      ],
    }],
  }],
}

describe('WikiMetaPane', () => {
  it('shows evidence, backlinks, timeline, and Fab pair', () => {
    const onSelectAgenda = vi.fn()
    const pairAgenda = {
      ...selected,
      id: 'agenda-2',
      mail_id: 'mail-2',
      summary: '6SA 수율 정상화',
      target_paths: [{ domain: 'DRAM' as const, tech: 'Spica', lotcd: '6SA' }],
      received_at: '2026-07-07T10:20:00+09:00',
    }
    render(
      <WikiMetaPane
        detail={detail}
        agendas={[selected, pairAgenda, { ...selected, id: 'agenda-3', summary: '4SA 원인 분석' }]}
        taxonomy={taxonomy}
        revisions={[]}
        onSelectAgenda={onSelectAgenda}
      />,
    )

    expect(screen.getByText('6SA')).toBeInTheDocument()
    expect(screen.getByText('Timeline · 4SA')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Source mail/ }))
    expect(screen.getByRole('dialog', { name: '원본 메일' })).toBeInTheDocument()
    expect(screen.getByText('4SA 수율이 하락했습니다.')).toBeInTheDocument()
  })
})
