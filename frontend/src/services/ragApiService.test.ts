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
        routing: {
          requested_mode: 'fast',
          route: 'fast',
          executed_system: 'fast_rag',
          reason_code: 'explicit_mode',
          confidence: 1,
          estimated_searches: 1,
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

  it('accepts deterministic retrieval from a demo chat response', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        conversation_id: 'c-demo',
        mode: 'fast_rag',
        answer: 'Cell Leakage는 저장 전하 누설 현상입니다 [S1]',
        references: [
          {
            evidence_id: 'S1',
            source_type: 'domain_knowledge',
            document_id: 'domain-cell-leakage',
            title: 'Cell Leakage',
            excerpt: '저장 전하 누설 현상이다.',
          },
        ],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'deterministic',
        },
        routing: {
          requested_mode: 'fast',
          route: 'fast',
          executed_system: 'fast_rag',
          reason_code: 'explicit_mode',
          confidence: 1,
          estimated_searches: 1,
        },
        disclosures: [],
        trace_id: 'trace-demo',
      }),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: 'Cell Leakage가 뭐야?',
      response_mode: 'fast',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.error).toBeUndefined()
    expect(exchange.response?.quality?.retrieval_mode).toBe('deterministic')
  })

  it('accepts general routing with retrieval not used', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        conversation_id: 'c-general',
        mode: 'fast_rag',
        answer: 'Hello!',
        references: [],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'not_used',
        },
        routing: {
          requested_mode: 'auto',
          route: 'general',
          executed_system: 'general',
          reason_code: 'deterministic_general',
          confidence: 1,
          estimated_searches: 0,
        },
        disclosures: [],
        trace_id: 'trace-general',
      }),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: 'hi',
      response_mode: 'auto',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.error).toBeUndefined()
    expect(exchange.response?.routing.route).toBe('general')
    expect(exchange.response?.quality?.retrieval_mode).toBe('not_used')
  })

  it('accepts references from every supported source type', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        conversation_id: 'c-multi-source',
        mode: 'fast_rag',
        answer: '확인된 답변입니다 [S1] [S2] [S3] [S4] [S5]',
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
        routing: {
          requested_mode: 'fast',
          route: 'fast',
          executed_system: 'fast_rag',
          reason_code: 'explicit_mode',
          confidence: 1,
          estimated_searches: 1,
        },
        disclosures: [],
        trace_id: 'trace-multi-source',
      }),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '회의와 도메인 지식을 알려줘',
      response_mode: 'fast',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.error).toBeUndefined()
    expect(exchange.response?.references).toHaveLength(5)
    expect(exchange.response?.references.map((reference) => reference.source_type)).toEqual([
      'calendar',
      'domain_knowledge',
      'mail',
      'wiki',
      'statistic',
    ])
  })

  it('rejects an unsupported reference source type', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        conversation_id: 'c-unsupported-source',
        mode: 'fast_rag',
        answer: '지원하지 않는 근거입니다 [S1]',
        references: [
          {
            evidence_id: 'S1',
            source_type: 'chat_log',
            document_id: 'chat-1',
            title: '내부 대화 로그',
            excerpt: '외부에 노출하면 안 되는 값이다.',
          },
        ],
        quality: {
          citation_valid: true,
          limited_answer: false,
          retrieval_mode: 'hybrid',
        },
        routing: {
          requested_mode: 'fast',
          route: 'fast',
          executed_system: 'fast_rag',
          reason_code: 'explicit_mode',
          confidence: 1,
          estimated_searches: 1,
        },
        disclosures: [],
        trace_id: 'trace-unsupported-source',
      }),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '지원하지 않는 출처를 확인해줘',
      response_mode: 'fast',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.response).toBeUndefined()
    expect(exchange.error?.code).toBe('INVALID_RESPONSE')
  })

  it('accepts typed failed execution with unrun citation and search', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        conversation_id: 'c-failed',
        mode: 'fast_rag',
        answer: null,
        references: [],
        quality: {
          citation_valid: null,
          limited_answer: true,
          retrieval_mode: 'not_started',
        },
        routing: {
          requested_mode: 'fast',
          route: 'fast',
          executed_system: 'fast_rag',
          reason_code: 'explicit_mode',
          confidence: 1,
          estimated_searches: 1,
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
        },
        disclosures: [],
        trace_id: 'trace-failed',
      }),
    )

    const exchange = await new RagApiService('/api').sendChat({
      user_id: 'kim',
      message: '질문',
      response_mode: 'fast',
      filters: { teams: [], weeks: [] },
    })

    expect(exchange.error).toBeUndefined()
    expect(exchange.response?.execution?.error_code).toBe('LLM_TIMEOUT')
    expect(exchange.response?.quality?.citation_valid).toBeNull()
  })

  it('rejects a successful chat response without routing diagnostics', async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        conversation_id: 'c1',
        mode: 'fast_rag',
        answer: '답변',
        references: [],
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
