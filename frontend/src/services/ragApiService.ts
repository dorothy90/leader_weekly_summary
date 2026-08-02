import type {
  ApiExchange,
  ApiRequestRecord,
  ChatPayload,
  ChatResponse,
  HealthResponse,
  ReadinessResponse,
  ResearchEvent,
  ResearchJobResponse,
  SafeApiError,
} from '../rag/types'

const SAFE_FAILURE_MESSAGE = '요청을 처리할 수 없습니다.'
const INVALID_RESPONSE_MESSAGE = 'API 응답 형식을 확인할 수 없습니다.'

const isRecord = (value: unknown): value is Record<string, unknown> =>
  Boolean(value && typeof value === 'object' && !Array.isArray(value))

const researchStatuses = new Set([
  'queued',
  'running',
  'completed',
  'failed',
  'cancelled',
  'cancelling',
])

const isNullableString = (value: unknown) =>
  value === undefined || value === null || typeof value === 'string'

const isStringArray = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((item) => typeof item === 'string')

const isReference = (value: unknown) =>
  isRecord(value) &&
  typeof value.evidence_id === 'string' &&
  ['mail', 'wiki', 'statistic'].includes(String(value.source_type)) &&
  typeof value.document_id === 'string' &&
  typeof value.title === 'string' &&
  typeof value.excerpt === 'string' &&
  isNullableString(value.team) &&
  isNullableString(value.week)

const isReferenceArray = (value: unknown) =>
  Array.isArray(value) && value.every(isReference)

const isQuality = (value: unknown) =>
  isRecord(value) &&
  typeof value.citation_valid === 'boolean' &&
  typeof value.limited_answer === 'boolean' &&
  (value.retrieval_mode === 'hybrid' || value.retrieval_mode === 'bm25')

const isProgress = (value: unknown) =>
  typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 100

const isChatResponse = (value: unknown): value is ChatResponse =>
  isRecord(value) &&
  typeof value.conversation_id === 'string' &&
  (value.mode === 'fast_rag' || value.mode === 'deep_research') &&
  isNullableString(value.answer) &&
  isReferenceArray(value.references) &&
  (value.quality === undefined || value.quality === null || isQuality(value.quality)) &&
  isStringArray(value.disclosures) &&
  typeof value.trace_id === 'string' &&
  isNullableString(value.job_id) &&
  (value.status === undefined || value.status === null || researchStatuses.has(String(value.status))) &&
  isNullableString(value.plan_summary)

const isResearchJobResponse = (value: unknown): value is ResearchJobResponse =>
  isRecord(value) &&
  typeof value.job_id === 'string' &&
  researchStatuses.has(String(value.status)) &&
  isProgress(value.progress) &&
  typeof value.plan_summary === 'string' &&
  isNullableString(value.result_markdown) &&
  isReferenceArray(value.references) &&
  isStringArray(value.disclosures) &&
  isNullableString(value.error_code)

const isHealthResponse = (value: unknown): value is HealthResponse =>
  isRecord(value) && value.status === 'ok'

const isReadinessResponse = (value: unknown): value is ReadinessResponse =>
  isRecord(value) &&
  (value.status === 'ready' || value.status === 'not_ready') &&
  isRecord(value.dependencies) &&
  Object.values(value.dependencies).every((item) => typeof item === 'string')

const isResearchEvent = (value: unknown): value is ResearchEvent =>
  isRecord(value) &&
  typeof value.job_id === 'string' &&
  researchStatuses.has(String(value.status)) &&
  isProgress(value.progress)

export interface RagApiClient {
  sendChat(payload: ChatPayload): Promise<ApiExchange<ChatResponse>>
  researchAction(
    jobId: string,
    action: 'status' | 'cancel' | 'retry',
    userId: string,
  ): Promise<ApiExchange<ResearchJobResponse>>
  getHealth(): Promise<ApiExchange<HealthResponse>>
  getReadiness(): Promise<ApiExchange<ReadinessResponse>>
  streamResearchEvents(
    jobId: string,
    userId: string,
    onEvent: (event: ResearchEvent) => void,
    signal: AbortSignal,
  ): Promise<void>
}

interface ErrorEnvelope {
  error?: {
    code?: unknown
    message?: unknown
    retryable?: unknown
  }
  trace_id?: unknown
}

const safeError = (payload: unknown, status: number): SafeApiError => {
  const retryable = status >= 500
  if (!payload || typeof payload !== 'object') {
    return { code: 'HTTP_ERROR', message: SAFE_FAILURE_MESSAGE, retryable }
  }
  const envelope = payload as ErrorEnvelope
  const error = envelope.error
  if (!error || typeof error !== 'object') {
    return { code: 'HTTP_ERROR', message: SAFE_FAILURE_MESSAGE, retryable }
  }
  return {
    code: typeof error.code === 'string' ? error.code : 'HTTP_ERROR',
    message:
      typeof error.message === 'string' ? error.message : SAFE_FAILURE_MESSAGE,
    retryable: error.retryable === true,
    ...(typeof envelope.trace_id === 'string'
      ? { traceId: envelope.trace_id }
      : {}),
  }
}

export class RagApiService implements RagApiClient {
  private readonly baseUrl: string

  constructor(baseUrl = '/api') {
    this.baseUrl = baseUrl.replace(/\/+$/, '')
  }

  private path(suffix: string) {
    return `${this.baseUrl}${suffix}`
  }

  private async requestJson<T>(
    request: ApiRequestRecord,
    validate: (value: unknown) => value is T,
    signal?: AbortSignal,
  ): Promise<ApiExchange<T>> {
    const started = performance.now()
    let response: Response
    try {
      response = await fetch(this.path(request.path), {
        method: request.method,
        headers: { 'content-type': 'application/json' },
        ...(request.body === undefined
          ? {}
          : { body: JSON.stringify(request.body) }),
        ...(signal ? { signal } : {}),
      })
    } catch (error) {
      const durationMs = Math.max(0, Math.round(performance.now() - started))
      return {
        request,
        status: 0,
        durationMs,
        receivedAt: new Date().toISOString(),
        error: {
          code: error instanceof DOMException && error.name === 'AbortError'
            ? 'ABORTED'
            : 'NETWORK_ERROR',
          message:
            error instanceof DOMException && error.name === 'AbortError'
              ? '요청이 취소되었습니다.'
              : 'API에 연결할 수 없습니다.',
          retryable: true,
        },
      }
    }

    const durationMs = Math.max(0, Math.round(performance.now() - started))
    const contentType = response.headers.get('content-type') ?? ''
    const payload = contentType.includes('application/json')
      ? await response.json().catch(() => undefined)
      : undefined
    const exchange: ApiExchange<T> = {
      request,
      status: response.status,
      durationMs,
      receivedAt: new Date().toISOString(),
    }
    if (!response.ok) {
      exchange.error = safeError(payload, response.status)
      return exchange
    }
    if (!contentType.includes('application/json') || !validate(payload)) {
      exchange.error = {
        code: 'INVALID_RESPONSE',
        message: INVALID_RESPONSE_MESSAGE,
        retryable: true,
      }
      return exchange
    }
    exchange.response = payload
    return exchange
  }

  sendChat(payload: ChatPayload) {
    return this.requestJson<ChatResponse>(
      {
        method: 'POST',
        path: '/v1/chat',
        body: payload,
      },
      isChatResponse,
    )
  }

  researchAction(
    jobId: string,
    action: 'status' | 'cancel' | 'retry',
    userId: string,
  ) {
    return this.requestJson<ResearchJobResponse>(
      {
        method: 'POST',
        path: `/v1/research/${encodeURIComponent(jobId)}/${action}`,
        body: { user_id: userId },
      },
      isResearchJobResponse,
    )
  }

  getHealth() {
    return this.requestJson<HealthResponse>(
      { method: 'GET', path: '/health' },
      isHealthResponse,
    )
  }

  getReadiness() {
    return this.requestJson<ReadinessResponse>(
      { method: 'GET', path: '/ready' },
      isReadinessResponse,
    )
  }

  async streamResearchEvents(
    jobId: string,
    userId: string,
    onEvent: (event: ResearchEvent) => void,
    signal: AbortSignal,
  ): Promise<void> {
    const path = `/v1/research/${encodeURIComponent(jobId)}/events`
    const response = await fetch(this.path(path), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ user_id: userId }),
      signal,
    })
    if (!response.ok || !response.body) {
      throw new Error('EVENT_STREAM_UNAVAILABLE')
    }
    if (!(response.headers.get('content-type') ?? '').includes('text/event-stream')) {
      throw new Error('EVENT_STREAM_INVALID_RESPONSE')
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    try {
      while (true) {
        const { done, value } = await reader.read()
        buffer += decoder.decode(value, { stream: !done })
        const frames = buffer.split(/\r?\n\r?\n/)
        buffer = frames.pop() ?? ''
        for (const frame of frames) {
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trim())
            .join('\n')
          if (!data) continue
          const parsed: unknown = JSON.parse(data)
          if (!isResearchEvent(parsed) || parsed.job_id !== jobId) {
            throw new Error('EVENT_STREAM_INVALID_EVENT')
          }
          onEvent(parsed)
        }
        if (done) break
      }
    } finally {
      reader.releaseLock()
    }
  }
}
