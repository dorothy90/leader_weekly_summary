import type { AgendaFilters, KnowledgeFacets } from '../types'

interface FilterBarProps {
  facets: KnowledgeFacets | null
  filters: AgendaFilters
  onChange: (name: keyof AgendaFilters, value: string) => void
  onReset: () => void
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

const reviewLabels: Record<string, string> = {
  pending: '검토 대기',
  confirmed: '확정됨',
  on_hold: '보류',
}

export function FilterBar({ facets, filters, onChange, onReset }: FilterBarProps) {
  const activeCount = Object.values(filters).filter(Boolean).length

  return (
    <div className="filter-bar" aria-label="agenda 필터">
      <label>
        <span>발신팀</span>
        <select
          value={filters.senderTeam}
          onChange={(event) => onChange('senderTeam', event.target.value)}
        >
          <option value="">전체</option>
          {facets?.sender_teams.map((team) => (
            <option value={team} key={team}>{team}</option>
          ))}
        </select>
      </label>
      <label>
        <span>Topic</span>
        <select
          value={filters.topic}
          onChange={(event) => onChange('topic', event.target.value)}
        >
          <option value="">전체</option>
          {facets?.topics.map((topic) => (
            <option value={topic} key={topic}>{topic}</option>
          ))}
        </select>
      </label>
      <label>
        <span>상태</span>
        <select
          value={filters.state}
          onChange={(event) => onChange('state', event.target.value)}
        >
          <option value="">전체</option>
          {facets?.states.map((state) => (
            <option value={state} key={state}>{stateLabels[state] ?? state}</option>
          ))}
        </select>
      </label>
      <label>
        <span>검토</span>
        <select
          value={filters.reviewStatus}
          onChange={(event) => onChange('reviewStatus', event.target.value)}
        >
          <option value="">전체</option>
          {Object.entries(reviewLabels).map(([value, label]) => (
            <option value={value} key={value}>{label}</option>
          ))}
        </select>
      </label>
      <label className="filter-date">
        <span>시작일</span>
        <input
          type="date"
          value={filters.dateFrom}
          min={facets?.date_min ?? undefined}
          max={filters.dateTo || facets?.date_max || undefined}
          onChange={(event) => onChange('dateFrom', event.target.value)}
        />
      </label>
      <label className="filter-date">
        <span>종료일</span>
        <input
          type="date"
          value={filters.dateTo}
          min={filters.dateFrom || facets?.date_min || undefined}
          max={facets?.date_max ?? undefined}
          onChange={(event) => onChange('dateTo', event.target.value)}
        />
      </label>
      <button type="button" onClick={onReset} disabled={activeCount === 0}>
        초기화{activeCount ? ` ${activeCount}` : ''}
      </button>
    </div>
  )
}
