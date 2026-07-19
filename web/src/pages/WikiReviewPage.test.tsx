import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchSession,
  fetchTopic,
  fetchWikiReviews,
  resolveWikiReview,
  startWikiBuild,
} from '../api/knowledge'
import type { WikiBuildRun, WikiReview, WikiTopicDetail } from '../types'
import { WIKI_ASSIGNMENT_REVIEWS_CHANGED } from '../reviewEvents'
import { WikiReviewPage } from './WikiReviewPage'

vi.mock('../api/knowledge', () => ({
  fetchSession: vi.fn(),
  fetchTopic: vi.fn(),
  fetchWikiBuild: vi.fn(),
  fetchWikiReviews: vi.fn(),
  resolveWikiReview: vi.fn(),
  startWikiBuild: vi.fn(),
}))

const assignmentReview: WikiReview = {
  review_id: 'RV-001',
  kind: 'assignment',
  agenda_id: 'A-001',
  candidates: [{ topic_id: 'T-001', score: 0.74, rank_reasons: ['수율 > 검사 > DRAM'] }],
  relation_id: null,
  relation_kind: null,
  relation_agenda_ids: [],
  rationale: '두 Topic과 유사도가 비슷합니다.',
  status: 'pending',
}

const relationReview: WikiReview = {
  review_id: 'RV-002',
  kind: 'relation',
  agenda_id: null,
  candidates: [],
  relation_id: 'REL-001',
  relation_kind: 'possible_cause',
  relation_agenda_ids: ['A-001', 'A-002'],
  rationale: 'A-001에서 가능한 원인 관계가 관찰되었습니다.',
  status: 'pending',
}

const buildRun: WikiBuildRun = {
  run_id: 'WB-001', week: '2026-W30', classification_run_id: 'CR-001', taxonomy_version: 3,
  status: 'review_required', input_hash: 'hash', model: 'test-model', affected_topic_ids: [],
  failed_topic_ids: [], started_at: '2026-07-20T00:00:00Z', completed_at: null, error: null,
}

const candidateDetail = {
  topic: {
    topic_id: 'T-001', title: '4SA D1 불량 추적',
    target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  },
} as WikiTopicDetail

describe('WikiReviewPage', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(fetchSession).mockResolvedValue({
      user_id: 'owner', roles: ['knowledge-editor'], can_edit: true,
    })
    vi.mocked(fetchWikiReviews).mockResolvedValue([])
    vi.mocked(fetchTopic).mockResolvedValue(candidateDetail)
    vi.mocked(resolveWikiReview).mockImplementation(async (reviewId, input) => ({
      ...(reviewId === relationReview.review_id ? relationReview : assignmentReview),
      status: input.action === 'hold' ? 'held' : 'resolved',
    }))
  })

  it('allows editors to attach an ambiguous Agenda', async () => {
    vi.mocked(fetchWikiReviews).mockResolvedValue([assignmentReview])
    render(<WikiReviewPage />)

    fireEvent.click(await screen.findByRole('button', { name: 'T-001에 연결' }))

    expect(screen.getByText('4SA D1 불량 추적')).toBeInTheDocument()
    expect(screen.getByText(/DRAM › Spica › 4SA/)).toBeInTheDocument()
    await waitFor(() => expect(resolveWikiReview).toHaveBeenCalledWith(
      'RV-001', { action: 'attach', topic_id: 'T-001' },
    ))
    expect(await screen.findByRole('status')).toHaveTextContent('검토 결정을 저장했습니다')
    expect(screen.getByRole('heading', { name: '분류 검토' })).toHaveFocus()
  })

  it('marks relation reviews as nonblocking and disables decisions for readers', async () => {
    vi.mocked(fetchWikiReviews).mockResolvedValue([relationReview])
    vi.mocked(fetchSession).mockResolvedValue({ user_id: 'reader', roles: [], can_edit: false })
    render(<WikiReviewPage />)

    expect(await screen.findByText('발행 비차단')).toBeInTheDocument()
    expect(screen.getByText('possible_cause')).toBeInTheDocument()
    expect(screen.getByText('A-001, A-002')).toBeInTheDocument()
    expect(screen.getByText(/가능한 원인 관계/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '관계 승인' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '관계 거절' })).toBeDisabled()
  })

  it.each([
    ['관계 승인', 'accept'],
    ['관계 거절', 'reject'],
  ] as const)('submits %s for a typed nonblocking relation review', async (button, action) => {
    vi.mocked(fetchWikiReviews).mockResolvedValue([relationReview])
    render(<WikiReviewPage />)

    fireEvent.click(await screen.findByRole('button', { name: button }))

    await waitFor(() => expect(resolveWikiReview).toHaveBeenCalledWith(
      'RV-002', { action },
    ))
  })

  it.each(['attach', 'create', 'hold'] as const)(
    'announces a successful %s decision to the shell badge',
    async (action) => {
      const listener = vi.fn()
      window.addEventListener(WIKI_ASSIGNMENT_REVIEWS_CHANGED, listener)
      vi.mocked(fetchWikiReviews).mockResolvedValue([assignmentReview])
      render(<WikiReviewPage />)

      if (action === 'attach') {
        fireEvent.click(await screen.findByRole('button', { name: 'T-001에 연결' }))
      } else if (action === 'create') {
        fireEvent.change(await screen.findByLabelText('새 Topic 제목'), {
          target: { value: '새 Topic' },
        })
        fireEvent.click(screen.getByRole('button', { name: '새 Topic 생성' }))
      } else {
        fireEvent.click(await screen.findByRole('button', { name: '보류' }))
      }

      await waitFor(() => expect(listener).toHaveBeenCalledOnce())
      window.removeEventListener(WIKI_ASSIGNMENT_REVIEWS_CHANGED, listener)
    },
  )

  it('starts an explicit build for the entered approved week', async () => {
    vi.mocked(startWikiBuild).mockResolvedValue(buildRun)
    render(<WikiReviewPage />)

    const week = await screen.findByLabelText('승인된 주차')
    fireEvent.change(week, { target: { value: '2026-W30' } })
    fireEvent.click(screen.getByRole('button', { name: 'Wiki 빌드 시작' }))

    await waitFor(() => expect(startWikiBuild).toHaveBeenCalledWith('2026-W30'))
    expect(await screen.findByText('배정 검토 필요')).toBeInTheDocument()
  })
})
