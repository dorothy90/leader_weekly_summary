import { describe, expect, it } from 'vitest'

import type { TopicListItem, WikiGraphView } from '../../../types'
import { buildGraphInsights, buildGraphModel } from './graphModel'

function topic(id: string, lotcd: string, evidenceCount = 2): TopicListItem {
  return {
    topic_id: id, title: `Topic ${id}`, state: 'investigating', importance: 'high',
    primary_area: 'yield_defect', target_paths: [{ domain: lotcd.startsWith('4H') ? 'NAND' : 'DRAM', tech: lotcd.startsWith('4H') ? 'Heraion' : 'Spica', lotcd }],
    teams: ['Team'], last_updated_week: '2026-W30', evidence_count: evidenceCount, rank_reasons: [],
  }
}

const graphFixture: WikiGraphView = {
  topics: [topic('T-1', '4SA'), topic('T-2', '4SA'), topic('T-3', '4H1'), topic('T-4', '4H1', 0), topic('T-5', '6SA')],
  relations: [
    { relation_id: 'R-accepted-1', source_topic_id: 'T-1', target_topic_id: 'T-2', kind: 'supports', agenda_ids: [], confidence: .9, review_state: 'accepted' },
    { relation_id: 'R-accepted-2', source_topic_id: 'T-2', target_topic_id: 'T-3', kind: 'affects', agenda_ids: [], confidence: .8, review_state: 'accepted' },
    { relation_id: 'R-pending', source_topic_id: 'T-3', target_topic_id: 'T-4', kind: 'follow_up', agenda_ids: [], confidence: .6, review_state: 'pending' },
    { relation_id: 'R-rejected', source_topic_id: 'T-1', target_topic_id: 'T-5', kind: 'contradicts', agenda_ids: [], confidence: .5, review_state: 'rejected' },
  ],
}

describe('graphModel', () => {
  it('keeps accepted and pending edges but omits rejected edges', () => {
    const model = buildGraphModel(graphFixture, new Set())
    expect(model.edges.map((edge) => [edge.id, edge.state])).toEqual([
      ['R-accepted-1', 'accepted'], ['R-accepted-2', 'accepted'], ['R-pending', 'pending'],
    ])
  })

  it('keeps scoped nodes and their immediate relation context', () => {
    const model = buildGraphModel(graphFixture, new Set(['T-2']))
    expect(model.nodes.map((node) => node.id)).toEqual(['T-1', 'T-2', 'T-3'])
  })

  it('finds isolated, bridge, cross-LOTCD, weak-evidence, and pending insights', () => {
    const kinds = new Set(buildGraphInsights(buildGraphModel(graphFixture, new Set())).map((item) => item.kind))
    expect(kinds).toEqual(new Set(['isolated', 'bridge', 'cross_lotcd', 'evidence_gap', 'pending_relation']))
  })
})
