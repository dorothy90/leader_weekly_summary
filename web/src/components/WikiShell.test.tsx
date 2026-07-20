import { act, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, vi } from 'vitest'

import { fetchWikiReviews } from '../api/knowledge'
import { WIKI_ASSIGNMENT_REVIEWS_CHANGED } from '../reviewEvents'
import type { WikiReview } from '../types'
import { WikiShell } from './WikiShell'

vi.mock('../api/knowledge', () => ({ fetchWikiReviews: vi.fn() }))

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(fetchWikiReviews).mockResolvedValue([])
})

it('shows the Wiki identity and keeps Classification available', () => {
  render(<MemoryRouter initialEntries={['/wiki/topics']}><WikiShell /></MemoryRouter>)
  expect(screen.getByRole('link', { name: /Weekly Knowledge Wiki/ })).toHaveAttribute('href', '/wiki/topics')
  expect(screen.getByRole('navigation', { name: 'Wiki Library 모드' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '주제' })).toHaveAttribute('aria-current', 'page')
  expect(screen.getByRole('link', { name: 'LOTCD' })).toHaveAttribute('href', '/wiki/lotcd/DRAM')
  expect(screen.getByRole('link', { name: '분류 작업대' })).toHaveAttribute('href', '/classification')
})

it('shows only blocking assignment reviews in the operator badge', async () => {
  vi.mocked(fetchWikiReviews).mockResolvedValue([
    { review_id: 'RV-001', kind: 'assignment', status: 'pending' },
    { review_id: 'RV-002', kind: 'relation', status: 'pending' },
  ] as WikiReview[])

  render(<MemoryRouter initialEntries={['/wiki/topics']}><WikiShell /></MemoryRouter>)

  expect(await screen.findByRole('link', { name: '차단 중인 배정 검토 1건' })).toHaveAttribute('href', '/wiki/reviews')
})

it('refreshes the assignment badge after a successful assignment decision event', async () => {
  vi.mocked(fetchWikiReviews)
    .mockResolvedValueOnce([
      { review_id: 'RV-001', kind: 'assignment', status: 'pending' },
    ] as WikiReview[])
    .mockResolvedValueOnce([])
  render(<MemoryRouter initialEntries={['/wiki/reviews']}><WikiShell /></MemoryRouter>)
  expect(await screen.findByRole('link', { name: '차단 중인 배정 검토 1건' })).toBeInTheDocument()

  act(() => { window.dispatchEvent(new Event(WIKI_ASSIGNMENT_REVIEWS_CHANGED)) })

  await waitFor(() => {
    expect(fetchWikiReviews).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('link', { name: /차단 중인 배정 검토/ })).not.toBeInTheDocument()
  })
})
