import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  approveClassificationWeek,
  correctClassificationItem,
  createClassificationAlias,
  fetchClassificationItem,
  fetchClassificationItems,
  fetchClassificationRunComparison,
  fetchClassificationWeeks,
  runClassificationWeek,
  setClassificationDisposition,
  splitClassificationItem,
} from './knowledge'

describe('classification API', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
    vi.mocked(fetch).mockImplementation(async () =>
      new Response(JSON.stringify({ items: [], total: 0 })),
    )
  })

  it('serializes week, LOTCD, status, and query filters', async () => {
    await fetchClassificationItems('2026/01', {
      lotcd: '4SA',
      status: 'confirmed',
      q: '수율 & 품질',
    })

    expect(vi.mocked(fetch).mock.calls[0][0]).toBe(
      '/api/knowledge/classification/weeks/2026%2F01/items?lotcd=4SA&status=confirmed&q=%EC%88%98%EC%9C%A8+%26+%ED%92%88%EC%A7%88',
    )
  })

  it('fetches weeks, an encoded item, and an encoded run comparison', async () => {
    await fetchClassificationWeeks()
    await fetchClassificationItem('agenda/1')
    await fetchClassificationRunComparison('old/run', 'new/run')

    expect(vi.mocked(fetch).mock.calls.map(([url]) => url)).toEqual([
      '/api/knowledge/classification/weeks',
      '/api/knowledge/classification/items/agenda%2F1',
      '/api/knowledge/classification/runs/old%2Frun/comparison/new%2Frun',
    ])
  })

  it('serializes run and approval mutations', async () => {
    await runClassificationWeek('2026/01', true)
    await approveClassificationWeek('2026/01')

    expect(vi.mocked(fetch).mock.calls[0]).toEqual([
      '/api/knowledge/classification/weeks/2026%2F01/run',
      expect.objectContaining({ method: 'POST', body: '{"rerun":true}' }),
    ])
    expect(vi.mocked(fetch).mock.calls[1]).toEqual([
      '/api/knowledge/classification/weeks/2026%2F01/approve',
      expect.objectContaining({ method: 'POST' }),
    ])
  })

  it('keeps correction separate from aggregate disposition', async () => {
    await correctClassificationItem('agenda/1', '4SA', '원문 확인')
    await setClassificationDisposition('agenda/2', 'aggregate', '종합지표')

    expect(vi.mocked(fetch).mock.calls[0]).toEqual([
      '/api/knowledge/classification/items/agenda%2F1',
      expect.objectContaining({
        method: 'PATCH',
        body: '{"lotcd":"4SA","reason":"원문 확인"}',
      }),
    ])
    expect(vi.mocked(fetch).mock.calls[1]).toEqual([
      '/api/knowledge/classification/items/agenda%2F2/disposition',
      expect.objectContaining({
        method: 'PATCH',
        body: '{"status":"aggregate","reason":"종합지표"}',
      }),
    ])
  })

  it('serializes split and alias learning with exact backend bodies', async () => {
    const parts = [
      { source_quote: '4SA', summary: '4SA 수율', lotcd: '4SA' },
      { source_quote: '6SA', summary: '6SA 수율', lotcd: '6SA' },
    ]
    await splitClassificationItem('agenda/1', parts, '두 LOT 분리')
    await createClassificationAlias({
      value: 'SP 24G',
      lotcd: '4SA',
      origin_agenda_id: 'agenda/1',
      context_domain: 'DRAM',
      context_tech: 'Spica',
    })

    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[0][1]?.body))).toEqual({
      parts,
      reason: '두 LOT 분리',
    })
    expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body))).toEqual({
      value: 'SP 24G',
      lotcd: '4SA',
      origin_agenda_id: 'agenda/1',
      context_domain: 'DRAM',
      context_tech: 'Spica',
    })
  })

  it('throws useful response text for failed mutations', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response('approval blocked', { status: 409 }),
    )

    await expect(approveClassificationWeek('2026-01')).rejects.toThrow(
      'approval blocked',
    )
  })
})
