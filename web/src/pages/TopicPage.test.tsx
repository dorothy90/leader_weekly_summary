import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { vi } from 'vitest'

import { fetchTopic } from '../api/knowledge'
import type { WikiTopicDetail } from '../types'
import { TopicPage } from './TopicPage'

vi.mock('../api/knowledge', () => ({ fetchTopic: vi.fn() }))

const showModal = vi.fn(function (this: HTMLDialogElement) {
  this.setAttribute('open', '')
})
const closeDialog = vi.fn(function (this: HTMLDialogElement) {
  this.removeAttribute('open')
})

beforeAll(() => {
  Object.defineProperties(HTMLDialogElement.prototype, {
    showModal: { configurable: true, value: showModal },
    close: { configurable: true, value: closeDialog },
  })
})

const topicDetail: WikiTopicDetail = {
  topic: {
    topic_id: 'T-001', title: '4SA D1 불량 추적', topic_kind: 'issue',
    primary_area: 'yield_defect', secondary_areas: ['quality_analysis'],
    state: 'investigating', importance: 'high', first_seen_week: '2026-W03',
    last_updated_week: '2026-W04', target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
    teams: ['수율개선팀'], source_agenda_ids: ['A-001'], related_topic_ids: ['T-002'],
    current_revision_id: 'R-003',
  },
  body_markdown: '# export only',
  sections: [{ key: 'status', title: '현재 상태', body: 'D1 불량 원인을\n분석 중이다.' }],
  claims: [{ text: 'D1 불량이 증가했다.', agenda_ids: ['A-001'] }],
  evidence: [{
    agenda_id: 'A-001', mail_id: 'M-001', team: '수율개선팀', week: '2026-W04',
    subject: '4SA 주간 품질', source_quote: '4SA D1 불량이 전주 대비 증가했습니다.',
    source_path: '/mail/archive/2026-04.eml',
  }],
  relations: [{
    relation_id: 'R-001', source_topic_id: 'T-001', target_topic_id: 'T-002',
    kind: 'possible_cause', agenda_ids: ['A-001'], confidence: 0.84, review_state: 'accepted',
  }, {
    relation_id: 'R-002', source_topic_id: 'T-001', target_topic_id: 'T-003',
    kind: 'affects', agenda_ids: [], confidence: 0.5, review_state: 'pending',
  }],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchTopic).mockResolvedValue(topicDetail)
})

it('opens cited Agenda evidence without duplicating the Topic', async () => {
  render(<MemoryRouter initialEntries={['/wiki/topics/T-001?from=%2Fwiki%2Flotcd%2FDRAM%2FSpica%2F4SA']}>
    <Routes><Route path="/wiki/topics/:topicId" element={<TopicPage />} /></Routes>
  </MemoryRouter>)
  const citation = await screen.findByRole('button', { name: '근거 A-001 보기' })
  fireEvent.click(citation)
  expect(screen.getByRole('dialog', { name: 'Agenda 근거' })).toHaveTextContent('4SA D1 불량')
  expect(showModal).toHaveBeenCalledOnce()
  await waitFor(() => expect(screen.getByRole('button', { name: '근거 닫기' })).toHaveFocus())
  expect(screen.getByText('/mail/archive/2026-04.eml')).not.toHaveAttribute('href')
  expect(screen.getByRole('link', { name: '이전 화면' })).toHaveAttribute('href', '/wiki/lotcd/DRAM/Spica/4SA')

  fireEvent(screen.getByRole('dialog', { name: 'Agenda 근거' }), new Event('cancel', { cancelable: true }))
  expect(screen.queryByRole('dialog', { name: 'Agenda 근거' })).not.toBeInTheDocument()
  expect(citation).toHaveFocus()

  fireEvent.click(citation)
  fireEvent.click(await screen.findByRole('button', { name: '근거 닫기' }))
  expect(screen.queryByRole('dialog', { name: 'Agenda 근거' })).not.toBeInTheDocument()
  expect(citation).toHaveFocus()
})

it('renders current revision sections and only accepted related Topics', async () => {
  render(<MemoryRouter initialEntries={['/wiki/topics/T-001?from=%2Fwiki%2Fteams%2Fyield']}>
    <Routes><Route path="/wiki/topics/:topicId" element={<TopicPage />} /></Routes>
  </MemoryRouter>)

  expect(await screen.findByRole('heading', { name: '현재 상태' })).toBeInTheDocument()
  expect(screen.getByText(/D1 불량 원인을/)).toHaveClass('topic-document__section-body')
  expect(screen.getByRole('link', { name: 'T-002' })).toHaveAttribute(
    'href', '/wiki/topics/T-002?from=%2Fwiki%2Fteams%2Fyield',
  )
  expect(screen.queryByRole('link', { name: 'T-003' })).not.toBeInTheDocument()
  expect(screen.queryByText('# export only')).not.toBeInTheDocument()
  expect(screen.queryByRole('main')).not.toBeInTheDocument()
})

it('opens evidence from an inline section citation token', async () => {
  vi.mocked(fetchTopic).mockResolvedValue({
    ...topicDetail,
    sections: [{ key: 'status', title: '현재 상태', body: '증가했다. [agenda:A-001]' }],
    claims: [],
  })
  render(<MemoryRouter initialEntries={['/wiki/topics/T-001']}>
    <Routes><Route path="/wiki/topics/:topicId" element={<TopicPage />} /></Routes>
  </MemoryRouter>)
  fireEvent.click(await screen.findByRole('button', { name: '근거 A-001 보기' }))
  expect(screen.getByRole('dialog', { name: 'Agenda 근거' })).toHaveTextContent('4SA D1 불량')
})
