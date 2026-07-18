import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { vi } from 'vitest'

import { ClassificationEditor } from './ClassificationEditor'
import type { Taxonomy } from '../types'

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
        {
          id: 'spica',
          name: 'Spica',
          aliases: [],
          lotcds: [
            {
              code: '4SA',
              fab_id: '4',
              product_code: 'SA',
              product: 'LPDDR5 24G',
              aliases: [],
            },
          ],
        },
      ],
    },
  ],
}

describe('ClassificationEditor', () => {
  it('submits a canonical category path', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(
      <ClassificationEditor
        taxonomy={taxonomy}
        candidate={{ domain: 'DRAM', tech: null, lotcd: '4ZZ' }}
        initialPaths={[]}
        allowHold
        onConfirm={onConfirm}
        onHold={async () => undefined}
        onCancel={() => undefined}
      />,
    )

    fireEvent.change(screen.getByRole('combobox', { name: 'Tech' }), {
      target: { value: 'Spica' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'LOTCD' }), {
      target: { value: '4SA' },
    })
    fireEvent.click(screen.getByRole('button', { name: '분류 확정' }))

    await waitFor(() => {
      expect(onConfirm).toHaveBeenCalledWith([
        {
          domain: 'DRAM',
          tech: 'Spica',
          lotcd: '4SA',
        },
      ])
    })
  })

  it('stores an unresolved agenda as on hold', async () => {
    const onHold = vi.fn().mockResolvedValue(undefined)
    render(
      <ClassificationEditor
        taxonomy={taxonomy}
        candidate={{ domain: 'DRAM', tech: null, lotcd: '4ZZ' }}
        initialPaths={[]}
        allowHold
        onConfirm={async () => undefined}
        onHold={onHold}
        onCancel={() => undefined}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '보류' }))

    await waitFor(() => expect(onHold).toHaveBeenCalledOnce())
  })

  it('edits a confirmed agenda with multiple paths', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(
      <ClassificationEditor
        taxonomy={taxonomy}
        candidate={null}
        initialPaths={[{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }]}
        allowHold={false}
        onConfirm={onConfirm}
        onHold={async () => undefined}
        onCancel={() => undefined}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'DRAM / Spica / 4SA 제거' }))
    fireEvent.change(screen.getByRole('combobox', { name: 'Tech' }), {
      target: { value: 'Spica' },
    })
    fireEvent.change(screen.getByRole('combobox', { name: 'LOTCD' }), {
      target: { value: '4SA' },
    })
    fireEvent.click(screen.getByRole('button', { name: '경로 추가' }))
    fireEvent.click(screen.getByRole('button', { name: '분류 확정' }))

    await waitFor(() =>
      expect(onConfirm).toHaveBeenCalledWith([
        { domain: 'DRAM', tech: 'Spica', lotcd: '4SA' },
      ]),
    )
  })

  it('submits exactly one LOTCD when single-path mode is enabled', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    render(
      <ClassificationEditor
        taxonomy={taxonomy}
        candidate={null}
        initialPaths={[{ domain: 'DRAM', tech: 'Spica', lotcd: '4SA' }]}
        allowHold={false}
        singleLotcdOnly
        onConfirm={onConfirm}
        onHold={async () => undefined}
        onCancel={() => undefined}
      />,
    )

    expect(screen.queryByRole('button', { name: '경로 추가' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '분류 확정' }))
    await waitFor(() =>
      expect(onConfirm).toHaveBeenCalledWith([
        { domain: 'DRAM', tech: 'Spica', lotcd: '4SA' },
      ]),
    )
  })
})
