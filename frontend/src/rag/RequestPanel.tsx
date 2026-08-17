import { useEffect, useMemo, useState } from 'react'

import type {
  RagRequestSettings,
  RetrievalFilters,
  SafeApiError,
} from './types'

interface RequestPanelProps {
  onSettingsChange: (settings?: RagRequestSettings) => void
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
  onSettingsChange,
  conversationId,
  conversationOwner,
  onConversationIdChange,
  apiError,
}: RequestPanelProps) {
  const [userId, setUserId] = useState('')
  const [teams, setTeams] = useState('')
  const [weeks, setWeeks] = useState('')
  const [mailType, setMailType] = useState<RetrievalFilters['mail_type']>()
  const normalizedWeeks = useMemo(() => splitValues(weeks), [weeks])
  const weekError = normalizedWeeks.some((week) => !/^\d{4}-\d{2}$/.test(week))
  const weekDescription = [weekError ? 'rag-request-error' : '', apiError ? 'rag-api-error' : '']
    .filter(Boolean)
    .join(' ') || undefined

  useEffect(() => {
    if (weekError) {
      onSettingsChange(undefined)
      return
    }
    onSettingsChange({
      userId: userId.trim(),
      ...(conversationId.trim() ? { conversationId: conversationId.trim() } : {}),
      filters: {
        teams: splitValues(teams),
        weeks: normalizedWeeks,
        ...(mailType ? { mail_type: mailType } : {}),
      },
    })
  }, [conversationId, mailType, normalizedWeeks, onSettingsChange, teams, userId, weekError])

  return (
    <form className="rag-request-panel" onSubmit={(event) => event.preventDefault()} noValidate>
      <div className="rag-panel-heading">
        <span className="eyebrow">Request scope</span>
        <h2 id="rag-console-title">RAG 검증 콘솔</h2>
        <p>왼쪽은 요청 범위만 설정합니다. 메시지는 대화창 아래에서 보냅니다.</p>
      </div>

      <label className="field">
        <span>user_id</span>
        <input
          required
          value={userId}
          onChange={(event) => {
            const nextUserId = event.target.value
            setUserId(nextUserId)
            if (nextUserId.trim() !== conversationOwner) onConversationIdChange('')
          }}
          autoComplete="off"
          placeholder="kim"
        />
      </label>

      <label className="field">
        <span>conversation_id <small>자동 입력</small></span>
        <input
          value={conversationId}
          onChange={(event) => onConversationIdChange(event.target.value)}
          autoComplete="off"
          placeholder="첫 응답 후 자동 연결"
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
          aria-invalid={weekError}
          aria-describedby={weekDescription}
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
            setMailType((event.target.value || undefined) as RetrievalFilters['mail_type'])
          }
        >
          <option value="">전체</option>
          <option value="weekly_report">weekly_report</option>
          <option value="daily_report">daily_report</option>
          <option value="other">other</option>
        </select>
      </label>

      {weekError ? (
        <p id="rag-request-error" className="form-error" role="alert">
          주차는 YYYY-WW 형식으로 입력해 주세요.
        </p>
      ) : null}
      {apiError ? (
        <p id="rag-api-error" className="form-error" role="alert">
          <strong>{apiError.code}</strong> · {apiError.message}
        </p>
      ) : null}
    </form>
  )
}
