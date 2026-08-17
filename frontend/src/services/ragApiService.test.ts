import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RagApiService } from './ragApiService'

const encoder = new TextEncoder()

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  })

const chatResponse = (updates: Record<string, unknown> = {}) => ({
  conversation_id: 'conversation-1',
  answer: '일정 답변 [S1]',
  references: [],
  quality: {
    citation_valid: true,
    limited_answer: false,
    retrieval_mode: 'hybrid',
  },
  disclosures: [],
  trace_id: 'trace-1',
  agent_trace: {
    tool_calls: ['search_calendar'],
    judge_decisions: ['sufficient'],
    iteration_count: 1,
  },
  execution: {
    status: 'succeeded',
    failure_stage: null,
    error_code: null,
    retryable: false,
    search_count: 1,
    evidence_count: 1,
    duration_ms: 12,
    include_in_llm_history: true,
    node_runs: [],
  },
  ...updates,
})

const completedJob = {
  job_id: 'job-1',
  status: 'completed',
  progress: 100,
  plan_summary: '조사 완료',
  result_markdown: '보고서 [S1]',
  references: [],
  disclosures: [],
  error_code: null,
}

describe('RagApiService', () => {
  const fetchMock = vi.fn<typeof fetch>()

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(performance, 'now').mockReturnValueOnce(100).mockReturnValueOnce(142)
  })

  afterEach(() => {
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('sends one route-free chat request and accepts bounded agent trace', async () => {
    fetchMock.mockResolvedValue(jsonResponse(chatResponse()))
    const service = new RagApiService('/api/')

    const exchange = await service.sendChat({
      user_id: 'kim',
      message: '질문',
      filters: {
        teams: ['YIELD'],
        weeks: ['2026-31'],
        mail_type: 'weekly_report',
      },
    })

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/chat',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({
          user_id: 'kim',
          message: '질문',
          filters: {
            teams: ['YIELD'],
            weeks: ['2026-31'],
            mail_type: 'weekly_report',
          },
        }),
      }),
    )
    expect(exchange.status).toBe(200)
    expect(exchange.durationMs).toBe(42)
    expect(exchange.response?.agent_trace?.tool_calls).toEqual(['search_calendar'])
    expect(exchange.request.body).not.toHaveProperty('response_mode')
  })

  it('accepts references from every supported source type', async () => {
    const references = [
      ['calendar', 'event-1'],
      ['domain_knowledge', 'domain-1'],
      ['mail', 'mail-1'],
      ['wiki', 'wiki-1'],
      ['statistic', 'stat-1'],
    ].map(([source_type, document_id], index) => ({
      evidence_id: `S${index + 1}`,
      source_type,
      document_id,
      title: '근거',
      excerpt: '검증된 내용',
    }))
    fetchMock.mockResolvedValue(jsonResponse(chatResponse({ references })))

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '회의와 도메인 지식을 알려줘',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.error).toBeUndefined()
    expect(exchange.response?.references.map((item) => item.source_type)).toEqual([
      'calendar',
      'domain_knowledge',
      'mail',
      'wiki',
      'statistic',
    ])
  })

  it('rejects unsupported evidence sources', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(chatResponse({
        references: [{
          evidence_id: 'S1',
          source_type: 'chat_log',
          document_id: 'chat-1',
          title: '내부 로그',
          excerpt: '노출 금지',
        }],
      })),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '질문',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.response).toBeUndefined()
    expect(exchange.error?.code).toBe('INVALID_RESPONSE')
  })

  it('accepts a typed failed agent execution', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(chatResponse({
        answer: null,
        references: [],
        quality: {
          citation_valid: null,
          limited_answer: true,
          retrieval_mode: 'not_started',
        },
        execution: {
          status: 'failed',
          failure_stage: 'planning',
          error_code: 'LLM_TIMEOUT',
          retryable: true,
          search_count: 0,
          evidence_count: 0,
          duration_ms: 5000,
          include_in_llm_history: false,
          node_runs: [],
        },
      })),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '질문',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.error).toBeUndefined()
    expect(exchange.response?.execution?.error_code).toBe('LLM_TIMEOUT')
    expect(exchange.response?.quality?.citation_valid).toBeNull()
  })

  it('rejects the obsolete route envelope', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        ...chatResponse(),
        mode: 'fast_rag',
        routing: {
          requested_mode: 'fast',
          route: 'fast',
          executed_system: 'fast_rag',
          reason_code: 'explicit_mode',
          confidence: 1,
          estimated_searches: 1,
        },
      }),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '질문',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.response).toBeUndefined()
    expect(exchange.error?.code).toBe('INVALID_RESPONSE')
  })

  it.each(['status', 'cancel', 'retry'] as const)(
    'keeps explicit research %s independent from chat',
    async (action) => {
      fetchMock.mockResolvedValue(jsonResponse(completedJob))

      await new RagApiService('/api').researchAction('job/1', action, 'kim')

      expect(fetchMock).toHaveBeenCalledWith(
        `/api/v1/research/job%2F1/${action}`,
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ user_id: 'kim' }),
        }),
      )
    },
  )

  it('returns only the safe FastAPI error envelope', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        {
          error: {
            code: 'INDEX_UNAVAILABLE',
            message: '검색 서비스를 현재 사용할 수 없습니다.',
            retryable: true,
          },
          trace_id: 'trace-safe',
        },
        503,
      ),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '질문',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.error).toEqual({
      code: 'INDEX_UNAVAILABLE',
      message: '검색 서비스를 현재 사용할 수 없습니다.',
      retryable: true,
      traceId: 'trace-safe',
    })
    expect(exchange.response).toBeUndefined()
  })

  it('rejects malformed nested references', async () => {
    fetchMock.mockResolvedValue(jsonResponse(chatResponse({ references: [null] })))

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '질문',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.response).toBeUndefined()
    expect(exchange.error?.code).toBe('INVALID_RESPONSE')
  })

  it('does not expose an HTML failure body', async () => {
    fetchMock.mockResolvedValue(
      new Response('<html>/srv/private secret</html>', {
        status: 502,
        headers: { 'content-type': 'text/html' },
      }),
    )

    const exchange = await new RagApiService('/api').getHealth()

    expect(exchange.error?.message).toBe('요청을 처리할 수 없습니다.')
    expect(JSON.stringify(exchange)).not.toContain('/srv/private')
  })

  it('parses split research event frames and sends the owner', async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('data: {"job_id":"j1","status":"run'))
        controller.enqueue(encoder.encode('ning","progress":40}\n\n'))
        controller.enqueue(
          encoder.encode(
            'data: {"job_id":"j1","status":"completed","progress":100}\n\n',
          ),
        )
        controller.close()
      },
    })
    fetchMock.mockResolvedValue(
      new Response(stream, {
        status: 200,
        headers: { 'content-type': 'text/event-stream' },
      }),
    )
    const events: Array<{ status: string; progress: number }> = []
    const controller = new AbortController()

    await new RagApiService('/api').streamResearchEvents(
      'j1',
      'kim',
      (event) => events.push(event),
      controller.signal,
    )

    expect(events).toEqual([
      expect.objectContaining({ status: 'running', progress: 40 }),
      expect.objectContaining({ status: 'completed', progress: 100 }),
    ])
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/v1/research/j1/events',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ user_id: 'kim' }),
        signal: controller.signal,
      }),
    )
  })
})
