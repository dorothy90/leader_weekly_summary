import { useState, type FormEvent } from 'react'

import type {
  ExecutionMode,
  RagRequestDraft,
  RetrievalFilters,
  SafeApiError,
} from './types'

interface RequestPanelProps {
  disabled: boolean
  onSubmit: (draft: RagRequestDraft) => void
  conversationId: string
  conversationOwner?: string
  onConversationIdChange: (value: string) => void
  apiError?: SafeApiError
}

const splitValues = (value: string) =>
  Array.from(
    new Set(
      value
        .split(/[,\n]/)
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  )

export function RequestPanel({
  disabled,
  onSubmit,
  conversationId,
  conversationOwner,
  onConversationIdChange,
  apiError,
}: RequestPanelProps) {
  const [userId, setUserId] = useState('')
  const [mode, setMode] = useState<ExecutionMode>('fast')
  const [teams, setTeams] = useState('')
  const [weeks, setWeeks] = useState('')
  const [mailType, setMailType] = useState<RetrievalFilters['mail_type']>()
  const [question, setQuestion] = useState('')
  const [error, setError] = useState('')
  const [errorField, setErrorField] = useState<'user_id' | 'question' | 'weeks'>()
  const describedBy = (field: typeof errorField) =>
    [errorField === field ? 'rag-request-error' : '', apiError ? 'rag-api-error' : '']
      .filter(Boolean)
      .join(' ') || undefined

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const owner = userId.trim()
    const prompt = question.trim()
    const normalizedWeeks = splitValues(weeks)
    if (!owner) {
      setError('user_id를 입력해 주세요.')
      setErrorField('user_id')
      return
    }
    if (!prompt) {
      setError('질문을 입력해 주세요.')
      setErrorField('question')
      return
    }
    if (normalizedWeeks.some((week) => !/^\d{4}-\d{2}$/.test(week))) {
      setError('주차는 YYYY-WW 형식으로 입력해 주세요.')
      setErrorField('weeks')
      return
    }
    setError('')
    setErrorField(undefined)
    onSubmit({
      userId: owner,
      mode,
      ...(conversationId.trim()
        ? { conversationId: conversationId.trim() }
        : {}),
      question: prompt,
      filters: {
        teams: splitValues(teams),
        weeks: normalizedWeeks,
        ...(mailType ? { mail_type: mailType } : {}),
      },
    })
  }

  return (
    <form className="rag-request-panel" onSubmit={submit} noValidate>
      <div className="rag-panel-heading">
        <span className="eyebrow">Request scope</span>
        <h2 id="rag-console-title">RAG 검증 콘솔</h2>
        <p>실제 API 요청과 검증 상태를 한 화면에서 확인합니다.</p>
      </div>

      <label className="field">
        <span>user_id</span>
        <input
          required
          aria-invalid={errorField === 'user_id'}
          aria-describedby={describedBy('user_id')}
          value={userId}
          onChange={(event) => {
            const nextUserId = event.target.value
            setUserId(nextUserId)
            if (nextUserId.trim() !== conversationOwner) {
              onConversationIdChange('')
            }
          }}
          autoComplete="off"
          placeholder="kim"
        />
      </label>

      <fieldset className="mode-fieldset">
        <legend>실행 시스템</legend>
        <div className="mode-switch">
          {(['fast', 'deep'] as const).map((value) => (
            <button
              key={value}
              type="button"
              aria-pressed={mode === value}
              className={mode === value ? 'is-active' : ''}
              onClick={() => setMode(value)}
            >
              {value === 'fast' ? 'Fast' : 'Deep'}
            </button>
          ))}
        </div>
      </fieldset>

      <label className="field">
        <span>conversation_id <small>선택</small></span>
        <input
          value={conversationId}
          onChange={(event) => onConversationIdChange(event.target.value)}
          autoComplete="off"
          placeholder="새 대화는 비워두세요"
        />
      </label>

      <label className="field">
        <span>팀 필터</span>
        <input
          value={teams}
          onChange={(event) => setTeams(event.target.value)}
          placeholder="YIELD, 품질"
        />
      </label>
      <p className="field-help">팀은 검색 범위만 좁히며 권한은 user_id로 검사합니다.</p>

      <label className="field">
        <span>주차 필터</span>
        <input
          aria-invalid={errorField === 'weeks'}
          aria-describedby={describedBy('weeks')}
          value={weeks}
          onChange={(event) => setWeeks(event.target.value)}
          placeholder="2026-31, 2026-32"
        />
      </label>

      <label className="field">
        <span>메일 유형</span>
        <select
          value={mailType ?? ''}
          onChange={(event) =>
            setMailType(
              (event.target.value || undefined) as RetrievalFilters['mail_type'],
            )
          }
        >
          <option value="">전체</option>
          <option value="weekly_report">weekly_report</option>
          <option value="daily_report">daily_report</option>
          <option value="other">other</option>
        </select>
      </label>

      <label className="field">
        <span>질문</span>
        <textarea
          required
          aria-invalid={errorField === 'question'}
          aria-describedby={describedBy('question')}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          placeholder="최근 4주 수율 저하 원인을 보고서로 정리해줘."
          rows={5}
        />
      </label>

      {error ? <p id="rag-request-error" className="form-error" role="alert">{error}</p> : null}
      {apiError ? (
        <p id="rag-api-error" className="form-error" role="alert">
          <strong>{apiError.code}</strong> · {apiError.message}
        </p>
      ) : null}
      <button className="button button-primary rag-run-button" disabled={disabled}>
        {disabled ? '실행 중…' : '실행'}
      </button>
    </form>
  )
}
