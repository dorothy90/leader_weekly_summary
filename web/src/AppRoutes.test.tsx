import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import { AppRoutes } from './AppRoutes'

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  )
}

it('redirects /wiki to the topic index and shows exactly four Wiki modes', async () => {
  renderRoute('/wiki')

  expect(await screen.findByRole('heading', { name: '주제' })).toBeInTheDocument()
  const navigation = screen.getByRole('navigation', { name: 'Wiki 탐색 모드' })
  expect(within(navigation).getAllByRole('link')).toHaveLength(4)
  expect(within(navigation).queryByRole('link', { name: '분류 검토' })).not.toBeInTheDocument()
})

it('loads a topic detail route through the Wiki outlet', async () => {
  renderRoute('/wiki/topics/T-001')

  expect(await screen.findByRole('heading', { name: '주제 상세' })).toBeInTheDocument()
})

it('keeps the review route outside the four-mode navigation', async () => {
  renderRoute('/wiki/reviews')

  expect(await screen.findByRole('heading', { name: '분류 검토' })).toBeInTheDocument()
  const navigation = screen.getByRole('navigation', { name: 'Wiki 탐색 모드' })
  expect(within(navigation).queryByRole('link', { name: '분류 검토' })).not.toBeInTheDocument()
})
