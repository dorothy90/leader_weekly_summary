import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import type { Agenda, Taxonomy } from '../types'
import { CategoryMetaPane } from './CategoryMetaPane'

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
      lotcds: [{ code: '4SA', fab_id: '4', product_code: 'SA', product: 'LPDDR5 24G', aliases: [] }],
    }],
  }],
}

const agenda: Agenda = {
  id: 'agenda-1',
  mail_id: 'mail-1',
  source_quote: '4SA 수율 하락',
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

describe('CategoryMetaPane', () => {
  it('shows category frontmatter and opens a linked note', () => {
    const onSelectAgenda = vi.fn()
    render(
      <CategoryMetaPane
        selection={{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }}
        taxonomy={taxonomy}
        agendas={[agenda]}
        scopeMode="descendants"
        onChangeScope={vi.fn()}
        onSelectAgenda={onSelectAgenda}
      />,
    )

    expect(screen.getByText('LOTCD')).toBeInTheDocument()
    expect(screen.getByText('LPDDR5 24G')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /4SA 수율 하락/ }))
    expect(onSelectAgenda).toHaveBeenCalledWith('agenda-1')
  })
})
