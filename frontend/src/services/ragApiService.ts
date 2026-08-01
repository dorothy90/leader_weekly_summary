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

const safeError = (payload: unknown): SafeApiError => {
  if (!payload || typeof payload !== 'object') {
    return { code: 'HTTP_ERROR', message: SAFE_FAILURE_MESSAGE, retryable: false }
  }
  const envelope = payload as ErrorEnvelope
  const error = envelope.error
  if (!error || typeof error !== 'object') {
    return { code: 'HTTP_ERROR', message: SAFE_FAILURE_MESSAGE, retryable: false }
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
      exchange.error = safeError(payload)
      return exchange
    }
    exchange.response = payload as T
    return exchange
  }

  sendChat(payload: ChatPayload) {
    return this.requestJson<ChatResponse>({
      method: 'POST',
      path: '/v1/chat',
      body: payload,
    })
  }

  researchAction(
    jobId: string,
    action: 'status' | 'cancel' | 'retry',
    userId: string,
  ) {
    return this.requestJson<ResearchJobResponse>({
      method: 'POST',
      path: `/v1/research/${encodeURIComponent(jobId)}/${action}`,
      body: { user_id: userId },
    })
  }

  getHealth() {
    return this.requestJson<HealthResponse>({ method: 'GET', path: '/health' })
  }

  getReadiness() {
    return this.requestJson<ReadinessResponse>({ method: 'GET', path: '/ready' })
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
          const parsed = JSON.parse(data) as ResearchEvent
          onEvent(parsed)
        }
        if (done) break
      }
    } finally {
      reader.releaseLock()
    }
  }
}
