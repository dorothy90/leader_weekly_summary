import type {
  KnowledgeArea,
  TopicListItem,
  TopicRelation,
  TopicState,
  WikiGraphView,
} from '../../../types'

export interface GraphNode {
  id: string
  label: string
  state: TopicState
  area: KnowledgeArea
  importance: TopicListItem['importance']
  evidenceCount: number
  paths: TopicListItem['target_paths']
  teams: string[]
  degree: number
  community: number
  inScope: boolean
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  kind: TopicRelation['kind']
  state: TopicRelation['review_state']
  confidence: number
  agendaIds: string[]
}

export interface GraphModel {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

export type GraphInsightKind = 'isolated' | 'bridge' | 'cross_lotcd' | 'evidence_gap' | 'pending_relation'

export interface GraphInsight {
  id: string
  kind: GraphInsightKind
  title: string
  detail: string
  nodeIds: string[]
  relationId?: string
}

function connectedComponents(ids: string[], adjacency: Map<string, Set<string>>): Map<string, number> {
  const communities = new Map<string, number>()
  let community = 0
  for (const start of ids) {
    if (communities.has(start)) continue
    const queue = [start]
    communities.set(start, community)
    while (queue.length > 0) {
      const current = queue.shift() as string
      for (const neighbor of [...(adjacency.get(current) ?? [])].sort()) {
        if (communities.has(neighbor)) continue
        communities.set(neighbor, community)
        queue.push(neighbor)
      }
    }
    community += 1
  }
  return communities
}

export function buildGraphModel(view: WikiGraphView, scopeIds: Set<string>): GraphModel {
  const relations = [...view.relations]
    .filter((relation) => relation.review_state !== 'rejected')
    .sort((a, b) => a.relation_id.localeCompare(b.relation_id))
  const visible = new Set(scopeIds.size === 0 ? view.topics.map((topic) => topic.topic_id) : scopeIds)
  if (scopeIds.size > 0) {
    for (const relation of relations) {
      if (scopeIds.has(relation.source_topic_id) || scopeIds.has(relation.target_topic_id)) {
        visible.add(relation.source_topic_id)
        visible.add(relation.target_topic_id)
      }
    }
  }
  const topicById = new Map(view.topics.map((topic) => [topic.topic_id, topic]))
  const edges: GraphEdge[] = relations
    .filter((relation) => visible.has(relation.source_topic_id) && visible.has(relation.target_topic_id))
    .filter((relation) => topicById.has(relation.source_topic_id) && topicById.has(relation.target_topic_id))
    .map((relation) => ({
      id: relation.relation_id,
      source: relation.source_topic_id,
      target: relation.target_topic_id,
      kind: relation.kind,
      state: relation.review_state,
      confidence: relation.confidence,
      agendaIds: relation.agenda_ids,
    }))
  const adjacency = new Map<string, Set<string>>()
  for (const id of visible) adjacency.set(id, new Set())
  for (const edge of edges) {
    adjacency.get(edge.source)?.add(edge.target)
    adjacency.get(edge.target)?.add(edge.source)
  }
  const ids = [...visible].filter((id) => topicById.has(id)).sort()
  const communities = connectedComponents(ids, adjacency)
  const nodes = ids.map((id): GraphNode => {
    const topic = topicById.get(id) as TopicListItem
    return {
      id,
      label: topic.title,
      state: topic.state,
      area: topic.primary_area,
      importance: topic.importance,
      evidenceCount: topic.evidence_count,
      paths: topic.target_paths,
      teams: topic.teams,
      degree: adjacency.get(id)?.size ?? 0,
      community: communities.get(id) ?? 0,
      inScope: scopeIds.size === 0 || scopeIds.has(id),
    }
  })
  return { nodes, edges }
}

function articulationPoints(model: GraphModel): Set<string> {
  const adjacency = new Map(model.nodes.map((node) => [node.id, new Set<string>()]))
  for (const edge of model.edges) {
    adjacency.get(edge.source)?.add(edge.target)
    adjacency.get(edge.target)?.add(edge.source)
  }
  const visited = new Set<string>()
  const discovered = new Map<string, number>()
  const low = new Map<string, number>()
  const parent = new Map<string, string | null>()
  const points = new Set<string>()
  let time = 0

  function visit(node: string) {
    visited.add(node)
    discovered.set(node, ++time)
    low.set(node, time)
    let children = 0
    for (const neighbor of adjacency.get(node) ?? []) {
      if (!visited.has(neighbor)) {
        children += 1
        parent.set(neighbor, node)
        visit(neighbor)
        low.set(node, Math.min(low.get(node) as number, low.get(neighbor) as number))
        if (parent.get(node) === null && children > 1) points.add(node)
        if (parent.get(node) !== null && (low.get(neighbor) as number) >= (discovered.get(node) as number)) points.add(node)
      } else if (neighbor !== parent.get(node)) {
        low.set(node, Math.min(low.get(node) as number, discovered.get(neighbor) as number))
      }
    }
  }

  for (const node of model.nodes) {
    if (visited.has(node.id)) continue
    parent.set(node.id, null)
    visit(node.id)
  }
  return points
}

function lotcds(node: GraphNode): Set<string> {
  return new Set(node.paths.flatMap((path) => path.lotcd ? [path.lotcd] : []))
}

export function buildGraphInsights(model: GraphModel): GraphInsight[] {
  const nodeById = new Map(model.nodes.map((node) => [node.id, node]))
  const insights: GraphInsight[] = []
  for (const node of model.nodes) {
    if (node.degree === 0) insights.push({ id: `isolated:${node.id}`, kind: 'isolated', title: '고립된 Topic', detail: node.label, nodeIds: [node.id] })
    if (node.evidenceCount <= 1) insights.push({ id: `evidence:${node.id}`, kind: 'evidence_gap', title: '근거 보강 필요', detail: `${node.label} · 근거 ${node.evidenceCount}건`, nodeIds: [node.id] })
  }
  for (const nodeId of [...articulationPoints(model)].sort()) {
    insights.push({ id: `bridge:${nodeId}`, kind: 'bridge', title: 'Bridge Topic', detail: `${nodeById.get(nodeId)?.label ?? nodeId}이 지식 군집을 연결합니다.`, nodeIds: [nodeId] })
  }
  for (const edge of model.edges) {
    const source = nodeById.get(edge.source)
    const target = nodeById.get(edge.target)
    if (!source || !target) continue
    const sourceLotcds = lotcds(source)
    const crosses = [...lotcds(target)].every((lotcd) => !sourceLotcds.has(lotcd))
    if (sourceLotcds.size > 0 && target.paths.some((path) => path.lotcd) && crosses) {
      insights.push({ id: `cross:${edge.id}`, kind: 'cross_lotcd', title: '교차 LOTCD 연결', detail: `${source.label} ↔ ${target.label}`, nodeIds: [source.id, target.id], relationId: edge.id })
    }
    if (edge.state === 'pending') {
      insights.push({ id: `pending:${edge.id}`, kind: 'pending_relation', title: '관계 검토 대기', detail: `${source.label} ↔ ${target.label}`, nodeIds: [source.id, target.id], relationId: edge.id })
    }
  }
  const order: GraphInsightKind[] = ['pending_relation', 'bridge', 'cross_lotcd', 'evidence_gap', 'isolated']
  return insights.sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind) || a.id.localeCompare(b.id))
}
