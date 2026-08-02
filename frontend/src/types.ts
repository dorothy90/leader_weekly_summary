export interface MailFolder {
  id: string
  name: string
  path: string
  itemCount: number
  defaultSelected: boolean
}

export interface SavedMailSource {
  userId: string
  folderIds: string[]
  savedAt: string
}
