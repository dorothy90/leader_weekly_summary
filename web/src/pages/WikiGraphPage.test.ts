import { describe, expect, it } from 'vitest'

import { buildWikiGraph } from './WikiGraphPage'
import type { Agenda, AliasRecord, Taxonomy } from '../types'

const taxonomy: Taxonomy = {
  version: 1,
  is_dummy: true,
  notice: 'test',
  group_aliases: [],
  domains: [{
    id: 'dram',
    name: 'DRAM',
    techs: [{
      id: 'spica',
      name: 'Spica',
      aliases: [],
      lotcds: [{ code: '4SA', fab_id: '4', product_code: 'SA', product: 'LPDDR5 24G', aliases: [] }],
    }],
  }],
}

const agenda: Agenda = {
  id: 'agenda-1',
  mail_id: 'mail-1',
  source_quote: 'quote',
  summary: '4SA 수율 하락',
  scope: 'lotcd',
  target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
  candidate_paths: [],
  topic: 'yield',
  state: 'open',
  confidence: 0.9,
  review_required: false,
  review_status: 'confirmed',
  subject: 'Spica 주간 수율',
  sender_team: 'Spica수율',
  received_at: '2026-07-06T10:20:00+09:00',
}

const alias: AliasRecord = {
  id: 1,
  value: 'SP 24G',
  target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }],
}

describe('buildWikiGraph', () => {
  it('links taxonomy, agenda, source mail, and alias nodes', () => {
    const graph = buildWikiGraph(taxonomy, [agenda], [alias])
    const ids = new Set(graph.nodes.map((node) => node.id))
    const kinds = new Set(graph.links.map((link) => link.kind))

    expect(ids.has('domain:DRAM')).toBe(true)
    expect(ids.has('tech:DRAM:Spica')).toBe(true)
    expect(ids.has('lotcd:4SA')).toBe(true)
    expect(ids.has('agenda:agenda-1')).toBe(true)
    expect(ids.has('mail:mail-1')).toBe(true)
    expect(ids.has('alias:1')).toBe(true)
    expect(kinds).toEqual(new Set(['contains', 'source', 'target', 'alias']))
  })
})
