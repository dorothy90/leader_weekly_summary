import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { RagApiClient } from '../../services/ragApiService'
import type {
  ApiExchange,
  ChatResponse,
  HealthResponse,
  ReadinessResponse,
  ResearchJobResponse,
} from '../types'
import { RagLabApp } from '../RagLabApp'

const FALLBACK =
  '임베딩 서비스를 사용할 수 없어 키워드(BM25) 검색만 사용했습니다. 의미 기반 검색 결과가 일부 누락될 수 있습니다.'

const exchange = <T,>(response: T, status = 200): ApiExchange<T> => ({
  request: { method: 'POST', path: '/v1/chat', body: { user_id: 'kim' } },
  response,
  status,
  durationMs: 184,
  receivedAt: '2026-08-02T00:00:00Z',
})

const health = exchange<HealthResponse>({ status: 'ok' })
const readiness = exchange<ReadinessResponse>({
  status: 'ready',
  dependencies: { mongo: 'ready', opensearch: 'ready' },
})

const baseService = (): RagApiClient => ({
  sendChat: vi.fn(),
  researchAction: vi.fn(),
  streamResearchEvents: vi.fn(),
  getHealth: vi.fn().mockResolvedValue(health),
  getReadiness: vi.fn().mockResolvedValue(readiness),
})

async function submitQuestion(user: ReturnType<typeof userEvent.setup>, mode: 'Fast' | 'Deep') {
  await user.type(screen.getByLabelText('user_id'), 'kim')
  await user.click(screen.getByRole('button', { name: mode }))
  await user.type(screen.getByLabelText('질문'), '최근 현황을 알려줘')
  await user.click(screen.getByRole('button', { name: '실행' }))
}

describe('RagLabApp', () => {
  it('shows Fast answer, evidence, exact fallback, quality, and exchange diagnostics', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>({
        conversation_id: 'conversation-1',
        mode: 'fast_rag',
        answer: '확인된 답변입니다 [S1]',
        references: [
          {
            evidence_id: 'S1',
            source_type: 'mail',
            document_id: 'opaque-1',
            title: '주간 수율 메일',
            excerpt: '수율 저하 원인',
            team: 'YIELD',
            week: '2026-31',
          },
        ],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'bm25',
        },
        disclosures: [FALLBACK],
        trace_id: 'trace-1',
      }),
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Fast')

    expect(await screen.findByText(/확인된 답변입니다/)).toBeInTheDocument()
    expect(screen.getByText(FALLBACK)).toBeInTheDocument()
    expect(screen.getByText('S1')).toBeInTheDocument()
    expect(screen.getByText('주간 수율 메일')).toBeInTheDocument()
    expect(screen.getByText('인용 유효')).toBeInTheDocument()
    expect(screen.getAllByText('bm25')).toHaveLength(2)
    expect(screen.getByText('trace-1')).toBeInTheDocument()
    expect(screen.getByText('200')).toBeInTheDocument()
  })

  it('tracks a Deep job from accepted events to the completed report', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>(
        {
          conversation_id: 'conversation-2',
          mode: 'deep_research',
          answer: null,
          references: [],
          quality: null,
          disclosures: [],
          trace_id: 'trace-deep',
          job_id: 'job-1',
          status: 'queued',
          plan_summary: '4주 조사 계획',
        },
        202,
      ),
    )
    const completed: ResearchJobResponse = {
      job_id: 'job-1',
      status: 'completed',
      progress: 100,
      plan_summary: '4주 조사 계획',
      result_markdown: '완료된 보고서 [S1]',
      references: [
        {
          evidence_id: 'S1',
          source_type: 'mail',
          document_id: 'opaque-1',
          title: '조사 근거',
          excerpt: '확인된 내용',
        },
      ],
      disclosures: [],
      error_code: null,
    }
    vi.mocked(service.researchAction).mockResolvedValue(exchange(completed))
    vi.mocked(service.streamResearchEvents).mockImplementation(
      async (_jobId, _userId, onEvent) => {
        onEvent({ job_id: 'job-1', status: 'running', progress: 40 })
        onEvent({ job_id: 'job-1', status: 'completed', progress: 100 })
      },
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Deep')

    expect(await screen.findByText(/완료된 보고서/)).toBeInTheDocument()
    expect(screen.getByText('100%')).toBeInTheDocument()
    expect(screen.getByText('completed')).toBeInTheDocument()
    await waitFor(() =>
      expect(service.researchAction).toHaveBeenCalledWith('job-1', 'status', 'kim'),
    )
    expect(service.streamResearchEvents).toHaveBeenCalledWith(
      'job-1',
      'kim',
      expect.any(Function),
      expect.any(AbortSignal),
    )
  })
})
