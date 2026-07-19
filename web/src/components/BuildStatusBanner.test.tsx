import { act, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

import { fetchWikiBuild } from '../api/knowledge'
import type { WikiBuildRun } from '../types'
import { BuildStatusBanner } from './BuildStatusBanner'

vi.mock('../api/knowledge', () => ({ fetchWikiBuild: vi.fn() }))

const run: WikiBuildRun = {
  run_id: 'WB-001', week: '2026-W30', classification_run_id: 'CR-001', taxonomy_version: 3,
  status: 'published', input_hash: 'hash', model: 'test-model', affected_topic_ids: ['T-001'],
  failed_topic_ids: [], started_at: '2026-07-20T00:00:00Z', completed_at: '2026-07-20T00:01:00Z',
  error: null,
}

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

it('keeps stale topic details visible on a partial failure', () => {
  render(<BuildStatusBanner run={{ ...run, status: 'partially_failed', failed_topic_ids: ['T-009'] }} />)

  expect(screen.getByRole('status')).toHaveTextContent('일부 Topic은 이전 정상 버전을 표시합니다')
  expect(screen.getByRole('status')).toHaveTextContent('T-009')
})

it.each([
  ['linking', 'Topic 연결 중'],
  ['review_required', '배정 검토 필요'],
  ['generating', 'Topic 생성 중'],
  ['validating', '검증 중'],
  ['published', '게시 완료'],
  ['failed', '빌드 실패'],
] as const)('shows the %s build state', (status, label) => {
  render(<BuildStatusBanner run={{ ...run, status }} />)
  expect(screen.getByRole('status')).toHaveTextContent(label)
})

it('polls a transient build and stops after its terminal result', async () => {
  vi.useFakeTimers()
  vi.mocked(fetchWikiBuild).mockResolvedValue({ ...run, status: 'published' })
  render(<BuildStatusBanner run={{ ...run, status: 'generating', completed_at: null }} />)

  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(fetchWikiBuild).toHaveBeenCalledOnce()
  expect(screen.getByRole('status')).toHaveTextContent('게시 완료')

  await act(async () => { await vi.advanceTimersByTimeAsync(4000) })
  expect(fetchWikiBuild).toHaveBeenCalledOnce()
})

it('retries a transient build after a non-abort polling error', async () => {
  vi.useFakeTimers()
  vi.mocked(fetchWikiBuild)
    .mockRejectedValueOnce(new Error('temporary network error'))
    .mockResolvedValueOnce({ ...run, status: 'published' })
  render(<BuildStatusBanner run={{ ...run, status: 'generating', completed_at: null }} />)

  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(fetchWikiBuild).toHaveBeenCalledOnce()

  await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
  expect(fetchWikiBuild).toHaveBeenCalledTimes(2)
  expect(screen.getByRole('status')).toHaveTextContent('게시 완료')
})
