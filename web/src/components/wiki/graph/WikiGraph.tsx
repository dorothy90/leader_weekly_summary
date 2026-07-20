import { useEffect, useMemo, useRef, useState } from 'react'
import type { Core } from 'cytoscape'

import { fetchWikiGraph } from '../../../api/knowledge'
import type { KnowledgeArea, TopicState, WikiGraphView } from '../../../types'
import { GraphInsights } from './GraphInsights'
import { GraphLegend } from './GraphLegend'
import { buildGraphInsights, buildGraphModel } from './graphModel'
import type { GraphEdge, GraphModel } from './graphModel'

interface WikiGraphProps {
  scopeIds: Set<string>
  selectedTopicId: string | null
  onSelectTopic: (topicId: string) => void
}

const areaColors = new Map<string, string>([
  ['yield_defect', '#2b64d8'], ['process_equipment', '#0c6b73'], ['quality_analysis', '#7154a6'],
  ['experiment_validation', '#3c8068'], ['product_production', '#7a6545'], ['schedule_delivery', '#a96512'],
  ['decision_action', '#be4b45'], ['other', '#71818a'],
])
const stateColors = new Map<string, string>([
  ['new', '#2b64d8'], ['investigating', '#7154a6'], ['action_in_progress', '#0c6b73'],
  ['monitoring', '#3c8068'], ['resolved', '#6d8d80'], ['reopened', '#be4b45'],
  ['closed', '#71818a'], ['review_required', '#a96512'],
])
const communityPalette = ['#2b64d8', '#0c6b73', '#7154a6', '#a96512', '#be4b45', '#3c8068', '#596c83']

function colorMap(mode: 'area' | 'state' | 'community', model: GraphModel): Map<string, string> {
  if (mode === 'area') return areaColors
  if (mode === 'state') return stateColors
  return new Map([...new Set(model.nodes.map((node) => String(node.community)))].map((key) => [key, communityPalette[Number(key) % communityPalette.length]]))
}

function nodeColor(mode: 'area' | 'state' | 'community', colors: Map<string, string>, node: GraphModel['nodes'][number]) {
  const key = mode === 'area' ? node.area : mode === 'state' ? node.state : String(node.community)
  return colors.get(key) ?? '#71818a'
}

export function WikiGraph({ scopeIds, selectedTopicId, onSelectTopic }: WikiGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const coreRef = useRef<Core | null>(null)
  const [view, setView] = useState<WikiGraphView | null>(null)
  const [error, setError] = useState(false)
  const [reloadKey, setReloadKey] = useState(0)
  const [query, setQuery] = useState('')
  const [stateFilter, setStateFilter] = useState<TopicState | ''>('')
  const [areaFilter, setAreaFilter] = useState<KnowledgeArea | ''>('')
  const [colorMode, setColorMode] = useState<'area' | 'state' | 'community'>('area')
  const [legendCollapsed, setLegendCollapsed] = useState(false)
  const [showInsights, setShowInsights] = useState(false)
  const [highlightIds, setHighlightIds] = useState<string[]>([])
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    setError(false)
    fetchWikiGraph(controller.signal).then(setView).catch((fetchError: unknown) => {
      if (fetchError instanceof DOMException && fetchError.name === 'AbortError') return
      setError(true)
    })
    return () => controller.abort()
  }, [reloadKey])

  const model = useMemo(() => buildGraphModel(view ?? { topics: [], relations: [] }, scopeIds), [view, scopeIds])
  const filteredModel = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    const visible = new Set(model.nodes.filter((node) => {
      if (stateFilter && node.state !== stateFilter) return false
      if (areaFilter && node.area !== areaFilter) return false
      return !normalized || [node.label, node.id, ...node.teams, ...node.paths.flatMap((path) => [path.lotcd ?? '', path.tech ?? ''])]
        .some((value) => value.toLocaleLowerCase().includes(normalized))
    }).map((node) => node.id))
    return {
      nodes: model.nodes.filter((node) => visible.has(node.id)),
      edges: model.edges.filter((edge) => visible.has(edge.source) && visible.has(edge.target)),
    }
  }, [areaFilter, model, query, stateFilter])
  const colors = colorMap(colorMode, filteredModel)
  const insights = useMemo(() => buildGraphInsights(model), [model])

  useEffect(() => {
    if (!containerRef.current || filteredModel.nodes.length === 0) return
    let disposed = false
    let observer: ResizeObserver | null = null
    import('cytoscape').then(({ default: cytoscape }) => {
      if (disposed || !containerRef.current) return
      const core = cytoscape({
        container: containerRef.current,
        elements: [
          ...filteredModel.nodes.map((node) => ({ data: {
            id: node.id, label: node.label, color: nodeColor(colorMode, colors, node),
            size: 24 + Math.min(18, node.degree * 3 + node.evidenceCount), inScope: node.inScope ? 1 : 0,
          }, classes: [selectedTopicId === node.id ? 'selected' : '', highlightIds.includes(node.id) ? 'highlighted' : ''].filter(Boolean).join(' ') })),
          ...filteredModel.edges.map((edge) => ({ data: { id: edge.id, source: edge.source, target: edge.target, state: edge.state, confidence: edge.confidence } })),
        ],
        layout: { name: 'cose', animate: false, randomize: true, nodeRepulsion: () => 6500, idealEdgeLength: () => 92, fit: true, padding: 42 },
        minZoom: .25,
        maxZoom: 2.5,
        wheelSensitivity: .22,
        style: [
          { selector: 'node', style: {
            'background-color': 'data(color)', width: 'data(size)', height: 'data(size)', label: 'data(label)',
            color: '#27333d', 'font-family': 'Pretendard, sans-serif', 'font-size': 10, 'font-weight': 600,
            'text-wrap': 'ellipsis', 'text-max-width': '120px', 'text-valign': 'bottom', 'text-margin-y': 8,
            'border-width': 2, 'border-color': '#ffffff', 'overlay-opacity': 0,
          } },
          { selector: 'node[inScope = 0]', style: { opacity: .5, 'border-style': 'dashed' } },
          { selector: 'node.selected', style: { 'border-width': 4, 'border-color': '#17212b' } },
          { selector: 'node.highlighted', style: { 'border-width': 5, 'border-color': '#a96512', 'z-index': 20 } },
          { selector: 'edge', style: { width: 1.5, 'line-color': '#9eacb2', 'curve-style': 'bezier', opacity: .7 } },
          { selector: 'edge[state = "pending"]', style: { 'line-color': '#a96512', 'line-style': 'dashed', width: 2.2 } },
          { selector: '.faded', style: { opacity: .12, 'text-opacity': 0 } },
        ],
      })
      core.on('tap', 'node', (event) => onSelectTopic(event.target.id()))
      core.on('tap', 'edge', (event) => setSelectedEdge(filteredModel.edges.find((edge) => edge.id === event.target.id()) ?? null))
      core.on('mouseover', 'node', (event) => {
        core.elements().addClass('faded')
        event.target.closedNeighborhood().removeClass('faded')
      })
      core.on('mouseout', 'node', () => core.elements().removeClass('faded'))
      coreRef.current = core
      if (typeof ResizeObserver !== 'undefined') {
        observer = new ResizeObserver(() => { core.resize(); core.fit(undefined, 36) })
        observer.observe(containerRef.current)
      }
    })
    return () => {
      disposed = true
      observer?.disconnect()
      coreRef.current?.destroy()
      coreRef.current = null
    }
  }, [colorMode, colors, filteredModel, highlightIds, onSelectTopic, selectedTopicId])

  function zoom(factor: number) {
    const core = coreRef.current
    if (!core) return
    core.zoom({ level: Math.max(core.minZoom(), Math.min(core.maxZoom(), core.zoom() * factor)), renderedPosition: { x: core.width() / 2, y: core.height() / 2 } })
  }

  return <div className="wiki-graph-view">
    <header className="wiki-graph-view__header">
      <div><h2>Wiki Graph</h2><span>{filteredModel.nodes.length} PAGES</span><span>{filteredModel.edges.length} LINKS</span></div>
      <div className="wiki-graph-view__actions">
        <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="Graph 검색" placeholder="Topic 검색" />
        <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value as TopicState | '')} aria-label="상태 필터"><option value="">모든 상태</option>{[...new Set(model.nodes.map((node) => node.state))].map((state) => <option key={state}>{state}</option>)}</select>
        <select value={areaFilter} onChange={(event) => setAreaFilter(event.target.value as KnowledgeArea | '')} aria-label="영역 필터"><option value="">모든 영역</option>{[...new Set(model.nodes.map((node) => node.area))].map((area) => <option key={area}>{area}</option>)}</select>
        <div className="graph-color-switch">{(['area', 'state', 'community'] as const).map((mode) => <button key={mode} type="button" aria-pressed={colorMode === mode} onClick={() => setColorMode(mode)}>{mode}</button>)}</div>
        <button type="button" className={showInsights ? 'is-active' : ''} onClick={() => setShowInsights((current) => !current)}>Insights <b>{insights.length}</b></button>
        <button type="button" onClick={() => setReloadKey((value) => value + 1)} aria-label="Graph 새로고침">↻</button>
      </div>
    </header>
    <div className="wiki-graph-view__stage">
      {error ? <p className="wiki-graph-view__empty" role="alert">Graph 데이터를 불러오지 못했습니다.</p> : null}
      {!error && view && filteredModel.nodes.length === 0 ? <p className="wiki-graph-view__empty">표시할 Topic이 없습니다.</p> : null}
      <div ref={containerRef} className="wiki-graph-view__canvas" aria-label="Topic 관계 그래프" />
      <div className="graph-zoom-controls"><button type="button" onClick={() => zoom(1.2)} aria-label="확대">＋</button><button type="button" onClick={() => zoom(1 / 1.2)} aria-label="축소">−</button><button type="button" onClick={() => coreRef.current?.fit(undefined, 36)} aria-label="화면 맞춤">⌗</button></div>
      <GraphLegend model={filteredModel} colorMode={colorMode} collapsed={legendCollapsed} colors={colors} onToggle={() => setLegendCollapsed((current) => !current)} />
      {selectedEdge ? <aside className={`graph-relation-detail is-${selectedEdge.state}`} aria-label="관계 상세"><button type="button" onClick={() => setSelectedEdge(null)} aria-label="관계 상세 닫기">×</button><small>{selectedEdge.id}</small><h3>{selectedEdge.kind}</h3><p>{selectedEdge.source} → {selectedEdge.target}</p><dl><div><dt>신뢰도</dt><dd>{Math.round(selectedEdge.confidence * 100)}%</dd></div><div><dt>상태</dt><dd>{selectedEdge.state}</dd></div></dl><footer>{selectedEdge.agendaIds.map((id) => <code key={id}>{id}</code>)}</footer></aside> : null}
    </div>
    {showInsights ? <GraphInsights insights={insights} onHighlight={setHighlightIds} onClose={() => { setShowInsights(false); setHighlightIds([]) }} /> : null}
  </div>
}

export default WikiGraph
