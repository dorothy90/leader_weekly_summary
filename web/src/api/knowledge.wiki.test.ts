import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  fetchLotcdWiki,
  fetchTeamWiki,
  fetchTopic,
  fetchWeekWiki,
  resolveWikiReview,
} from './knowledge'

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
})
