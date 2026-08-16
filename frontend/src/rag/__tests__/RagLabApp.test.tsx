import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RagApiClient } from '../../services/ragApiService'
import type {
  ApiExchange,
  ChatResponse,
  HealthResponse,
  ReadinessResponse,
  ResearchJobResponse,
  RoutingDiagnostics,
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

const fastRouting = (
  overrides: Partial<RoutingDiagnostics> = {},
): RoutingDiagnostics => ({
  requested_mode: 'fast',
  route: 'fast',
  executed_system: 'fast_rag',
  reason_code: 'explicit_mode',
  confidence: 1,
  estimated_searches: 1,
  ...overrides,
})

const deepRouting = (): RoutingDiagnostics => ({
  requested_mode: 'deep',
  route: 'deep',
  executed_system: 'deep_research',
  reason_code: 'explicit_mode',
  confidence: 1,
  estimated_searches: 6,
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

async function submitQuestion(
  user: ReturnType<typeof userEvent.setup>,
  mode: 'Auto' | 'Fast 강제' | 'Deep 강제',
) {
  await user.type(screen.getByLabelText('user_id'), 'kim')
  await user.click(screen.getByRole('button', { name: mode }))
  await user.type(screen.getByRole('textbox', { name: '메시지' }), '최근 현황을 알려줘')
  await user.click(screen.getByRole('button', { name: '전송' }))
}

describe('RagLabApp', () => {
  it('provides explicit mobile panel switching without hiding diagnostics from desktop', async () => {
    const user = userEvent.setup()
    render(<RagLabApp service={baseService()} />)

    const nav = screen.getByRole('navigation', { name: '모바일 패널' })
    await user.click(screen.getByRole('button', { name: '검사기 패널' }))

    expect(nav.parentElement).toHaveAttribute('data-mobile-view', 'inspector')
    expect(screen.getByLabelText('API 검사기')).toBeInTheDocument()
    expect(
      within(screen.getByLabelText('대화 및 결과')).getByRole('textbox', { name: '메시지' }),
    ).toBeInTheDocument()
    expect(
      within(screen.getByRole('heading', { name: 'RAG 검증 콘솔' }).closest('form')!)
        .queryByRole('textbox', { name: '메시지' }),
    ).not.toBeInTheDocument()
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
        routing: fastRouting(),
      }),
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Fast 강제')

    expect(await screen.findByText(/확인된 답변입니다/)).toBeInTheDocument()
    expect(screen.getByText(FALLBACK)).toBeInTheDocument()
    expect(screen.getByText('S1')).toBeInTheDocument()
    expect(screen.getByText('주간 수율 메일')).toBeInTheDocument()
    expect(screen.getByText('메일 · YIELD · 2026-31')).toBeInTheDocument()
    expect(screen.getByText('인용 유효')).toBeInTheDocument()
    expect(screen.getAllByText('bm25')).toHaveLength(2)
    expect(screen.getByText('trace-1')).toBeInTheDocument()
    expect(screen.getByText('200')).toBeInTheDocument()
  })

  it('shows accessible labels for every supported reference source', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>({
        conversation_id: 'conversation-multi-source',
        mode: 'fast_rag',
        answer: '모든 출처의 근거입니다 [S1] [S2] [S3] [S4] [S5]',
        references: [
          {
            evidence_id: 'S1',
            source_type: 'calendar',
            document_id: 'event-kim-1',
            title: 'NAND Yield Review',
            excerpt: 'FDC 로그를 확인한다.',
          },
          {
            evidence_id: 'S2',
            source_type: 'domain_knowledge',
            document_id: 'domain-cell-leakage',
            title: 'Cell Leakage',
            excerpt: '저장 전하 누설 현상이다.',
          },
          {
            evidence_id: 'S3',
            source_type: 'mail',
            document_id: 'mail-yield-1',
            title: '주간 수율 메일',
            excerpt: '수율 저하 원인을 정리했다.',
            team: 'YIELD',
            week: '2026-31',
          },
          {
            evidence_id: 'S4',
            source_type: 'wiki',
            document_id: 'wiki-nand-1',
            title: 'NAND 공정 Wiki',
            excerpt: '공정 기준을 설명한다.',
          },
          {
            evidence_id: 'S5',
            source_type: 'statistic',
            document_id: 'stat-yield-1',
            title: '수율 통계',
            excerpt: '최근 수율 추이를 집계했다.',
          },
        ],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'hybrid',
        },
        disclosures: [],
        trace_id: 'trace-multi-source',
        routing: fastRouting(),
      }),
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Fast 강제')

    const referenceList = await screen.findByRole('region', { name: '검증된 인용 근거' })
    expect(within(referenceList).getByText('일정/회의')).toBeInTheDocument()
    expect(within(referenceList).getByText('도메인 지식')).toBeInTheDocument()
    expect(within(referenceList).getByText('메일 · YIELD · 2026-31')).toBeInTheDocument()
    expect(within(referenceList).getByText('Wiki')).toBeInTheDocument()
    expect(within(referenceList).getByText('통계')).toBeInTheDocument()
    expect(referenceList.textContent).not.toMatch(/calendar|domain_knowledge/)
  })

  it('shows authoritative general routing without retrieval quality badges', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>({
        conversation_id: 'conversation-general',
        mode: 'fast_rag',
        answer: 'Hello!',
        references: [],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'not_used',
        },
        disclosures: [],
        trace_id: 'trace-general',
        routing: fastRouting({
          requested_mode: 'auto',
          route: 'general',
          executed_system: 'general',
          reason_code: 'deterministic_general',
          estimated_searches: 0,
        }),
      }),
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Auto')

    expect(await screen.findByText('Hello!')).toBeInTheDocument()
    expect(screen.getByText('요청 Auto')).toBeInTheDocument()
    expect(screen.getByText('Router general')).toBeInTheDocument()
    expect(screen.getByText('실행 general')).toBeInTheDocument()
    expect(screen.getAllByText(/deterministic_general/)).toHaveLength(2)
    const resultPanel = screen.getByLabelText('대화 및 결과')
    expect(within(resultPanel).queryByText('인용 유효')).not.toBeInTheDocument()
    expect(within(resultPanel).queryByText('not_used')).not.toBeInTheDocument()
  })

  it('shows clarification routing while keeping retrieval diagnostics out of result badges', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>({
        conversation_id: 'conversation-clarify',
        mode: 'fast_rag',
        answer: '조회할 기간을 알려주세요.',
        references: [],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'not_used',
        },
        disclosures: [],
        trace_id: 'trace-clarify',
        routing: fastRouting({
          requested_mode: 'auto',
          route: 'clarify',
          executed_system: 'clarification',
          reason_code: 'model_clarify',
          estimated_searches: 0,
        }),
      }),
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Auto')

    expect(await screen.findByRole('heading', { name: 'Clarification' })).toBeInTheDocument()
    expect(screen.getByText('Router clarify')).toBeInTheDocument()
    expect(screen.getByText('실행 clarification')).toBeInTheDocument()
    const resultPanel = screen.getByLabelText('대화 및 결과')
    expect(within(resultPanel).queryByText('인용 유효')).not.toBeInTheDocument()
    expect(within(resultPanel).queryByText('not_used')).not.toBeInTheDocument()
    expect(within(screen.getByLabelText('API 검사기')).getByText('not_used')).toBeInTheDocument()
  })

  it('renders a synchronous Deep answer without job tracking', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>(
        {
          conversation_id: 'conversation-2',
          mode: 'deep_research',
          answer: '완료된 보고서 [S1]',
          references: [
            {
              evidence_id: 'S1',
              source_type: 'mail',
              document_id: 'opaque-1',
              title: '조사 근거',
              excerpt: '확인된 내용',
            },
          ],
          quality: {
            citation_valid: true,
            limited_answer: false,
            retrieval_mode: 'hybrid',
          },
          disclosures: [],
          trace_id: 'trace-deep',
          routing: deepRouting(),
        },
        200,
      ),
    )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Deep 강제')

    expect(await screen.findByText(/완료된 보고서/)).toBeInTheDocument()
    expect(screen.getByText('trace-deep')).toBeInTheDocument()
    expect(screen.getByText('owner: kim')).toBeInTheDocument()
    expect(service.researchAction).not.toHaveBeenCalled()
    expect(service.streamResearchEvents).not.toHaveBeenCalled()
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
          routing: deepRouting(),
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
    await submitQuestion(user, 'Deep 강제')

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
    await submitQuestion(user, 'Fast 강제')

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
          routing: deepRouting(),
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
    await submitQuestion(user, 'Deep 강제')

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
          routing: fastRouting(),
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
          routing: fastRouting(),
        }),
      )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Fast 강제')
    await screen.findByText('첫 답변')
    expect(screen.getByLabelText('conversation_id 자동 입력')).toHaveValue(
      'conversation-followup',
    )

    const composer = screen.getByRole('textbox', { name: '메시지' })
    await user.type(composer, '후속 질문')
    await user.click(screen.getByRole('button', { name: '전송' }))

    await waitFor(() => expect(service.sendChat).toHaveBeenCalledTimes(2))
    expect(service.sendChat).toHaveBeenLastCalledWith(
      expect.objectContaining({ conversation_id: 'conversation-followup' }),
    )
    expect(screen.getByText('최근 현황을 알려줘')).toBeInTheDocument()
    expect(screen.getByText('첫 답변')).toBeInTheDocument()
    expect(screen.getByText('후속 질문')).toBeInTheDocument()
    expect(await screen.findByText('후속 답변')).toBeInTheDocument()
    expect(composer).toHaveValue('')
  })

  it('uses Enter to send, Shift+Enter for a newline, and blocks an empty message', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat).mockResolvedValue(
      exchange<ChatResponse>({
        conversation_id: 'conversation-keys',
        mode: 'fast_rag',
        answer: '키 입력 답변',
        references: [],
        quality: { citation_valid: true, limited_answer: false, retrieval_mode: 'hybrid' },
        disclosures: [],
        trace_id: 'trace-keys',
        routing: fastRouting(),
      }),
    )

    render(<RagLabApp service={service} />)
    await user.type(screen.getByLabelText('user_id'), 'kim')
    const composer = screen.getByRole('textbox', { name: '메시지' })
    const send = screen.getByRole('button', { name: '전송' })
    expect(send).toBeDisabled()

    await user.type(composer, '첫 줄')
    fireEvent.keyDown(composer, { key: 'Enter', shiftKey: true })
    expect(service.sendChat).not.toHaveBeenCalled()

    fireEvent.change(composer, { target: { value: '첫 줄\n둘째 줄' } })
    fireEvent.keyDown(composer, { key: 'Enter' })

    await waitFor(() => expect(service.sendChat).toHaveBeenCalledTimes(1))
    expect(service.sendChat).toHaveBeenCalledWith(
      expect.objectContaining({ message: '첫 줄\n둘째 줄' }),
    )
  })

  it('does not reuse a conversation id after the owner changes', async () => {
    const user = userEvent.setup()
    const service = baseService()
    vi.mocked(service.sendChat)
      .mockResolvedValueOnce(
        exchange<ChatResponse>({
          conversation_id: 'conversation-kim',
          mode: 'fast_rag',
          answer: '김 답변',
          references: [],
          quality: { citation_valid: true, limited_answer: false, retrieval_mode: 'hybrid' },
          disclosures: [],
          trace_id: 'trace-kim',
          routing: fastRouting(),
        }),
      )
      .mockResolvedValueOnce(
        exchange<ChatResponse>({
          conversation_id: 'conversation-lee',
          mode: 'fast_rag',
          answer: '이 답변',
          references: [],
          quality: { citation_valid: true, limited_answer: false, retrieval_mode: 'hybrid' },
          disclosures: [],
          trace_id: 'trace-lee',
          routing: fastRouting(),
        }),
      )

    render(<RagLabApp service={service} />)
    await submitQuestion(user, 'Auto')
    await screen.findByText('김 답변')

    await user.clear(screen.getByLabelText('user_id'))
    await user.type(screen.getByLabelText('user_id'), 'lee')
    await user.type(screen.getByRole('textbox', { name: '메시지' }), '새 소유자 질문')
    await user.click(screen.getByRole('button', { name: '전송' }))

    await waitFor(() => expect(service.sendChat).toHaveBeenCalledTimes(2))
    expect(service.sendChat).toHaveBeenLastCalledWith(
      expect.not.objectContaining({ conversation_id: 'conversation-kim' }),
    )
  })
})
