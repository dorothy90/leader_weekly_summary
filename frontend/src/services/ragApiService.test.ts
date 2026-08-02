import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { RagApiService } from './ragApiService'

const encoder = new TextEncoder()

const jsonResponse = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
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

  it('sends explicit Fast chat with owner and search facets', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        conversation_id: 'c1',
        mode: 'fast_rag',
        answer: '답 [S1]',
        references: [],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'hybrid',
        },
        disclosures: [],
        trace_id: 't1',
      }),
    )
    const service = new RagApiService('/api/')

    const exchange = await service.sendChat({
      user_id: 'kim',
      message: '질문',
      response_mode: 'fast',
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
          response_mode: 'fast',
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
    expect(exchange.response?.trace_id).toBe('t1')
    expect(exchange.request).not.toHaveProperty('headers')
  })

  it.each(['status', 'cancel', 'retry'] as const)(
    'sends owner for research %s',
    async (action) => {
      fetchMock.mockResolvedValue(jsonResponse(completedJob))
      const service = new RagApiService('/api')

      await service.researchAction('job/1', action, 'kim')

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
      response_mode: 'fast',
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

  it('does not expose an HTML failure body', async () => {
    fetchMock.mockResolvedValue(
      new Response('<html>/srv/private secret</html>', {
        status: 502,
        headers: { 'content-type': 'text/html' },
      }),
    )
    const exchange = await new RagApiService('/api').getHealth()

    expect(exchange.error?.message).toBe('요청을 처리할 수 없습니다.')
    expect(exchange.error?.retryable).toBe(true)
    expect(JSON.stringify(exchange)).not.toContain('/srv/private')
  })

  it('returns a safe protocol error for a successful malformed response', async () => {
    fetchMock.mockResolvedValue(
      new Response('<html>unexpected success</html>', {
        status: 200,
        headers: { 'content-type': 'text/html' },
      }),
    )

    const exchange = await new RagApiService('/api').getHealth()

    expect(exchange.response).toBeUndefined()
    expect(exchange.error).toEqual({
      code: 'INVALID_RESPONSE',
      message: 'API 응답 형식을 확인할 수 없습니다.',
      retryable: true,
    })
  })

  it('rejects malformed nested references in an otherwise successful chat', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        conversation_id: 'c1',
        mode: 'fast_rag',
        answer: '답변',
        references: [null],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'hybrid',
        },
        disclosures: [],
        trace_id: 'trace-1',
      }),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '질문',
      response_mode: 'fast',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.response).toBeUndefined()
    expect(exchange.error?.code).toBe('INVALID_RESPONSE')
  })

  it('parses split POST event-stream frames and sends the owner', async () => {
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
