import { useRef, useState, type KeyboardEvent } from 'react'

import type {
  ApiExchange,
  ChatResponse,
  RecordedResearchEvent,
  ResearchJobResponse,
} from './types'

type InspectableResponse = ChatResponse | ResearchJobResponse
type InspectorTab = 'summary' | 'json' | 'events'

interface InspectorPanelProps {
  exchange?: ApiExchange<InspectableResponse>
  jobExchange?: ApiExchange<ResearchJobResponse>
  events: RecordedResearchEvent[]
  copyText?: (value: string) => Promise<void>
}

const isChatResponse = (value: InspectableResponse | undefined): value is ChatResponse =>
  Boolean(value && 'mode' in value)

const requestOwner = (exchange: ApiExchange<unknown> | undefined) => {
  const body = exchange?.request.body
  if (!body || typeof body !== 'object' || !('user_id' in body)) return '서버 미제공'
  return typeof body.user_id === 'string' ? body.user_id : '서버 미제공'
}

export function InspectorPanel({
  exchange,
  jobExchange,
  events,
  copyText,
}: InspectorPanelProps) {
  const [tab, setTab] = useState<InspectorTab>('summary')
  const [copyStatus, setCopyStatus] = useState<'request' | 'response' | 'error'>()
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([])
  const response = exchange?.response
  const chat = isChatResponse(response) ? response : undefined
  const job = jobExchange?.response ?? (
    response && !isChatResponse(response) ? response : undefined
  )
  const requestJson = JSON.stringify(exchange?.request ?? {}, null, 2)
  const responseJson = JSON.stringify(
    exchange?.response ?? exchange?.error ?? {},
    null,
    2,
  )
  const jobJson = JSON.stringify(jobExchange ?? {}, null, 2)

  const copyJson = async (kind: 'request' | 'response', value: string) => {
    try {
      const writer = copyText ?? ((text: string) => navigator.clipboard.writeText(text))
      await writer(value)
      setCopyStatus(kind)
    } catch {
      setCopyStatus('error')
    }
  }

  const tabs: InspectorTab[] = ['summary', 'json', 'events']
  const moveTab = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
    event.preventDefault()
    const next = event.key === 'Home'
      ? 0
      : event.key === 'End'
        ? tabs.length - 1
        : (index + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length
    setTab(tabs[next])
    tabRefs.current[next]?.focus()
  }

  return (
    <aside className="inspector-panel" aria-label="API 검사기">
      <div className="inspector-heading">
        <div>
          <span className="eyebrow">Verification</span>
          <h2>Request inspector</h2>
        </div>
        <span className="api-target">/api</span>
      </div>
      <div className="inspector-tabs" role="tablist" aria-label="검사기 보기">
        {tabs.map((value, index) => (
          <button
            key={value}
            ref={(element) => { tabRefs.current[index] = element }}
            type="button"
            role="tab"
            id={`inspector-tab-${value}`}
            aria-controls={`inspector-panel-${value}`}
            aria-selected={tab === value}
            tabIndex={tab === value ? 0 : -1}
            className={tab === value ? 'is-active' : ''}
            onClick={() => setTab(value)}
            onKeyDown={(event) => moveTab(event, index)}
          >
            {value === 'summary' ? 'Summary' : value === 'json' ? 'JSON' : 'Events'}
          </button>
        ))}
      </div>

      {tab === 'summary' ? (
        <div
          id="inspector-panel-summary"
          className="inspector-content"
          role="tabpanel"
          aria-labelledby="inspector-tab-summary"
        >
          {!exchange ? <p className="inspector-empty">요청을 실행하면 진단 정보가 기록됩니다.</p> : null}
          {exchange ? (
            <>
              <div className="metric-grid">
                <div className="metric"><small>HTTP</small><strong>{exchange.status}</strong></div>
                <div className="metric"><small>Latency</small><strong>{exchange.durationMs} ms</strong></div>
                <div className="metric"><small>Mode</small><strong>{chat?.mode ?? 'deep_research'}</strong></div>
                <div className="metric"><small>References</small><strong>{job?.references.length ?? response?.references.length ?? 0}</strong></div>
              </div>
              <dl className="trace-list">
                <div><dt>trace_id</dt><dd>{chat?.trace_id ?? '서버 미제공'}</dd></div>
                <div><dt>conversation</dt><dd>{chat?.conversation_id ?? '서버 미제공'}</dd></div>
                <div><dt>job_id</dt><dd>{chat?.job_id ?? job?.job_id ?? '해당 없음'}</dd></div>
                <div><dt>requested</dt><dd>{chat?.routing.requested_mode ?? '서버 미제공'}</dd></div>
                <div><dt>route</dt><dd>{chat?.routing.route ?? '서버 미제공'}</dd></div>
                <div><dt>executed</dt><dd>{chat?.routing.executed_system ?? '서버 미제공'}</dd></div>
                <div><dt>reason</dt><dd>{chat?.routing.reason_code ?? '서버 미제공'}</dd></div>
                <div><dt>confidence</dt><dd>{chat ? `${Math.round(chat.routing.confidence * 100)}%` : '서버 미제공'}</dd></div>
                <div><dt>searches</dt><dd>{chat?.routing.estimated_searches ?? '서버 미제공'}</dd></div>
                <div><dt>retrieval</dt><dd>{chat?.quality?.retrieval_mode ?? '서버 미제공'}</dd></div>
                <div><dt>citation</dt><dd>{chat?.quality ? String(chat.quality.citation_valid) : '서버 미제공'}</dd></div>
              </dl>
              {exchange.error ? (
                <div className="error-diagnostic" role="alert">
                  <strong>{exchange.error.code}</strong>
                  <p>{exchange.error.message}</p>
                  <small>retryable: {String(exchange.error.retryable)}</small>
                </div>
              ) : null}
              <div className="verification-strip">
                <span className={exchange.error ? 'is-bad' : 'is-good'}>
                  {exchange.error ? '요청 실패' : '응답 수신'}
                </span>
                <span>owner: {requestOwner(exchange)}</span>
                <span>budget: 서버 미제공</span>
              </div>
              {jobExchange ? (
                <dl className="trace-list">
                  <div><dt>latest job HTTP</dt><dd>{jobExchange.status}</dd></div>
                  <div><dt>latest latency</dt><dd>{jobExchange.durationMs} ms</dd></div>
                  <div><dt>job error</dt><dd>{job?.error_code ?? jobExchange.error?.code ?? '없음'}</dd></div>
                </dl>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      {tab === 'json' ? (
        <div
          id="inspector-panel-json"
          className="inspector-content json-inspector"
          role="tabpanel"
          aria-labelledby="inspector-tab-json"
        >
          <section>
            <div className="section-heading-row">
              <h3>Request</h3>
              <button
                type="button"
                className="json-copy-button"
                aria-label="요청 JSON 복사"
                onClick={() => void copyJson('request', requestJson)}
              >
                {copyStatus === 'request' ? '복사됨' : '복사'}
              </button>
            </div>
            <pre>{requestJson}</pre>
          </section>
          <section>
            <div className="section-heading-row">
              <h3>Response</h3>
              <button
                type="button"
                className="json-copy-button"
                aria-label="응답 JSON 복사"
                onClick={() => void copyJson('response', responseJson)}
              >
                {copyStatus === 'response' ? '복사됨' : '복사'}
              </button>
            </div>
            <pre>{responseJson}</pre>
            {copyStatus === 'error' ? (
              <p className="form-error" role="alert">클립보드 복사에 실패했습니다.</p>
            ) : null}
          </section>
          {jobExchange ? (
            <section>
              <div className="section-heading-row"><h3>Latest job exchange</h3></div>
              <pre>{jobJson}</pre>
            </section>
          ) : null}
        </div>
      ) : null}

      {tab === 'events' ? (
        <div
          id="inspector-panel-events"
          className="inspector-content event-list"
          role="tabpanel"
          aria-labelledby="inspector-tab-events"
        >
          {!events.length ? <p className="inspector-empty">수신된 Deep 이벤트가 없습니다.</p> : null}
          {events.map((event, index) => (
            <div className="event-row" key={`${event.receivedAt}-${index}`}>
              <time>{new Date(event.receivedAt).toLocaleTimeString('ko-KR')}</time>
              <strong>{event.status}</strong>
              <span>{event.progress}%</span>
              {event.note ? <small>{event.note}</small> : null}
            </div>
          ))}
        </div>
      ) : null}
    </aside>
  )
}
