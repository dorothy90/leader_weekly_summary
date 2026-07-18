import type { ClassificationItem, DecisionStatus } from '../types'

interface ClassificationItemListProps {
  items: ClassificationItem[]
  selectedId: string | null
  query: string
  onQueryChange: (query: string) => void
  onSelect: (agendaId: string) => void
}

const statusLabels: Record<DecisionStatus, string> = {
  confirmed: '확정',
  aggregate: '종합지표',
  unclassified: '미분류',
  conflict: '충돌',
  review_required: '검토 필요',
  manually_corrected: '수동 수정',
  excluded: '제외',
}

function classificationLabel(item: ClassificationItem) {
  if (item.decision.status === 'aggregate' || item.item_kind === 'aggregate') {
    return '종합지표'
  }
  return item.decision.target_path?.lotcd ?? '미분류'
}

function matchReason(item: ClassificationItem) {
  const match = item.decision.matches[0]
  if (match) return `${match.phrase} · ${match.rule_id}`
  return item.decision.diagnostics[0] ?? '-'
}

export function ClassificationItemList({
  items,
  selectedId,
  query,
  onQueryChange,
  onSelect,
}: ClassificationItemListProps) {
  return (
    <section className="classification-list" aria-labelledby="classification-list-title">
      <header className="classification-list__header">
        <h2 id="classification-list-title">분류 항목</h2>
        <label>
          <span>항목 검색</span>
          <input
            type="search"
            aria-label="분류 항목 검색"
            value={query}
            onChange={(event) => onQueryChange(event.target.value)}
          />
        </label>
      </header>

      <table className="classification-list__table" aria-label="분류 항목 목록">
        <thead>
          <tr>
            <th scope="col">LOTCD</th>
            <th scope="col">Agenda</th>
            <th scope="col">Match reason</th>
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {items.length === 0 ? (
            <tr>
              <td colSpan={4}>조건에 맞는 분류 항목이 없습니다.</td>
            </tr>
          ) : (
            items.map((item) => {
              const selected = item.agenda_id === selectedId
              return (
                <tr
                  key={item.agenda_id}
                  className={selected ? 'is-selected' : ''}
                  tabIndex={0}
                  aria-selected={selected}
                  onClick={() => onSelect(item.agenda_id)}
                  onKeyDown={(event) => {
                    if (event.key !== 'Enter' && event.key !== ' ') return
                    event.preventDefault()
                    onSelect(item.agenda_id)
                  }}
                >
                  <td>{classificationLabel(item)}</td>
                  <td>{item.summary}</td>
                  <td>{matchReason(item)}</td>
                  <td>{statusLabels[item.decision.status]}</td>
                </tr>
              )
            })
          )}
        </tbody>
      </table>
    </section>
  )
}
