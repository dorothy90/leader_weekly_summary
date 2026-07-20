import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { fetchTopic } from '../../api/knowledge'
import type { WikiTopicDetail } from '../../types'
import type { WikiCollectionState } from './useWikiCollection'
import { WikiDocumentPane } from './WikiDocumentPane'

vi.mock('../../api/knowledge', () => ({ fetchTopic: vi.fn() }))

const collection: WikiCollectionState = {
  kind: 'topics', path: '/wiki/topics', title: '전체 주제', summary: '1개 canonical Topic',
  status: 'ready', evidence: [], topics: [],
}

const detail: WikiTopicDetail = {
  topic: {
    topic_id: 'DEMO-TOPIC-01', title: '4SA chamber A 편차', topic_kind: 'issue',
    primary_area: 'yield_defect', secondary_areas: [], state: 'investigating', importance: 'critical',
    first_seen_week: '2026-W28', last_updated_week: '2026-W30',
    target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }], teams: ['Spica수율'],
    source_agenda_ids: ['DEMO-AGENDA-01'], related_topic_ids: ['DEMO-TOPIC-02'], current_revision_id: 'DEMO-REV-01',
  },
  body_markdown: '',
  sections: [{ key: 'current_state', title: '현재 상태', body: '편차를 분석 중이다. [agenda:DEMO-AGENDA-01]' }],
  claims: [{ text: '수율이 하락했다.', agenda_ids: ['DEMO-AGENDA-01'] }],
  evidence: [{
    agenda_id: 'DEMO-AGENDA-01', mail_id: 'dummy_mail_001', team: 'Spica수율', week: '2026-W30',
    subject: '4SA 수율', source_quote: 'chamber A 편차가 확인됐다.', source_path: 'fixture#1',
  }],
  relations: [{
    relation_id: 'DEMO-REL-01', source_topic_id: 'DEMO-TOPIC-01', target_topic_id: 'DEMO-TOPIC-02',
    kind: 'possible_cause', agenda_ids: ['DEMO-AGENDA-01'], confidence: 0.9, review_state: 'accepted',
  }],
}

beforeAll(() => {
  HTMLDialogElement.prototype.showModal = vi.fn(function (this: HTMLDialogElement) { this.setAttribute('open', '') })
  HTMLDialogElement.prototype.close = vi.fn(function (this: HTMLDialogElement) { this.removeAttribute('open') })
})

it('shows TOC, connected Topics, and immutable Agenda evidence', async () => {
  vi.mocked(fetchTopic).mockResolvedValue(detail)
  render(<WikiDocumentPane topicId="DEMO-TOPIC-01" collection={collection} onSelectTopic={vi.fn()} />)

  expect(await screen.findByRole('heading', { name: '4SA chamber A 편차' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '문서 목차' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '연결된 주제' })).toBeInTheDocument()
  fireEvent.click(screen.getAllByRole('button', { name: /Agenda 근거 DEMO-AGENDA-01 보기/ })[0])
  expect(screen.getByRole('dialog', { name: 'Agenda 근거' })).toHaveTextContent('chamber A 편차')
})
