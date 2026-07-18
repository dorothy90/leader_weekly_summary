import type { ClassificationWeek, DecisionStatus } from '../types'

interface ClassificationWeekNavProps {
  weeks: ClassificationWeek[]
  selectedWeek: string
  lotcdCounts: Record<string, number>
  selectedLotcd: string
  selectedStatus: '' | DecisionStatus
  onWeekChange: (week: string) => void
  onLotcdChange: (lotcd: string) => void
  onStatusChange: (status: '' | DecisionStatus) => void
}

const workflowStateLabels: Record<ClassificationWeek['workflow_state'], string> = {
  not_started: '시작 전',
  processing: '처리 중',
  review_in_progress: '검토 중',
  ready_for_approval: '승인 준비',
  approved: '승인 완료',
  revalidation_required: '재검증 필요',
  failed: '실패',
}

const statusFilters: Array<{ value: DecisionStatus; label: string }> = [
  { value: 'aggregate', label: '종합지표' },
  { value: 'unclassified', label: '미분류' },
  { value: 'conflict', label: '충돌' },
  { value: 'review_required', label: '검토 필요' },
]

const unresolvedStatuses: DecisionStatus[] = [
  'unclassified',
  'conflict',
  'review_required',
]

function weekLabel(week: string) {
  const match = /^(\d{4})-(\d{1,2})$/.exec(week)
  if (!match) return week
  return `${match[1]}-W${match[2].padStart(2, '0')}`
}

function unresolvedCount(week: ClassificationWeek) {
  return unresolvedStatuses.reduce(
    (total, status) => total + (week.counts[status] ?? 0),
    0,
  )
}

export function ClassificationWeekNav({
  weeks,
  selectedWeek,
  lotcdCounts,
  selectedLotcd,
  selectedStatus,
  onWeekChange,
  onLotcdChange,
  onStatusChange,
}: ClassificationWeekNavProps) {
  const chronologicalWeeks = [...weeks].sort((left, right) =>
    left.week.localeCompare(right.week),
  )
  const lotcdEntries = Object.entries(lotcdCounts)
    .filter(([lotcd]) => !/^multi[-_\s]?lotcd$/i.test(lotcd))
    .sort(([left], [right]) => left.localeCompare(right))

  return (
    <aside className="classification-nav" aria-label="분류 탐색">
      <nav className="classification-nav__weeks" aria-label="분류 주차">
        <h2>주차</h2>
        {chronologicalWeeks.map((week) => {
          const unresolved = unresolvedCount(week)
          return (
            <button
              type="button"
              key={week.week}
              className={selectedWeek === week.week ? 'is-selected' : ''}
              aria-pressed={selectedWeek === week.week}
              onClick={() => onWeekChange(week.week)}
            >
              <strong>{weekLabel(week.week)}</strong>
              <span>{workflowStateLabels[week.workflow_state]}</span>
              {unresolved > 0 ? <small>미해결 {unresolved}건</small> : null}
            </button>
          )
        })}
      </nav>

      <section className="classification-nav__filters" aria-labelledby="lotcd-filter-title">
        <h2 id="lotcd-filter-title">LOTCD</h2>
        <button
          type="button"
          aria-pressed={selectedLotcd === ''}
          onClick={() => onLotcdChange('')}
        >
          전체
        </button>
        {lotcdEntries.map(([lotcd, count]) => (
          <button
            type="button"
            key={lotcd}
            className={selectedLotcd === lotcd ? 'is-selected' : ''}
            aria-label={`${lotcd} ${count}건`}
            aria-pressed={selectedLotcd === lotcd}
            onClick={() => onLotcdChange(lotcd)}
          >
            <span>{lotcd}</span>
            <small>{count}건</small>
          </button>
        ))}
      </section>

      <section className="classification-nav__filters" aria-labelledby="status-filter-title">
        <h2 id="status-filter-title">상태</h2>
        <button
          type="button"
          aria-pressed={selectedStatus === ''}
          onClick={() => onStatusChange('')}
        >
          전체
        </button>
        {statusFilters.map((filter) => (
          <button
            type="button"
            key={filter.value}
            className={selectedStatus === filter.value ? 'is-selected' : ''}
            aria-pressed={selectedStatus === filter.value}
            onClick={() => onStatusChange(filter.value)}
          >
            {filter.label}
          </button>
        ))}
      </section>
    </aside>
  )
}
