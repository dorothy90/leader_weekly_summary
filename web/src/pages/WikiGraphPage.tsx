import { useEffect, useMemo, useRef, useState } from 'react'
import ForceGraph2D from 'react-force-graph-2d'
import { Link } from 'react-router-dom'

import { fetchAliases, fetchAllAgendas, fetchTaxonomy } from '../api/knowledge'
import type { Agenda, AliasRecord, Taxonomy } from '../types'

type NodeType = 'domain' | 'tech' | 'lotcd' | 'agenda' | 'mail' | 'alias'

export interface WikiGraphNode {
  id: string
  label: string
  type: NodeType
  domain?: string
  description?: string
  agendaId?: string
  x?: number
  y?: number
}

export interface WikiGraphLink {
  source: string | WikiGraphNode
  target: string | WikiGraphNode
  kind: 'contains' | 'target' | 'source' | 'alias'
}

const nodeColors: Record<NodeType, string> = {
  domain: '#facc15',
  tech: '#fb923c',
  lotcd: '#a78bfa',
  agenda: '#60a5fa',
  mail: '#4ade80',
  alias: '#9ca3af',
}

const nodeSizes: Record<NodeType, number> = {
  domain: 8,
  tech: 6,
  lotcd: 5,
  agenda: 3.5,
  mail: 4,
  alias: 2.5,
}

function categoryNodeId(domain: string, tech?: string | null, lotcd?: string | null) {
  if (lotcd) return `lotcd:${lotcd}`
  if (tech) return `tech:${domain}:${tech}`
  return `domain:${domain}`
}

export function buildWikiGraph(
  taxonomy: Taxonomy,
  agendas: Agenda[],
  aliases: AliasRecord[],
) {
  const nodes = new Map<string, WikiGraphNode>()
  const links: WikiGraphLink[] = []
  const addNode = (node: WikiGraphNode) => nodes.set(node.id, node)

  for (const domain of taxonomy.domains) {
    const domainId = categoryNodeId(domain.name)
    addNode({ id: domainId, label: domain.name, type: 'domain', domain: domain.name })
    for (const tech of domain.techs) {
      const techId = categoryNodeId(domain.name, tech.name)
      addNode({ id: techId, label: tech.name, type: 'tech', domain: domain.name })
      links.push({ source: domainId, target: techId, kind: 'contains' })
      for (const lotcd of tech.lotcds) {
        const lotcdId = categoryNodeId(domain.name, tech.name, lotcd.code)
        addNode({
          id: lotcdId,
          label: lotcd.code,
          type: 'lotcd',
          domain: domain.name,
          description: lotcd.product,
        })
        links.push({ source: techId, target: lotcdId, kind: 'contains' })
      }
    }
  }

  for (const agenda of agendas) {
    const agendaId = `agenda:${agenda.id}`
    const mailId = `mail:${agenda.mail_id}`
    addNode({
      id: agendaId,
      label: agenda.summary,
      type: 'agenda',
      agendaId: agenda.id,
      description: `${agenda.topic} · ${agenda.state}`,
    })
    if (!nodes.has(mailId)) {
      addNode({
        id: mailId,
        label: agenda.subject,
        type: 'mail',
        description: agenda.sender_team,
      })
    }
    links.push({ source: mailId, target: agendaId, kind: 'source' })
    for (const path of agenda.target_paths) {
      links.push({
        source: agendaId,
        target: categoryNodeId(path.domain, path.tech, path.lotcd),
        kind: 'target',
      })
    }
  }

  for (const alias of aliases) {
    const aliasId = `alias:${alias.id}`
    addNode({ id: aliasId, label: alias.value, type: 'alias' })
    for (const path of alias.target_paths) {
      links.push({
        source: aliasId,
        target: categoryNodeId(path.domain, path.tech, path.lotcd),
        kind: 'alias',
      })
    }
  }

  return { nodes: [...nodes.values()], links }
}

function nodeId(value: string | WikiGraphNode) {
  return typeof value === 'string' ? value : value.id
}

function localNodeIds(
  nodes: WikiGraphNode[],
  links: WikiGraphLink[],
  focusId: string,
  depth: number,
) {
  const adjacency = new Map(nodes.map((node) => [node.id, new Set<string>()]))
  for (const link of links) {
    const source = nodeId(link.source)
    const target = nodeId(link.target)
    adjacency.get(source)?.add(target)
    adjacency.get(target)?.add(source)
  }
  const visited = new Set([focusId])
  let frontier = new Set([focusId])
  for (let hop = 0; hop < depth; hop += 1) {
    const next = new Set<string>()
    for (const current of frontier) {
      for (const neighbor of adjacency.get(current) ?? []) {
        if (!visited.has(neighbor)) {
          visited.add(neighbor)
          next.add(neighbor)
        }
      }
    }
    frontier = next
  }
  return visited
}

export default function WikiGraphPage() {
  const containerRef = useRef<HTMLDivElement>(null)
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null)
  const [agendas, setAgendas] = useState<Agenda[]>([])
  const [aliases, setAliases] = useState<AliasRecord[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const [types, setTypes] = useState<Set<NodeType>>(new Set())
  const [localOnly, setLocalOnly] = useState(false)
  const [depth, setDepth] = useState(1)
  const [size, setSize] = useState({ width: 900, height: 700 })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchTaxonomy(controller.signal),
      fetchAllAgendas(controller.signal),
      fetchAliases(controller.signal),
    ])
      .then(([nextTaxonomy, agendaResponse, nextAliases]) => {
        setTaxonomy(nextTaxonomy)
        setAgendas(agendaResponse.items)
        setAliases(nextAliases)
      })
      .catch((loadError: unknown) => {
        if (loadError instanceof Error && loadError.name !== 'AbortError') {
          setError('Knowledge graph 데이터를 불러오지 못했습니다.')
        }
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const element = containerRef.current
    if (!element) return
    const observer = new ResizeObserver(([entry]) => {
      setSize({ width: entry.contentRect.width, height: entry.contentRect.height })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const graph = useMemo(
    () => (taxonomy ? buildWikiGraph(taxonomy, agendas, aliases) : { nodes: [], links: [] }),
    [agendas, aliases, taxonomy],
  )
  const visibleGraph = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    let allowed = new Set(
      graph.nodes
        .filter((node) => types.size === 0 || types.has(node.type))
        .filter(
          (node) =>
            !normalized ||
            `${node.label} ${node.description ?? ''}`.toLowerCase().includes(normalized),
        )
        .map((node) => node.id),
    )
    if (localOnly && selectedId) {
      const local = localNodeIds(graph.nodes, graph.links, selectedId, depth)
      allowed = new Set([...allowed].filter((id) => local.has(id)))
    }
    return {
      nodes: graph.nodes.filter((node) => allowed.has(node.id)),
      links: graph.links.filter(
        (link) => allowed.has(nodeId(link.source)) && allowed.has(nodeId(link.target)),
      ),
    }
  }, [depth, graph, localOnly, query, selectedId, types])
  const selected = graph.nodes.find((node) => node.id === selectedId) ?? null

  function toggleType(type: NodeType) {
    setTypes((current) => {
      const next = new Set(current)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })
  }

  return (
    <div className="wiki-graph-page">
      <aside className="graph-filter-panel">
        <div className="graph-filter-panel__header">
          <Link to="/wiki/docs">← Wiki docs</Link>
          <h1>Knowledge graph</h1>
          <span>{visibleGraph.nodes.length} nodes · {visibleGraph.links.length} links</span>
        </div>
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="노드 검색"
          aria-label="그래프 노드 검색"
        />
        <section>
          <h2>Node types</h2>
          <div className="graph-type-chips">
            {(Object.keys(nodeColors) as NodeType[]).map((type) => (
              <button
                type="button"
                className={types.has(type) ? 'is-active' : ''}
                onClick={() => toggleType(type)}
                key={type}
              >
                <i style={{ background: nodeColors[type] }} />{type}
              </button>
            ))}
          </div>
        </section>
        <section>
          <h2>Local graph</h2>
          <label className="graph-toggle">
            <span>선택 주변만 보기</span>
            <input
              type="checkbox"
              checked={localOnly}
              disabled={!selectedId}
              onChange={(event) => setLocalOnly(event.target.checked)}
            />
          </label>
          <label className="graph-depth">
            <span>Depth</span>
            <input
              type="range"
              min="1"
              max="3"
              value={depth}
              onChange={(event) => setDepth(Number(event.target.value))}
            />
            <strong>{depth}</strong>
          </label>
        </section>
      </aside>

      <main className="graph-stage" ref={containerRef}>
        {error ? <div className="graph-state graph-state--error">{error}</div> : null}
        {!error && !taxonomy ? <div className="graph-state">Graph loading…</div> : null}
        {taxonomy ? (
          <ForceGraph2D<WikiGraphNode, WikiGraphLink>
            width={size.width}
            height={size.height}
            graphData={visibleGraph}
            backgroundColor="#161616"
            nodeRelSize={1}
            nodeVal={(node) => nodeSizes[node.type]}
            nodeColor={(node) => nodeColors[node.type]}
            linkColor={(link) => (link.kind === 'target' ? '#554b73' : '#343434')}
            linkWidth={(link) => (link.kind === 'target' ? 1.2 : 0.7)}
            cooldownTicks={120}
            onNodeClick={(node) => {
              setSelectedId(node.id)
              setLocalOnly(true)
            }}
            nodeCanvasObject={(node, context, globalScale) => {
              const radius = nodeSizes[node.type]
              context.beginPath()
              context.arc(node.x ?? 0, node.y ?? 0, radius, 0, Math.PI * 2)
              context.fillStyle = nodeColors[node.type]
              context.fill()
              if (node.id === selectedId) {
                context.strokeStyle = '#ffffff'
                context.lineWidth = 1.5 / globalScale
                context.stroke()
              }
              if (globalScale > 1.15 || node.type !== 'agenda' || node.id === selectedId) {
                context.font = `${Math.max(3.5, 11 / globalScale)}px Inter, sans-serif`
                context.fillStyle = '#d4d4d4'
                context.fillText(node.label, (node.x ?? 0) + radius + 2, (node.y ?? 0) + 2)
              }
            }}
          />
        ) : null}
      </main>

      <aside className="graph-preview">
        {selected ? (
          <>
            <header>
              <span>{selected.type}</span>
              <button type="button" onClick={() => { setSelectedId(null); setLocalOnly(false) }}>×</button>
            </header>
            <h2>{selected.label}</h2>
            {selected.description ? <p>{selected.description}</p> : null}
            <dl>
              <div><dt>node id</dt><dd>{selected.id}</dd></div>
              {selected.domain ? <div><dt>domain</dt><dd>{selected.domain}</dd></div> : null}
            </dl>
            {selected.agendaId ? (
              <Link to={`/wiki/docs?agenda=${encodeURIComponent(selected.agendaId)}`}>
                Wiki note 열기
              </Link>
            ) : null}
          </>
        ) : (
          <div className="graph-preview__empty">노드를 선택하면 Preview가 표시됩니다.</div>
        )}
      </aside>
    </div>
  )
}
