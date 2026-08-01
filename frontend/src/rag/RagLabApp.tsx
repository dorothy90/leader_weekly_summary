import { useEffect, useRef, useState } from 'react'

import type { RagApiClient } from '../services/ragApiService'
import { ConversationPanel } from './ConversationPanel'
import { InspectorPanel } from './InspectorPanel'
import { RequestPanel } from './RequestPanel'
import type {
  ApiExchange,
  ChatPayload,
  ChatResponse,
  RagRequestDraft,
  RecordedResearchEvent,
  ResearchJobResponse,
  ResearchStatus,
} from './types'

interface RagLabAppProps {
  service: RagApiClient
  pollIntervalMs?: number
}

type InspectableExchange = ApiExchange<ChatResponse | ResearchJobResponse>

const terminal = new Set<ResearchStatus>(['completed', 'failed', 'cancelled'])

const initialJob = (response: ChatResponse): ResearchJobResponse | undefined => {
  if (!response.job_id || !response.status) return undefined
  return {
    job_id: response.job_id,
    status: response.status,
    progress: 0,
    plan_summary: response.plan_summary ?? '',
    result_markdown: null,
    references: [],
    disclosures: response.disclosures,
    error_code: null,
  }
}

export function RagLabApp({ service, pollIntervalMs = 2000 }: RagLabAppProps) {
  const [mobileView, setMobileView] = useState<'request' | 'result' | 'inspector'>('request')
  const [requesting, setRequesting] = useState(false)
  const [question, setQuestion] = useState<string>()
  const [chat, setChat] = useState<ChatResponse>()
  const [job, setJob] = useState<ResearchJobResponse>()
  const [exchange, setExchange] = useState<InspectableExchange>()
  const [jobExchange, setJobExchange] = useState<ApiExchange<ResearchJobResponse>>()
  const [conversationId, setConversationId] = useState('')
  const [conversationOwner, setConversationOwner] = useState<string>()
  const [events, setEvents] = useState<RecordedResearchEvent[]>([])
  const activeOwner = useRef('')
  const streamController = useRef<AbortController | undefined>(undefined)
  const pollingTimer = useRef<number | undefined>(undefined)
  const activeRun = useRef(0)

  const stopTracking = () => {
    streamController.current?.abort()
    streamController.current = undefined
    if (pollingTimer.current !== undefined) window.clearTimeout(pollingTimer.current)
    pollingTimer.current = undefined
  }

  useEffect(
    () => () => {
      activeRun.current += 1
      stopTracking()
    },
    [],
  )

  const refreshJob = async (jobId: string, owner: string, runId: number) => {
    const next = await service.researchAction(jobId, 'status', owner)
    if (runId !== activeRun.current) return undefined
    setJobExchange(next)
    if (next.response) setJob(next.response)
    if (next.response && terminal.has(next.response.status)) stopTracking()
    return next
  }

  const pollJob = (jobId: string, owner: string, runId: number) => {
    pollingTimer.current = window.setTimeout(async () => {
      if (runId !== activeRun.current) return
      try {
        const current = await refreshJob(jobId, owner, runId)
        if (
          !current ||
          current.error?.retryable ||
          (current.response && !terminal.has(current.response.status))
        ) {
          pollJob(jobId, owner, runId)
        }
      } catch {
        if (runId === activeRun.current) pollJob(jobId, owner, runId)
      }
    }, pollIntervalMs)
  }

  const trackJob = async (
    jobId: string,
    owner: string,
    runId: number,
    reconnectAttempt = 0,
  ) => {
    const controller = new AbortController()
    streamController.current = controller
    let receivedTerminal = false
    try {
      await service.streamResearchEvents(
        jobId,
        owner,
        (event) => {
          if (runId !== activeRun.current) return
          setEvents((prior) => [
            ...prior,
            { ...event, receivedAt: new Date().toISOString() },
          ])
          setJob((prior) =>
            prior ? { ...prior, status: event.status, progress: event.progress } : prior,
          )
          if (terminal.has(event.status)) {
            receivedTerminal = true
            void refreshJob(jobId, owner, runId)
              .then((latest) => {
                if (latest?.error?.retryable) pollJob(jobId, owner, runId)
              })
              .catch(() => pollJob(jobId, owner, runId))
          }
        },
        controller.signal,
      )
      if (!receivedTerminal && runId === activeRun.current) {
        throw new Error('EVENT_STREAM_ENDED_EARLY')
      }
    } catch (error) {
      if (controller.signal.aborted || runId !== activeRun.current) return
      if (reconnectAttempt === 0) {
        setEvents((prior) => [
          ...prior,
          {
            job_id: jobId,
            status: 'running',
            progress: 0,
            receivedAt: new Date().toISOString(),
            note: 'SSE 재연결 시도',
          },
        ])
        await trackJob(jobId, owner, runId, 1)
        return
      }
      setEvents((prior) => [
        ...prior,
        {
          job_id: jobId,
          status: job?.status ?? 'running',
          progress: job?.progress ?? 0,
          receivedAt: new Date().toISOString(),
          note: error instanceof Error ? 'SSE 단절 · 상태 조회로 전환됨' : '상태 조회로 전환됨',
        },
      ])
      pollJob(jobId, owner, runId)
    }
  }

  const submit = async (draft: RagRequestDraft) => {
    const runId = activeRun.current + 1
    activeRun.current = runId
    stopTracking()
    setRequesting(true)
    setQuestion(draft.question)
    setMobileView('result')
    setChat(undefined)
    setJob(undefined)
    setJobExchange(undefined)
    setEvents([])
    activeOwner.current = draft.userId
    const payload: ChatPayload = {
      user_id: draft.userId,
      message: draft.question,
      ...(draft.conversationId ? { conversation_id: draft.conversationId } : {}),
      filters: draft.filters,
      response_mode: draft.mode,
    }
    const next = await service.sendChat(payload)
    if (runId !== activeRun.current) return
    setExchange(next)
    setRequesting(false)
    if (!next.response) return
    setConversationId(next.response.conversation_id)
    setConversationOwner(draft.userId)
    if (next.response.mode === 'fast_rag') {
      setChat(next.response)
      return
    }
    const created = initialJob(next.response)
    if (!created) return
    setChat(next.response)
    setJob(created)
    void trackJob(created.job_id, draft.userId, runId)
  }

  const jobAction = async (action: 'cancel' | 'retry') => {
    if (!job || !activeOwner.current) return
    const runId = activeRun.current
    stopTracking()
    const next = await service.researchAction(job.job_id, action, activeOwner.current)
    if (runId !== activeRun.current) return
    setJobExchange(next)
    if (!next.response) {
      if (next.error?.retryable) pollJob(job.job_id, activeOwner.current, runId)
      return
    }
    setJob(next.response)
    if (action === 'retry' && !terminal.has(next.response.status)) {
      setEvents([])
      void trackJob(next.response.job_id, activeOwner.current, runId)
    }
  }

  return (
    <div className="rag-console-grid" data-mobile-view={mobileView}>
      <nav className="rag-mobile-nav" aria-label="모바일 패널">
        {([
          ['request', '요청 패널'],
          ['result', '결과 패널'],
          ['inspector', '검사기 패널'],
        ] as const).map(([value, label]) => (
          <button
            key={value}
            type="button"
            className={mobileView === value ? 'is-active' : ''}
            aria-pressed={mobileView === value}
            onClick={() => setMobileView(value)}
          >
            {label}
          </button>
        ))}
      </nav>
      <RequestPanel
        disabled={requesting}
        conversationId={conversationId}
        conversationOwner={conversationOwner}
        apiError={exchange?.error}
        onConversationIdChange={setConversationId}
        onSubmit={(draft) => void submit(draft)}
      />
      <ConversationPanel
        question={question}
        chat={chat}
        job={job}
        error={jobExchange?.error ?? exchange?.error}
        onCancel={() => void jobAction('cancel')}
        onRetry={() => void jobAction('retry')}
      />
      <InspectorPanel exchange={exchange} jobExchange={jobExchange} events={events} />
    </div>
  )
}
