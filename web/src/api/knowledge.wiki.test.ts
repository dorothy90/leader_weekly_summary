import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchWikiBuild,
  fetchWikiGraph,
  fetchWikiReviews,
  fetchLotcdWiki,
  fetchTeamWiki,
  fetchTopic,
  fetchWeekWiki,
  resolveWikiReview,
  startWikiBuild,
} from './knowledge'
import type { WeekWikiView, WikiBuildRun, WikiReview } from '../types'

describe('Wiki API', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    vi.mocked(fetch).mockImplementation(async () =>
      new Response(JSON.stringify({})),
    )
  })

  it('encodes all four Wiki mode routes', async () => {
    await fetchTopic('T/001')
    await fetchLotcdWiki('DRAM')
    await fetchLotcdWiki('DRAM', 'Spica X')
    await fetchLotcdWiki('DRAM', 'Spica X', '4SA')
    await fetchTeamWiki('Yield & Quality')
    await fetchWeekWiki('2026-W30')

    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
      '/api/knowledge/wiki/topics/T%2F001',
      '/api/knowledge/wiki/lotcd/DRAM',
      '/api/knowledge/wiki/lotcd/DRAM/Spica%20X',
      '/api/knowledge/wiki/lotcd/DRAM/Spica%20X/4SA',
      '/api/knowledge/wiki/teams/Yield%20%26%20Quality',
      '/api/knowledge/wiki/weeks/2026-W30',
    ])
  })

  it('loads the shared graph contract', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify({ topics: [], relations: [] })))

    expect(await fetchWikiGraph()).toEqual({ topics: [], relations: [] })
    expect(vi.mocked(fetch).mock.calls[0]?.[0]).toBe('/api/knowledge/wiki/graph')
  })

  it('serializes a manual attach review decision', async () => {
    await resolveWikiReview('RV/1', { action: 'attach', topic_id: 'T-001' })

    expect(vi.mocked(fetch).mock.calls[0]).toEqual([
      '/api/knowledge/wiki/reviews/RV%2F1/resolve',
      expect.objectContaining({
        method: 'POST',
        body: '{"action":"attach","topic_id":"T-001"}',
      }),
    ])
  })

  it('preserves typed relation review details from the backend', async () => {
    const review: WikiReview = {
      review_id: 'R-REL-001', kind: 'relation', agenda_id: null, candidates: [],
      relation_id: 'REL-001', relation_kind: 'supports', relation_agenda_ids: ['A-001'],
      rationale: 'Evidence supports the relation.', status: 'pending',
    }
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify([review])))

    const [fetched] = await fetchWikiReviews()

    expect(fetched.relation_kind).toBe('supports')
    expect(fetched.relation_agenda_ids).toEqual(['A-001'])
  })

  it('preserves the build taxonomy version from the backend contract', async () => {
    const build: WikiBuildRun = {
      run_id: 'WB/1',
      week: '2026-W30',
      classification_run_id: 'CR-1',
      taxonomy_version: 7,
      status: 'published',
      input_hash: 'hash',
      model: 'model',
      affected_topic_ids: ['T-001'],
      failed_topic_ids: [],
      started_at: '2026-07-20T00:00:00Z',
      completed_at: '2026-07-20T00:01:00Z',
      error: null,
    }
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(build)),
    )
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(build)),
    )

    const started = await startWikiBuild('2026/W30')
    const fetched = await fetchWikiBuild('WB/1')

    expect(started.taxonomy_version).toBe(7)
    expect(fetched.taxonomy_version).toBe(7)
    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
      '/api/knowledge/wiki/builds/2026%2FW30',
      '/api/knowledge/wiki/builds/WB%2F1',
    ])
  })

  it('preserves backend-owned actions in a Week snapshot', async () => {
    const week: WeekWikiView = {
      week: '2026-W30', revision_id: 'WREV-001', published_at: '2026-07-20T00:01:00Z',
      build_run_id: 'RUN-001', new_topic_ids: [], changed_topic_ids: ['T-001'],
      resolved_topic_ids: [], reopened_topic_ids: [], new_relation_ids: [],
      pending_assignment_count: 0, contradictions: [], teams: ['Yield'],
      actions_and_decisions: [{
        topic_id: 'T-001', title: '스냅샷 조치', state: 'monitoring', importance: 'high',
        primary_area: 'yield_defect', target_paths: [], teams: ['Yield'],
        last_updated_week: '2026-W30', evidence_count: 1, rank_reasons: [],
      }],
    }
    vi.mocked(fetch).mockResolvedValueOnce(new Response(JSON.stringify(week)))

    const fetched = await fetchWeekWiki('2026-W30')

    expect(fetched.actions_and_decisions[0].title).toBe('스냅샷 조치')
  })
})
