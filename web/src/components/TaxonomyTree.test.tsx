import { render, screen, within } from '@testing-library/react'
import { vi } from 'vitest'

import type { Taxonomy, WikiPageSummary } from '../types'
import { TaxonomyTree } from './TaxonomyTree'

const taxonomy: Taxonomy = {
  version: 1,
  is_dummy: false,
  notice: '',
  group_aliases: [],
  domains: [
    {
      id: 'dram',
      name: 'DRAM',
      techs: [
        {
          id: 'spica',
          name: 'Spica',
          aliases: ['SP'],
          lotcds: [
            {
              code: '4SA',
              fab_id: '4',
              product_code: 'SA',
              product: 'LPDDR5 24G',
              aliases: ['SP LPDDR5 24G'],
            },
            {
              code: '4SB',
              fab_id: '4',
              product_code: 'SB',
              product: 'LPDDR5 16G',
              aliases: [],
            },
          ],
        },
        {
          id: 'orion',
          name: 'Orion',
          aliases: [],
          lotcds: [
            {
              code: '8OA',
              fab_id: '8',
              product_code: 'OA',
              product: 'DDR5 32G',
              aliases: [],
            },
          ],
        },
      ],
    },
    {
      id: 'nand',
      name: 'NAND',
      techs: [
        {
          id: 'v9',
          name: 'V9',
          aliases: [],
          lotcds: [
            {
              code: 'N9A',
              fab_id: 'N',
              product_code: '9A',
              product: 'TLC 1T',
              aliases: [],
            },
          ],
        },
      ],
    },
  ],
}

const wikiPages: WikiPageSummary[] = [
  {
    category_id: 'lotcd:4sa',
    canonical_id: 'dram/spica/4sa',
    level: 'lotcd',
    domain: 'DRAM',
    tech: 'Spica',
    lotcd: '4SA',
    title: '4SA',
    as_of_week: '2026-W28',
    open_issue_count: 3,
    resolved_issue_count: 1,
    confidence: 'high',
    review_item_count: 0,
  },
]

describe('TaxonomyTree', () => {
  it('finds a LOTCD by alias and shows its canonical pending count', () => {
    render(
      <TaxonomyTree
        taxonomy={taxonomy}
        counts={[
          {
            path: { domain: 'DRAM', tech: 'Spica', lotcd: '4SA' },
            direct: 99,
            descendants: 99,
          },
        ]}
        wikiPages={wikiPages}
        query="sp lpddr5 24g"
        selection={{ domain: null, tech: null, lotcd: null }}
        scopeMode="descendants"
        onSelect={vi.fn()}
      />,
    )

    const lotcd = screen.getByRole('button', { name: /4SA/ })
    expect(within(lotcd).getByText('3')).toBeInTheDocument()
    expect(within(lotcd).queryByText('99')).not.toBeInTheDocument()
  })

  it('keeps hierarchy context and excludes nonmatching branches', () => {
    render(
      <TaxonomyTree
        taxonomy={taxonomy}
        counts={[]}
        wikiPages={wikiPages}
        query="SP LPDDR5 24G"
        selection={{ domain: null, tech: null, lotcd: null }}
        scopeMode="descendants"
        onSelect={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: /Spica/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /4SA/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /4SB/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Orion/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /NAND/ })).not.toBeInTheDocument()
  })

  it('uses Explorer agenda counts when wiki pages are omitted', () => {
    render(
      <TaxonomyTree
        taxonomy={taxonomy}
        counts={[
          {
            path: { domain: 'DRAM', tech: 'Spica', lotcd: '4SA' },
            direct: 7,
            descendants: 11,
          },
        ]}
        query="no matching wiki branch"
        selection={{ domain: null, tech: null, lotcd: null }}
        scopeMode="direct"
        onSelect={vi.fn()}
      />,
    )

    const lotcd = screen.getByRole('button', { name: /4SA/ })
    expect(within(lotcd).getByText('7')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Orion/ })).toBeInTheDocument()
  })
})
