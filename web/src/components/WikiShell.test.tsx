import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { WikiShell } from './WikiShell'

it('shows four Wiki modes and keeps Classification available', () => {
  render(<MemoryRouter initialEntries={['/wiki/topics']}><WikiShell /></MemoryRouter>)
  expect(screen.getByRole('link', { name: '주제' })).toHaveAttribute('href', '/wiki/topics')
  expect(screen.getByRole('link', { name: 'LOTCD' })).toHaveAttribute('href', '/wiki/lotcd')
  expect(screen.getByRole('link', { name: '팀' })).toHaveAttribute('href', '/wiki/teams')
  expect(screen.getByRole('link', { name: '주차' })).toHaveAttribute('href', '/wiki/weeks')
  expect(screen.getByRole('link', { name: '분류 작업대' })).toHaveAttribute('href', '/classification')
})
