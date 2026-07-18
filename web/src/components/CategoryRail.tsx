import type { Selection } from '../types'

interface CategoryRailProps {
  selection: Selection
  total: number
  reviewOnly: boolean
}

export function CategoryRail({ selection, total, reviewOnly }: CategoryRailProps) {
  const nodes = [
    selection.domain ?? '전체 메모리',
    selection.tech,
    selection.lotcd,
  ].filter((node): node is string => Boolean(node))

  return (
    <section className="category-rail" aria-label="현재 분류 경로">
      <div className="category-rail__eyebrow">
        {reviewOnly ? '검토 필요 agenda' : '현재 탐색 범위'}
      </div>
      <div className="category-rail__track">
        {nodes.map((node, index) => (
          <div className="category-rail__node" key={node}>
            <span>{node}</span>
            {index < nodes.length - 1 ? (
              <span className="category-rail__connector" aria-hidden="true" />
            ) : null}
          </div>
        ))}
      </div>
      <div className="category-rail__count">
        <strong>{total}</strong>
        <span>agenda</span>
      </div>
    </section>
  )
}
