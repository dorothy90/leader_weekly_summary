import { Link } from 'react-router-dom'

import type { GraphInsight } from './graphModel'

const insightLabels: Record<GraphInsight['kind'], string> = {
  isolated: '고립 Topic', bridge: 'Bridge', cross_lotcd: '교차 LOTCD',
  evidence_gap: '근거 공백', pending_relation: '관계 검토',
}

interface GraphInsightsProps {
  insights: GraphInsight[]
  onHighlight: (nodeIds: string[]) => void
  onClose: () => void
}

export function GraphInsights({ insights, onHighlight, onClose }: GraphInsightsProps) {
  return <aside className="graph-insights" aria-label="Graph 인사이트">
    <header><div><small>DETERMINISTIC ANALYSIS</small><strong>Graph Insights</strong></div><button type="button" onClick={onClose} aria-label="인사이트 닫기">×</button></header>
    <div className="graph-insights__body">
      {insights.length === 0 ? <p>현재 범위에서 발견된 인사이트가 없습니다.</p> : insights.map((insight) => <button key={insight.id} type="button" onClick={() => onHighlight(insight.nodeIds)}>
        <span>{insightLabels[insight.kind]}</span><strong>{insight.title}</strong><small>{insight.detail}</small>
        {insight.kind === 'pending_relation' ? <Link to="/wiki/reviews" onClick={(event) => event.stopPropagation()}>검토 열기 →</Link> : null}
      </button>)}
    </div>
  </aside>
}
