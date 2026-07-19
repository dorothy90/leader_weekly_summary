import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, vi } from 'vitest'

import { fetchWikiReviews } from '../api/knowledge'
import type { WikiReview } from '../types'
import { WikiShell } from './WikiShell'

vi.mock('../api/knowledge', () => ({ fetchWikiReviews: vi.fn() }))

beforeEach(() => {
  vi.mocked(fetchWikiReviews).mockResolvedValue([])
})

it('shows four Wiki modes and keeps Classification available', () => {
  render(<MemoryRouter initialEntries={['/wiki/topics']}><WikiShell /></MemoryRouter>)
  expect(screen.getByRole('link', { name: '주제' })).toHaveAttribute('href', '/wiki/topics')
  expect(screen.getByRole('link', { name: 'LOTCD' })).toHaveAttribute('href', '/wiki/lotcd')
  expect(screen.getByRole('link', { name: '팀' })).toHaveAttribute('href', '/wiki/teams')
  expect(screen.getByRole('link', { name: '주차' })).toHaveAttribute('href', '/wiki/weeks')
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
