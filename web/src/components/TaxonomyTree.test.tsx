import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

import type { Taxonomy } from '../types'
import { TaxonomyTree } from './TaxonomyTree'

const taxonomy: Taxonomy = {
  version: 7,
  is_dummy: false,
  notice: '',
  domains: [{
    id: 'dram',
    name: 'DRAM',
    techs: [{
      id: 'spica',
      name: 'Spica X',
      aliases: [],
      lotcds: [{
        code: '4SA',
        fab_id: 'F4',
        product_code: '4SA',
        product: '4SA Product',
        aliases: [],
      }],
    }],
  }],
  group_aliases: [],
}

it('expands Domain and Tech locally and links LOTCD leaves', () => {
  render(<MemoryRouter><TaxonomyTree taxonomy={taxonomy} /></MemoryRouter>)

  const domainButton = screen.getByRole('button', { name: 'DRAM 펼치기' })
  expect(screen.queryByRole('button', { name: 'Spica X 펼치기' })).not.toBeInTheDocument()

  fireEvent.click(domainButton)
  const techButton = screen.getByRole('button', { name: 'Spica X 펼치기' })
  expect(domainButton).toHaveAttribute('aria-expanded', 'true')

  fireEvent.click(techButton)
  expect(screen.getByRole('link', { name: /4SA/ })).toHaveAttribute(
    'href',
    '/wiki/lotcd/DRAM/Spica%20X/4SA',
  )
})

it('opens the selected path and marks its LOTCD leaf as current', () => {
  render(
    <MemoryRouter>
      <TaxonomyTree taxonomy={taxonomy} currentPath={{ domain: 'DRAM', tech: 'Spica X', lotcd: '4SA' }} />
    </MemoryRouter>,
  )

  expect(screen.getByRole('button', { name: 'DRAM 접기' })).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByRole('button', { name: 'Spica X 접기' })).toHaveAttribute('aria-expanded', 'true')
  expect(screen.getByRole('link', { name: /4SA/ })).toHaveAttribute('aria-current', 'page')
})
