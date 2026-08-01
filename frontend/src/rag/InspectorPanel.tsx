import { useState } from 'react'

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
  events: RecordedResearchEvent[]
}

const isChatResponse = (value: InspectableResponse | undefined): value is ChatResponse =>
  Boolean(value && 'mode' in value)

export function InspectorPanel({ exchange, events }: InspectorPanelProps) {
  const [tab, setTab] = useState<InspectorTab>('summary')
  const response = exchange?.response
  const chat = isChatResponse(response) ? response : undefined
  const job = response && !isChatResponse(response) ? response : undefined

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
        {(['summary', 'json', 'events'] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            className={tab === value ? 'is-active' : ''}
            onClick={() => setTab(value)}
          >
            {value === 'summary' ? 'Summary' : value === 'json' ? 'JSON' : 'Events'}
          </button>
        ))}
      </div>

      {tab === 'summary' ? (
        <div className="inspector-content">
          {!exchange ? <p className="inspector-empty">요청을 실행하면 진단 정보가 기록됩니다.</p> : null}
          {exchange ? (
            <>
              <div className="metric-grid">
                <div className="metric"><small>HTTP</small><strong>{exchange.status}</strong></div>
                <div className="metric"><small>Latency</small><strong>{exchange.durationMs} ms</strong></div>
                <div className="metric"><small>Mode</small><strong>{chat?.mode ?? 'deep_research'}</strong></div>
                <div className="metric"><small>References</small><strong>{response?.references.length ?? 0}</strong></div>
              </div>
              <dl className="trace-list">
                <div><dt>trace_id</dt><dd>{chat?.trace_id ?? '서버 미제공'}</dd></div>
                <div><dt>conversation</dt><dd>{chat?.conversation_id ?? '서버 미제공'}</dd></div>
                <div><dt>job_id</dt><dd>{chat?.job_id ?? job?.job_id ?? '해당 없음'}</dd></div>
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
                <span>owner: request body</span>
                <span>budget: 서버 미제공</span>
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      {tab === 'json' ? (
        <div className="inspector-content json-inspector">
          <section>
            <div className="section-heading-row"><h3>Request</h3></div>
            <pre>{JSON.stringify(exchange?.request ?? {}, null, 2)}</pre>
          </section>
          <section>
            <div className="section-heading-row"><h3>Response</h3></div>
            <pre>{JSON.stringify(exchange?.response ?? exchange?.error ?? {}, null, 2)}</pre>
          </section>
        </div>
      ) : null}

      {tab === 'events' ? (
        <div className="inspector-content event-list">
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
