import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

import type { ConversationTurn, ResearchJobResponse } from './types'

interface ConversationPanelProps {
  turns: ConversationTurn[]
  requesting: boolean
  canSend: boolean
  activeJob?: ResearchJobResponse
  onSend: (message: string) => void
  onCancel: () => void
  onRetry: () => void
}

const terminalRetry = new Set(['failed', 'cancelled'])
const cancellable = new Set(['queued', 'running', 'cancelling'])
const modeLabels = {
  auto: 'Auto',
  fast: 'Fast 강제',
  deep: 'Deep 강제',
} as const

function TurnResult({ turn }: { turn: ConversationTurn }) {
  const { chat, job, error } = turn
  const references = job?.references ?? chat?.references ?? []
  const disclosures = job?.disclosures ?? chat?.disclosures ?? []
  const result = job?.result_markdown ?? chat?.answer

  return (
    <article className="conversation-turn" data-turn-id={turn.id}>
      <div className="message is-user">{turn.question}</div>
      {error ? (
        <div className="error-diagnostic result-error" role="alert">
          <strong>{error.code}</strong>
          <p>{error.message}</p>
          <small>{error.retryable ? '재시도 가능' : '재시도 불가'}</small>
        </div>
      ) : null}
      {job?.error_code ? (
        <div className="error-diagnostic result-error" role="alert">
          <strong>{job.error_code}</strong>
          <p>Deep Research 작업이 {job.status} 상태로 종료되었습니다.</p>
        </div>
      ) : null}
      {chat ? (
        <div className="routing-flow" aria-label="라우팅 결과">
          <div className="routing-steps">
            <span>요청 {modeLabels[chat.routing.requested_mode]}</span>
            <i aria-hidden="true">→</i>
            <span>Router {chat.routing.route}</span>
            <i aria-hidden="true">→</i>
            <span>실행 {chat.routing.executed_system}</span>
          </div>
          <small>
            {chat.routing.reason_code} · 신뢰도 {Math.round(chat.routing.confidence * 100)}%
            {' · '}예상 검색 {chat.routing.estimated_searches}회
          </small>
        </div>
      ) : null}
      {job && !result ? (
        <div className="message is-system">
          <strong>{job.plan_summary || '조사 계획을 준비하고 있습니다.'}</strong>
          <div
            className="progress-track"
            role="progressbar"
            aria-label="조사 진행률"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={job.progress}
          >
            <span style={{ width: `${job.progress}%` }} />
          </div>
        </div>
      ) : null}
      {turn.pending && !chat && !error ? (
        <div className="message is-system pending-message" role="status">응답을 기다리는 중…</div>
      ) : null}
      {result ? (
        <div className="message is-assistant">
          <p className="answer-text">{result}</p>
          {chat?.quality && chat.routing.executed_system === 'fast_rag' ? (
            <div className="quality-row" aria-label="답변 품질">
              <span className={chat.quality.citation_valid ? 'is-good' : 'is-bad'}>
                {chat.quality.citation_valid ? '인용 유효' : '인용 실패'}
              </span>
              <span>{chat.quality.retrieval_mode}</span>
              {chat.quality.limited_answer ? <span>제한 답변</span> : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {disclosures.map((item) => (
        <div className="disclosure" key={item} role="status">{item}</div>
      ))}
      {references.length ? (
        <section className="reference-list" aria-label="검증된 인용 근거">
          <div className="section-heading-row">
            <h3>검증된 인용 근거</h3>
            <span>{references.length}</span>
          </div>
          {references.map((reference) => (
            <article className="reference-card" key={reference.evidence_id}>
              <div className="reference-id">{reference.evidence_id}</div>
              <div>
                <strong>{reference.title || reference.source_type}</strong>
                <p>{reference.excerpt}</p>
                <small>
                  {[reference.source_type, reference.team, reference.week]
                    .filter(Boolean)
                    .join(' · ')}
                </small>
              </div>
            </article>
          ))}
        </section>
      ) : null}
    </article>
  )
}

export function ConversationPanel({
  turns,
  requesting,
  canSend,
  activeJob,
  onSend,
  onCancel,
  onRetry,
}: ConversationPanelProps) {
  const [message, setMessage] = useState('')
  const composerRef = useRef<HTMLTextAreaElement>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const latest = turns.at(-1)
  const latestJob = latest?.job
  const resultTitle = latestJob
    ? 'Deep Research'
    : latest?.chat?.routing.executed_system === 'general'
      ? 'General response'
      : latest?.chat?.routing.executed_system === 'clarification'
        ? 'Clarification'
        : latest?.chat
          ? 'Fast answer'
          : 'Conversation'
  const sendDisabled = requesting || !canSend || !message.trim()

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [turns])

  const submitMessage = () => {
    const prompt = message.trim()
    if (!prompt || requesting || !canSend) return
    onSend(prompt)
    setMessage('')
    window.requestAnimationFrame(() => composerRef.current?.focus())
  }

  const onComposerKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.shiftKey) return
    event.preventDefault()
    submitMessage()
  }

  return (
    <section className="conversation-panel" aria-label="대화 및 결과">
      <header className="conversation-header">
        <div>
          <span className="eyebrow">Conversation</span>
          <h2>{resultTitle}</h2>
        </div>
        {latestJob ? (
          <div className={`job-state is-${latestJob.status}`}>
            <span>{latestJob.status}</span>
            <strong>{latestJob.progress}%</strong>
          </div>
        ) : null}
      </header>

      <div className="conversation-scroll" ref={scrollRef}>
        {turns.length ? turns.map((turn) => <TurnResult key={turn.id} turn={turn} />) : (
          <div className="empty-result">
            <strong>대화를 시작하세요.</strong>
            <p>왼쪽에서 user_id를 설정한 뒤 아래 입력창에 메시지를 보내세요.</p>
          </div>
        )}
      </div>

      <footer className="conversation-composer">
        {activeJob && cancellable.has(activeJob.status) ? (
          <button className="button button-secondary" type="button" onClick={onCancel}>
            조사 취소
          </button>
        ) : null}
        {activeJob && terminalRetry.has(activeJob.status) ? (
          <button className="button button-secondary" type="button" onClick={onRetry}>
            조사 재시도
          </button>
        ) : null}
        <label className="composer-field">
          <span className="sr-only">메시지</span>
          <textarea
            ref={composerRef}
            aria-label="메시지"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            onKeyDown={onComposerKeyDown}
            placeholder={canSend ? '메시지를 입력하세요' : '먼저 왼쪽에서 user_id를 입력하세요'}
            rows={1}
          />
        </label>
        <button
          className="button button-primary composer-send"
          type="button"
          disabled={sendDisabled}
          onClick={submitMessage}
        >
          {requesting ? '전송 중…' : '전송'}
        </button>
      </footer>
    </section>
  )
}
