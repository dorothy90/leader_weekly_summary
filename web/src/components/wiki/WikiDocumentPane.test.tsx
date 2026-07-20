import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { fetchTopic } from '../../api/knowledge'
import type { WikiTopicDetail } from '../../types'
import type { WikiCollectionState } from './useWikiCollection'
import { WikiDocumentPane } from './WikiDocumentPane'

vi.mock('../../api/knowledge', () => ({ fetchTopic: vi.fn() }))

const collection: WikiCollectionState = {
  kind: 'topics', path: '/wiki/topics', title: '전체 주제', summary: '1개 canonical Topic',
  status: 'ready', evidence: [], topics: [], directEvidence: [], rolledUpEvidence: [], breadcrumb: [], scopeLevel: null,
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
    subject: '4SA 수율', source_quote: 'chamber A 편차가 확인됐다.',
    mail_html_available: true, original_mail_url: '/api/knowledge/evidence/DEMO-AGENDA-01/mail#agenda-source',
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

it('shows TOC, connected Topics, inline citations, and source-mail references', async () => {
  vi.mocked(fetchTopic).mockResolvedValue(detail)
  render(<WikiDocumentPane topicId="DEMO-TOPIC-01" collection={collection} onSelectTopic={vi.fn()} />)

  expect(await screen.findByRole('heading', { name: '4SA chamber A 편차' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '문서 목차' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '연결된 주제' })).toBeInTheDocument()
  expect(screen.getAllByRole('link', { name: '참고문서 DEMO-AGENDA-01로 이동' })[0]).toHaveAttribute('href', '#reference-DEMO-AGENDA-01')
  const original = screen.getByRole('link', { name: /원본 메일 보기/ })
  expect(original).toHaveAttribute('target', '_blank')
  expect(original).toHaveAttribute('rel', 'noopener noreferrer')
  fireEvent.click(screen.getByRole('button', { name: '참고문서 DEMO-AGENDA-01 상세' }))
  expect(screen.getByRole('dialog', { name: 'Agenda 근거' })).toHaveTextContent('chamber A 편차')
})

it('synthesizes a collection as one document with linked Topic sections', () => {
  const onSelectTopic = vi.fn()
  const teamCollection: WikiCollectionState = {
    kind: 'team', path: '/wiki/teams/Spica수율', title: 'Spica수율',
    summary: '1개 Topic에 기여', status: 'ready',
    directEvidence: [detail.evidence[0]], rolledUpEvidence: [], breadcrumb: ['Spica수율'], scopeLevel: null,
    topics: [{
      topic_id: 'DEMO-TOPIC-01', title: '4SA chamber A 편차', state: 'investigating',
      importance: 'critical', primary_area: 'yield_defect',
      target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }], teams: ['Spica수율'],
      last_updated_week: '2026-W30', evidence_count: 1, rank_reasons: ['최근 갱신'],
    }],
    evidence: [detail.evidence[0]],
  }

  render(<WikiDocumentPane topicId={null} collection={teamCollection} onSelectTopic={onSelectTopic} />)

  expect(screen.getByRole('heading', { name: 'Spica수율' })).toBeInTheDocument()
  expect(screen.getByRole('navigation', { name: '문서 목차' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '주요 주제' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '지식 영역' })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: '참고문서' })).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Topic 문서 열기: 4SA chamber A 편차' }))
  expect(onSelectTopic).toHaveBeenCalledWith('DEMO-TOPIC-01')
})

it('distinguishes direct and descendant references in a category rollup', () => {
  const direct = detail.evidence[0]
  const rolled = {
    ...direct, agenda_id: 'DEMO-AGENDA-02', mail_id: 'dummy_mail_002',
    subject: '4SA 하위 LOTCD 보고', original_mail_url: '/api/knowledge/evidence/DEMO-AGENDA-02/mail#agenda-source',
  }
  const categoryCollection: WikiCollectionState = {
    kind: 'lotcd', path: '/wiki/lotcd/DRAM/Spica', title: 'Spica Wiki',
    summary: 'Spica: 1 Topics', status: 'ready', topics: [],
    evidence: [direct, rolled], directEvidence: [direct], rolledUpEvidence: [rolled],
    breadcrumb: ['DRAM', 'Spica'], scopeLevel: 'tech',
  }

  render(<WikiDocumentPane topicId={null} collection={categoryCollection} onSelectTopic={vi.fn()} />)

  expect(screen.getByText('DRAM / Spica')).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /직접 분류된 참고문서 1/ })).toBeInTheDocument()
  expect(screen.getByRole('heading', { name: /하위 계층에서 롤업된 참고문서 1/ })).toBeInTheDocument()
  expect(screen.getAllByRole('link', { name: /원본 메일 보기/ })).toHaveLength(2)
})
