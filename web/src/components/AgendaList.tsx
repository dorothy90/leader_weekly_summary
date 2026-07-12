import type { Agenda } from '../types'

interface AgendaListProps {
  agendas: Agenda[]
  selectedId: string | null
  loading: boolean
  error: string | null
  onSelect: (agendaId: string) => void
}

const topicLabels: Record<string, string> = {
  action: '조치',
  analysis: '분석',
  decision: '의사결정',
  defect: '불량',
  equipment: '장비',
  performance: '성능',
  policy: '정책',
  program_fail: 'Program fail',
  qualification: '검증',
  reliability: '신뢰성',
  root_cause: '원인',
  schedule: '일정',
  test: 'Test',
  yield: '수율',
}

const stateLabels: Record<string, string> = {
  confirmed: '확정',
  in_progress: '진행 중',
  investigating: '분석 중',
  monitoring: '모니터링',
  open: '미해결',
  pending: '결정 대기',
  planned: '예정',
  positive: '목표 상회',
  resolved: '해결',
  stable: '안정',
}

const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

function primaryLotcd(agenda: Agenda) {
  const codes = agenda.target_paths
    .map((path) => path.lotcd)
    .filter((code): code is string => Boolean(code))
  if (codes.length === 0) return null
  return codes.length > 2 ? `${codes.slice(0, 2).join(' · ')} +${codes.length - 2}` : codes.join(' · ')
}

export function AgendaList({
  agendas,
  selectedId,
  loading,
  error,
  onSelect,
}: AgendaListProps) {
  if (loading) {
    return <div className="list-state">agenda를 불러오는 중</div>
  }
  if (error) {
    return <div className="list-state list-state--error">{error}</div>
  }
  if (agendas.length === 0) {
    return (
      <div className="list-state">
        <strong>조건에 맞는 agenda 없음</strong>
        <span>검색어 또는 분류 범위를 바꿔보세요.</span>
      </div>
    )
  }

  return (
    <div className="agenda-list" aria-label="agenda 목록">
      {agendas.map((agenda) => {
        const lotcd = primaryLotcd(agenda)
        return (
          <button
            type="button"
            className={`agenda-row ${selectedId === agenda.id ? 'is-selected' : ''}`}
            key={agenda.id}
            onClick={() => onSelect(agenda.id)}
          >
            <span className={`state-dot state-dot--${agenda.state}`} aria-hidden="true" />
            <span className="agenda-row__body">
              <span className="agenda-row__labels">
                <span className="topic-label">
                  {topicLabels[agenda.topic] ?? agenda.topic}
                </span>
                {lotcd ? <span className="lotcd-label">{lotcd}</span> : null}
                {agenda.review_required ? (
                  <span className="review-label">검토 필요</span>
                ) : null}
              </span>
              <strong>{agenda.summary}</strong>
              <span className="agenda-row__meta">
                {dateFormatter.format(new Date(agenda.received_at))}
                <i aria-hidden="true" />
                {agenda.sender_team}
                <i aria-hidden="true" />
                {stateLabels[agenda.state] ?? agenda.state}
              </span>
            </span>
            <span className="agenda-row__confidence">
              {Math.round(agenda.confidence * 100)}
              <small>%</small>
            </span>
          </button>
        )
      })}
    </div>
  )
}
