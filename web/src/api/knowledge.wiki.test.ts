import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchWikiBuild,
  fetchLotcdWiki,
  fetchTeamWiki,
  fetchTopic,
  fetchWeekWiki,
  resolveWikiReview,
  startWikiBuild,
} from './knowledge'
import type { WikiBuildRun } from '../types'

describe('Wiki API', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    vi.mocked(fetch).mockImplementation(async () =>
      new Response(JSON.stringify({})),
    )
  })

  it('encodes all four Wiki mode routes', async () => {
    await fetchTopic('T/001')
    await fetchLotcdWiki('DRAM', 'Spica X', '4SA')
    await fetchTeamWiki('Yield & Quality')
    await fetchWeekWiki('2026-W30')

    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
      '/api/knowledge/wiki/topics/T%2F001',
      '/api/knowledge/wiki/lotcd/DRAM/Spica%20X/4SA',
      '/api/knowledge/wiki/teams/Yield%20%26%20Quality',
      '/api/knowledge/wiki/weeks/2026-W30',
    ])
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
})
