import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import type { WikiCollectionState } from './useWikiCollection'
import { CollectionExplorer } from './CollectionExplorer'

const collection: WikiCollectionState = {
  kind: 'team', path: '/wiki/teams/Spica수율', title: 'Spica수율', summary: '1개 Topic에 기여',
  status: 'ready', evidence: [], topics: [{
    topic_id: 'DEMO-TOPIC-01', title: '4SA chamber A 편차', state: 'investigating',
    importance: 'critical', primary_area: 'yield_defect',
    target_paths: [{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }], teams: ['Spica수율'],
    last_updated_week: '2026-W30', evidence_count: 2, rank_reasons: ['최근 갱신'],
  }],
}

it('filters compact Topic rows and opens the canonical document', () => {
  const onSelectTopic = vi.fn()
  render(<CollectionExplorer collection={collection} selectedTopicId={null} onSelectTopic={onSelectTopic} />)

  fireEvent.change(screen.getByRole('searchbox', { name: '컬렉션 내 검색' }), { target: { value: 'chamber' } })
  fireEvent.click(screen.getByRole('button', { name: '4SA chamber A 편차' }))

  expect(onSelectTopic).toHaveBeenCalledWith('DEMO-TOPIC-01')
  expect(screen.getByText('DRAM / Spica / 4SA')).toBeInTheDocument()
  expect(screen.getByText('근거 2')).toBeInTheDocument()
})
