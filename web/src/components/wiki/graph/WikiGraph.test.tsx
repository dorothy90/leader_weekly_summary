import { render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { fetchWikiGraph } from '../../../api/knowledge'
import { WikiGraph } from './WikiGraph'

vi.mock('../../../api/knowledge', () => ({ fetchWikiGraph: vi.fn() }))

it('shows graph controls and a truthful empty state', async () => {
  vi.mocked(fetchWikiGraph).mockResolvedValue({ topics: [], relations: [] })
  render(<WikiGraph scopeIds={new Set()} selectedTopicId={null} onSelectTopic={vi.fn()} />)

  expect(await screen.findByRole('heading', { name: 'Wiki Graph' })).toBeInTheDocument()
  expect(screen.getByText('0 PAGES')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '확대' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '화면 맞춤' })).toBeInTheDocument()
  expect(screen.getByText('표시할 Topic이 없습니다.')).toBeInTheDocument()
})
