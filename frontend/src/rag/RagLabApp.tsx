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

export function RagLabApp({ service }: RagLabAppProps) {
  const [requesting, setRequesting] = useState(false)
  const [question, setQuestion] = useState<string>()
  const [chat, setChat] = useState<ChatResponse>()
  const [job, setJob] = useState<ResearchJobResponse>()
  const [exchange, setExchange] = useState<InspectableExchange>()
  const [events, setEvents] = useState<RecordedResearchEvent[]>([])
  const activeOwner = useRef('')
  const streamController = useRef<AbortController | undefined>(undefined)
  const pollingTimer = useRef<number | undefined>(undefined)

  const stopTracking = () => {
    streamController.current?.abort()
    streamController.current = undefined
    if (pollingTimer.current !== undefined) window.clearTimeout(pollingTimer.current)
    pollingTimer.current = undefined
  }

  useEffect(() => stopTracking, [])

  const refreshJob = async (jobId: string, owner: string) => {
    const next = await service.researchAction(jobId, 'status', owner)
    setExchange(next)
    if (next.response) setJob(next.response)
    if (next.response && terminal.has(next.response.status)) stopTracking()
    return next.response
  }

  const pollJob = (jobId: string, owner: string) => {
    pollingTimer.current = window.setTimeout(async () => {
      const current = await refreshJob(jobId, owner)
      if (current && !terminal.has(current.status)) pollJob(jobId, owner)
    }, 2000)
  }

  const trackJob = async (jobId: string, owner: string) => {
    const controller = new AbortController()
    streamController.current = controller
    try {
      await service.streamResearchEvents(
        jobId,
        owner,
        (event) => {
          setEvents((prior) => [
            ...prior,
            { ...event, receivedAt: new Date().toISOString() },
          ])
          setJob((prior) =>
            prior ? { ...prior, status: event.status, progress: event.progress } : prior,
          )
          if (terminal.has(event.status)) void refreshJob(jobId, owner)
        },
        controller.signal,
      )
    } catch (error) {
      if (controller.signal.aborted) return
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
      pollJob(jobId, owner)
    }
  }

  const submit = async (draft: RagRequestDraft) => {
    stopTracking()
    setRequesting(true)
    setQuestion(draft.question)
    setChat(undefined)
    setJob(undefined)
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
    setExchange(next)
    setRequesting(false)
    if (!next.response) return
    if (next.response.mode === 'fast_rag') {
      setChat(next.response)
      return
    }
    const created = initialJob(next.response)
    if (!created) return
    setChat(next.response)
    setJob(created)
    void trackJob(created.job_id, draft.userId)
  }

  const jobAction = async (action: 'cancel' | 'retry') => {
    if (!job || !activeOwner.current) return
    stopTracking()
    const next = await service.researchAction(job.job_id, action, activeOwner.current)
    setExchange(next)
    if (!next.response) return
    setJob(next.response)
    if (action === 'retry' && !terminal.has(next.response.status)) {
      setEvents([])
      void trackJob(next.response.job_id, activeOwner.current)
    }
  }

  return (
    <div className="rag-console-grid">
      <RequestPanel disabled={requesting} onSubmit={(draft) => void submit(draft)} />
      <ConversationPanel
        question={question}
        chat={chat}
        job={job}
        onCancel={() => void jobAction('cancel')}
        onRetry={() => void jobAction('retry')}
      />
      <InspectorPanel exchange={exchange} events={events} />
    </div>
  )
}
