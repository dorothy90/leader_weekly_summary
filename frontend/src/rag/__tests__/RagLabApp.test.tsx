import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

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

afterEach(() => {
  vi.useRealTimers()
})

async function submitQuestion(user: ReturnType<typeof userEvent.setup>, mode: 'Fast' | 'Deep') {
  await user.type(screen.getByLabelText('user_id'), 'kim')
  await user.click(screen.getByRole('button', { name: mode }))
  await user.type(screen.getByLabelText('질문'), '최근 현황을 알려줘')
  await user.click(screen.getByRole('button', { name: '실행' }))
}

describe('RagLabApp', () => {
  it('provides explicit mobile panel switching without hiding diagnostics from desktop', async () => {
    const user = userEvent.setup()
    render(<RagLabApp service={baseService()} />)

    const nav = screen.getByRole('navigation', { name: '모바일 패널' })
    await user.click(screen.getByRole('button', { name: '검사기 패널' }))

    expect(nav.parentElement).toHaveAttribute('data-mobile-view', 'inspector')
    expect(screen.getByLabelText('API 검사기')).toBeInTheDocument()
  })

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
    expect(screen.getByText('trace-deep')).toBeInTheDocument()
    expect(screen.getByText('owner: kim')).toBeInTheDocument()
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

  it('reconnects a failed Deep event stream once before polling status', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>(
        {
          conversation_id: 'conversation-3',
          mode: 'deep_research',
          answer: null,
          references: [],
          quality: null,
          disclosures: [],
          trace_id: 'trace-reconnect',
          job_id: 'job-reconnect',
          status: 'queued',
          plan_summary: '재연결 검사',
        },
        202,
      ),
    )
    vi.mocked(service.streamResearchEvents)
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error('stream unavailable'))
    vi.mocked(service.researchAction).mockResolvedValue(
      exchange<ResearchJobResponse>({
        job_id: 'job-reconnect',
        status: 'running',
        progress: 20,
        plan_summary: '재연결 검사',
        result_markdown: null,
        references: [],
        disclosures: [],
        error_code: null,
      }),
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Deep')

    await waitFor(() =>
      expect(service.streamResearchEvents).toHaveBeenCalledTimes(2),
    )
    await user.click(screen.getByRole('tab', { name: 'Events' }))
    expect(await screen.findByText('SSE 재연결 시도')).toBeInTheDocument()
    expect(await screen.findByText(/상태 조회로 전환됨/)).toBeInTheDocument()
  })

  it('shows safe API failures in the result panel as well as the inspector', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue({
      request: {
        method: 'POST',
        path: '/v1/chat',
        body: { user_id: 'kim', message: '질문', response_mode: 'fast' },
      },
      status: 503,
      durationMs: 12,
      receivedAt: '2026-08-02T00:00:00Z',
      error: {
        code: 'INDEX_UNAVAILABLE',
        message: '검색 서비스를 현재 사용할 수 없습니다.',
        retryable: true,
      },
    })

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Fast')

    const resultPanel = screen.getByLabelText('대화 및 결과')
    const resultAlert = await within(resultPanel).findByRole('alert')
    expect(resultAlert).toHaveTextContent('INDEX_UNAVAILABLE')
    expect(resultAlert).toHaveTextContent('재시도 가능')
    const requestForm = screen.getByRole('heading', { name: 'RAG 검증 콘솔' })
      .closest('form')
    expect(requestForm).not.toBeNull()
    expect(within(requestForm!).getByRole('alert')).toHaveTextContent(
      '검색 서비스를 현재 사용할 수 없습니다.',
    )
  })

  it('polls again when the terminal event status fetch is temporarily unavailable', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>(
        {
          conversation_id: 'conversation-terminal',
          mode: 'deep_research',
          answer: null,
          references: [],
          quality: null,
          disclosures: [],
          trace_id: 'trace-terminal',
          job_id: 'job-terminal',
          status: 'queued',
          plan_summary: '최종 상태 재시도',
        },
        202,
      ),
    )
    vi.mocked(service.streamResearchEvents).mockImplementation(
      async (_jobId, _userId, onEvent) => {
        onEvent({ job_id: 'job-terminal', status: 'completed', progress: 100 })
      },
    )
    vi.mocked(service.researchAction)
      .mockResolvedValueOnce({
        request: { method: 'POST', path: '/status', body: { user_id: 'kim' } },
        status: 503,
        durationMs: 5,
        receivedAt: '2026-08-02T00:00:00Z',
        error: { code: 'INDEX_UNAVAILABLE', message: '일시 장애', retryable: true },
      })
      .mockResolvedValueOnce(
        exchange<ResearchJobResponse>({
          job_id: 'job-terminal',
          status: 'completed',
          progress: 100,
          plan_summary: '최종 상태 재시도',
          result_markdown: '복구된 보고서',
          references: [],
          disclosures: [],
          error_code: null,
        }),
      )

    render(<RagLabApp service={service} pollIntervalMs={1} />)
    await submitQuestion(user, 'Deep')

    await waitFor(() => expect(service.researchAction).toHaveBeenCalledTimes(2))
    expect(await screen.findByText('복구된 보고서')).toBeInTheDocument()
  })

  it('carries the server conversation id into the next turn', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat)
      .mockResolvedValueOnce(
        exchange<ChatResponse>({
          conversation_id: 'conversation-followup',
          mode: 'fast_rag',
          answer: '첫 답변',
          references: [],
          quality: {
            citation_valid: true,
            limited_answer: false,
            retrieval_mode: 'hybrid',
          },
          disclosures: [],
          trace_id: 'trace-first',
        }),
      )
      .mockResolvedValueOnce(
        exchange<ChatResponse>({
          conversation_id: 'conversation-followup',
          mode: 'fast_rag',
          answer: '후속 답변',
          references: [],
          quality: {
            citation_valid: true,
            limited_answer: false,
            retrieval_mode: 'hybrid',
          },
          disclosures: [],
          trace_id: 'trace-second',
        }),
      )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Fast')
    await screen.findByText('첫 답변')
    expect(screen.getByLabelText('conversation_id 선택')).toHaveValue(
      'conversation-followup',
    )

    await user.clear(screen.getByLabelText('질문'))
    await user.type(screen.getByLabelText('질문'), '후속 질문')
    await user.click(screen.getByRole('button', { name: '실행' }))

    await waitFor(() => expect(service.sendChat).toHaveBeenCalledTimes(2))
    expect(service.sendChat).toHaveBeenLastCalledWith(
      expect.objectContaining({ conversation_id: 'conversation-followup' }),
    )
  })
})
