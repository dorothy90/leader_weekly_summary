import { knowledgeAreaLabels, topicStateLabels } from '../../TopicList'
import type { GraphModel } from './graphModel'

interface GraphLegendProps {
  model: GraphModel
  colorMode: 'area' | 'state' | 'community'
  collapsed: boolean
  colors: Map<string, string>
  onToggle: () => void
}

export function GraphLegend({ model, colorMode, collapsed, colors, onToggle }: GraphLegendProps) {
  const values = new Map<string, number>()
  for (const node of model.nodes) {
    const key = colorMode === 'area' ? node.area : colorMode === 'state' ? node.state : String(node.community)
    values.set(key, (values.get(key) ?? 0) + 1)
  }
  const label = (key: string) => colorMode === 'area'
    ? knowledgeAreaLabels[key as keyof typeof knowledgeAreaLabels]
    : colorMode === 'state' ? topicStateLabels[key as keyof typeof topicStateLabels] : `Community ${Number(key) + 1}`
  return <aside className={`graph-legend ${collapsed ? 'is-collapsed' : ''}`} aria-label="Graph 범례">
    <header><strong>{colorMode === 'community' ? 'COMMUNITIES' : colorMode.toUpperCase()}</strong><button type="button" onClick={onToggle} aria-label={collapsed ? '범례 열기' : '범례 접기'}>{collapsed ? '＋' : '−'}</button></header>
    {collapsed ? null : [...values.entries()].map(([key, count]) => <div key={key}><i style={{ background: colors.get(key) }} /><span>{label(key)}</span><b>{count}</b></div>)}
  </aside>
}
