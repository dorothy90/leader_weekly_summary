import { describe, expect, it } from 'vitest'

import {
  DummyMailSourceService,
  type StorageLike,
} from './mailSourceService'

const memoryStorage = (): StorageLike => {
  const values = new Map<string, string>()

  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  }
}

describe('DummyMailSourceService', () => {
  it('returns stable Outlook folders and rejects missing credentials', async () => {
    const service = new DummyMailSourceService(memoryStorage())

    await expect(service.connect('', 'password')).rejects.toThrow(
      '사용자 ID를 입력해 주세요.',
    )
    await expect(service.connect('user@company.com', '')).rejects.toThrow(
      '비밀번호를 입력해 주세요.',
    )
    const folders = await service.connect(
      'user@company.com',
      'dummy-password',
    )

    expect(folders).toHaveLength(5)
    expect(folders).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          id: 'inbox',
          name: 'Inbox',
          defaultSelected: true,
        }),
        expect.objectContaining({
          id: 'weekly-reports',
          name: 'Weekly Reports',
          defaultSelected: true,
        }),
      ]),
    )
  })

  it('stores only valid folder selections and never stores a password', async () => {
    const storage = memoryStorage()
    const service = new DummyMailSourceService(storage)

    await service.connect('user@company.com', 'super-secret')
    const saved = await service.saveSelection('user@company.com', [
      'inbox',
      'weekly-reports',
    ])

    expect(saved.folderIds).toEqual(['inbox', 'weekly-reports'])
    expect(storage.getItem('weekly-mail:mail-source')).not.toContain(
      'super-secret',
    )
    await expect(
      service.saveSelection('user@company.com', []),
    ).rejects.toThrow('폴더를 하나 이상 선택해 주세요.')
    await expect(
      service.saveSelection('user@company.com', ['unknown']),
    ).rejects.toThrow('선택할 수 없는 폴더가 포함되어 있습니다.')
  })
})
