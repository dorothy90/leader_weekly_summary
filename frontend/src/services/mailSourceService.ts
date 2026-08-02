import type { MailFolder, SavedMailSource } from '../types'

export interface StorageLike {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
}

export const MAIL_SOURCE_STORAGE_KEY = 'weekly-mail:mail-source'

const DUMMY_FOLDERS: MailFolder[] = [
  {
    id: 'inbox',
    name: 'Inbox',
    path: 'Inbox',
    itemCount: 1248,
    defaultSelected: true,
  },
  {
    id: 'weekly-reports',
    name: 'Weekly Reports',
    path: 'Inbox / Weekly Reports',
    itemCount: 186,
    defaultSelected: true,
  },
  {
    id: 'project-alpha',
    name: 'Project Alpha',
    path: 'Projects / Alpha',
    itemCount: 72,
    defaultSelected: false,
  },
  {
    id: 'team-updates',
    name: 'Team Updates',
    path: 'Inbox / Team Updates',
    itemCount: 94,
    defaultSelected: false,
  },
  {
    id: 'archive-2026',
    name: 'Archive 2026',
    path: 'Archive / 2026',
    itemCount: 324,
    defaultSelected: false,
  },
]

export class DummyMailSourceService {
  constructor(private readonly storage: StorageLike = window.localStorage) {}

  async connect(userId: string, password: string): Promise<MailFolder[]> {
    if (!userId.trim()) {
      throw new Error('사용자 ID를 입력해 주세요.')
    }

    if (!password) {
      throw new Error('비밀번호를 입력해 주세요.')
    }

    return DUMMY_FOLDERS.map((folder) => ({ ...folder }))
  }

  async saveSelection(
    userId: string,
    folderIds: string[],
  ): Promise<SavedMailSource> {
    if (folderIds.length === 0) {
      throw new Error('폴더를 하나 이상 선택해 주세요.')
    }

    const validIds = new Set(DUMMY_FOLDERS.map(({ id }) => id))
    if (folderIds.some((id) => !validIds.has(id))) {
      throw new Error('선택할 수 없는 폴더가 포함되어 있습니다.')
    }

    const saved: SavedMailSource = {
      userId: userId.trim(),
      folderIds: [...new Set(folderIds)],
      savedAt: new Date().toISOString(),
    }

    this.storage.setItem(MAIL_SOURCE_STORAGE_KEY, JSON.stringify(saved))
    return saved
  }

  loadSelection(): SavedMailSource | null {
    const raw = this.storage.getItem(MAIL_SOURCE_STORAGE_KEY)
    return raw ? (JSON.parse(raw) as SavedMailSource) : null
  }
}
