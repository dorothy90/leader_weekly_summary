import { useEffect, useRef, useState } from 'react'

import type { RagApiClient } from '../services/ragApiService'
import { ConversationPanel } from './ConversationPanel'
import { InspectorPanel } from './InspectorPanel'
import { RequestPanel } from './RequestPanel'
import type {
  ApiExchange,
  ChatPayload,
  ChatResponse,
  ConversationTurn,
  RagRequestSettings,
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
  const [mobileView, setMobileView] = useState<'request' | 'result' | 'inspector'>('result')
  const [settings, setSettings] = useState<RagRequestSettings>()
  const [requesting, setRequesting] = useState(false)
  const [turns, setTurns] = useState<ConversationTurn[]>([])
  const [activeJob, setActiveJob] = useState<ResearchJobResponse>()
  const [exchange, setExchange] = useState<InspectableExchange>()
  const [jobExchange, setJobExchange] = useState<ApiExchange<ResearchJobResponse>>()
  const [conversationId, setConversationId] = useState('')
  const [conversationOwner, setConversationOwner] = useState<string>()
  const [events, setEvents] = useState<RecordedResearchEvent[]>([])
  const activeOwner = useRef('')
  const activeTurnId = useRef<number | undefined>(undefined)
  const turnSequence = useRef(0)
  const streamController = useRef<AbortController | undefined>(undefined)
  const pollingTimer = useRef<number | undefined>(undefined)
  const activeRun = useRef(0)

  const updateTurn = (turnId: number, patch: Partial<ConversationTurn>) => {
    setTurns((prior) =>
      prior.map((turn) => (turn.id === turnId ? { ...turn, ...patch } : turn)),
    )
  }

  const updateTurnJob = (turnId: number, nextJob: ResearchJobResponse) => {
    setActiveJob(nextJob)
    setTurns((prior) =>
      prior.map((turn) => (turn.id === turnId ? { ...turn, job: nextJob } : turn)),
    )
  }

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

  const refreshJob = async (jobId: string, owner: string, runId: number, turnId: number) => {
    const next = await service.researchAction(jobId, 'status', owner)
    if (runId !== activeRun.current) return undefined
    setJobExchange(next)
    if (next.response) updateTurnJob(turnId, next.response)
    if (next.response && terminal.has(next.response.status)) stopTracking()
    return next
  }

  const pollJob = (jobId: string, owner: string, runId: number, turnId: number) => {
    pollingTimer.current = window.setTimeout(async () => {
      if (runId !== activeRun.current) return
      try {
        const current = await refreshJob(jobId, owner, runId, turnId)
        if (!current || current.error?.retryable ||
          (current.response && !terminal.has(current.response.status))) {
          pollJob(jobId, owner, runId, turnId)
        }
      } catch {
        if (runId === activeRun.current) pollJob(jobId, owner, runId, turnId)
      }
    }, pollIntervalMs)
  }

  const trackJob = async (
    jobId: string,
    owner: string,
    runId: number,
    turnId: number,
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
          setEvents((prior) => [...prior, { ...event, receivedAt: new Date().toISOString() }])
          setTurns((prior) => prior.map((turn) =>
            turn.id === turnId && turn.job
              ? { ...turn, job: { ...turn.job, status: event.status, progress: event.progress } }
              : turn,
          ))
          setActiveJob((prior) =>
            prior ? { ...prior, status: event.status, progress: event.progress } : prior,
          )
          if (terminal.has(event.status)) {
            receivedTerminal = true
            void refreshJob(jobId, owner, runId, turnId)
              .then((latest) => {
                if (latest?.error?.retryable) pollJob(jobId, owner, runId, turnId)
              })
              .catch(() => pollJob(jobId, owner, runId, turnId))
          }
        },
        controller.signal,
      )
      if (!receivedTerminal && runId === activeRun.current) throw new Error('EVENT_STREAM_ENDED_EARLY')
    } catch (error) {
      if (controller.signal.aborted || runId !== activeRun.current) return
      if (reconnectAttempt === 0) {
        setEvents((prior) => [...prior, {
          job_id: jobId,
          status: 'running',
          progress: 0,
          receivedAt: new Date().toISOString(),
          note: 'SSE 재연결 시도',
        }])
        await trackJob(jobId, owner, runId, turnId, 1)
        return
      }
      setEvents((prior) => [...prior, {
        job_id: jobId,
        status: activeJob?.status ?? 'running',
        progress: activeJob?.progress ?? 0,
        receivedAt: new Date().toISOString(),
        note: error instanceof Error ? 'SSE 단절 · 상태 조회로 전환됨' : '상태 조회로 전환됨',
      }])
      pollJob(jobId, owner, runId, turnId)
    }
  }

  const submit = async (message: string) => {
    if (!settings?.userId) return
    const runId = activeRun.current + 1
    const turnId = turnSequence.current + 1
    activeRun.current = runId
    turnSequence.current = turnId
    activeTurnId.current = turnId
    stopTracking()
    setRequesting(true)
    setMobileView('result')
    setActiveJob(undefined)
    setJobExchange(undefined)
    setEvents([])
    setTurns((prior) => [...prior, { id: turnId, question: message, pending: true }])
    activeOwner.current = settings.userId
    const payload: ChatPayload = {
      user_id: settings.userId,
      message,
      ...(settings.conversationId ? { conversation_id: settings.conversationId } : {}),
      filters: settings.filters,
      response_mode: settings.mode,
    }
    const next = await service.sendChat(payload)
    if (runId !== activeRun.current) return
    setExchange(next)
    setRequesting(false)
    if (!next.response) {
      updateTurn(turnId, { pending: false, error: next.error })
      return
    }
    setConversationId(next.response.conversation_id)
    setConversationOwner(settings.userId)
    const created = initialJob(next.response)
    updateTurn(turnId, {
      pending: false,
      chat: next.response,
      ...(created ? { job: created } : {}),
    })
    if (!created) return
    setActiveJob(created)
    void trackJob(created.job_id, settings.userId, runId, turnId)
  }

  const jobAction = async (action: 'cancel' | 'retry') => {
    const turnId = activeTurnId.current
    if (!activeJob || !activeOwner.current || turnId === undefined) return
    const runId = activeRun.current
    stopTracking()
    const next = await service.researchAction(activeJob.job_id, action, activeOwner.current)
    if (runId !== activeRun.current) return
    setJobExchange(next)
    if (!next.response) {
      if (next.error?.retryable) pollJob(activeJob.job_id, activeOwner.current, runId, turnId)
      return
    }
    updateTurnJob(turnId, next.response)
    if (action === 'retry' && !terminal.has(next.response.status)) {
      setEvents([])
      void trackJob(next.response.job_id, activeOwner.current, runId, turnId)
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
        conversationId={conversationId}
        conversationOwner={conversationOwner}
        apiError={exchange?.error}
        onConversationIdChange={setConversationId}
        onSettingsChange={setSettings}
      />
      <ConversationPanel
        turns={turns}
        requesting={requesting}
        canSend={Boolean(settings?.userId)}
        activeJob={activeJob}
        onSend={(message) => void submit(message)}
        onCancel={() => void jobAction('cancel')}
        onRetry={() => void jobAction('retry')}
      />
      <InspectorPanel exchange={exchange} jobExchange={jobExchange} events={events} />
    </div>
  )
}
