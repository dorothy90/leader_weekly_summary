import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import { createLotcd, updateTech } from '../api/knowledge'
import { TaxonomyEditor } from './TaxonomyEditor'
import type { Taxonomy } from '../types'

vi.mock('../api/knowledge', () => ({
  createLotcd: vi.fn(),
  createTech: vi.fn(),
  deleteTech: vi.fn(),
  updateLotcd: vi.fn(),
  updateTech: vi.fn(),
}))

const taxonomy: Taxonomy = {
  version: 1,
  is_dummy: true,
  notice: 'test',
  group_aliases: [],
  domains: [
    {
      id: 'dram',
      name: 'DRAM',
      techs: [
        { id: 'spica', name: 'Spica', aliases: [], lotcds: [] },
      ],
    },
  ],
}

describe('TaxonomyEditor', () => {
  it('creates a LOTCD with canonical hierarchy and metadata', async () => {
    vi.mocked(createLotcd).mockResolvedValue(taxonomy)
    const onSaved = vi.fn()
    render(
      <TaxonomyEditor
        taxonomy={taxonomy}
        editing={null}
        onSaved={onSaved}
        onCancel={() => undefined}
      />,
    )

    fireEvent.change(screen.getByRole('combobox', { name: 'Tech' }), {
      target: { value: 'Spica' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: 'LOTCD' }), {
      target: { value: '4ZX' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: 'Fab ID' }), {
      target: { value: '4' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: '제품 코드' }), {
      target: { value: 'ZX' },
    })
    fireEvent.change(screen.getByRole('textbox', { name: '제품' }), {
      target: { value: 'DDR5 32G' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'LOTCD 추가' }))

    await waitFor(() => {
      expect(createLotcd).toHaveBeenCalledWith({
        domain: 'DRAM',
        tech: 'Spica',
        code: '4ZX',
        fab_id: '4',
        product_code: 'ZX',
        product: 'DDR5 32G',
        aliases: [],
      })
    })
    expect(onSaved).toHaveBeenCalledWith(taxonomy, 'LOTCD 추가 완료')
  })

  it('updates an existing Tech without changing its stable ID', async () => {
    vi.mocked(updateTech).mockResolvedValue(taxonomy)
    const onSaved = vi.fn()
    render(
      <TaxonomyEditor
        taxonomy={taxonomy}
        editing={null}
        onSaved={onSaved}
        onCancel={() => undefined}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '수정' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Tech 이름' }), {
      target: { value: 'Spica Next' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Tech 변경 저장' }))

    await waitFor(() => {
      expect(updateTech).toHaveBeenCalledWith('spica', {
        name: 'Spica Next',
        aliases: [],
      })
    })
    expect(onSaved).toHaveBeenCalledWith(taxonomy, 'Tech “Spica Next” 변경 완료')
  })
})
