import { useMemo, useState } from 'react'

import type { MailFolder } from '../types'

interface FolderSelectionStepProps {
  folders: MailFolder[]
  selectedIds: string[]
  onSelectionChange: (ids: string[]) => void
  onBack: () => void
  onSave: () => Promise<void>
}

export function FolderSelectionStep({
  folders,
  selectedIds,
  onSelectionChange,
  onBack,
  onSave,
}: FolderSelectionStepProps) {
  const [query, setQuery] = useState('')
  const [error, setError] = useState('')
  const [isSaving, setIsSaving] = useState(false)

  const visibleFolders = useMemo(() => {
    const normalizedQuery = query.trim().toLocaleLowerCase()
    if (!normalizedQuery) return folders

    return folders.filter((folder) =>
      `${folder.name} ${folder.path}`
        .toLocaleLowerCase()
        .includes(normalizedQuery),
    )
  }, [folders, query])

  const toggleFolder = (folderId: string) => {
    onSelectionChange(
      selectedIds.includes(folderId)
        ? selectedIds.filter((id) => id !== folderId)
        : [...selectedIds, folderId],
    )
  }

  const handleSave = async () => {
    setError('')
    setIsSaving(true)

    try {
      await onSave()
    } catch (saveError) {
      setError(
        saveError instanceof Error
          ? saveError.message
          : '폴더 설정을 저장할 수 없습니다.',
      )
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <section className="wizard-panel" aria-labelledby="folders-title">
      <div className="panel-heading">
        <span className="eyebrow">Step 2</span>
        <h2 id="folders-title">수집할 폴더 선택</h2>
        <p>선택한 폴더의 메일만 파싱하고 임베딩 대상으로 사용합니다.</p>
      </div>

      <label className="field search-field">
        <span>폴더 검색</span>
        <input
          placeholder="폴더 이름 또는 경로 검색"
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </label>

      <div className="selection-summary">
        <span>Outlook 폴더</span>
        <strong>{selectedIds.length}개 선택됨</strong>
      </div>

      <fieldset className="folder-list">
        <legend className="sr-only">Outlook 폴더</legend>
        {visibleFolders.length > 0 ? (
          visibleFolders.map((folder) => (
            <label className="folder-row" key={folder.id}>
              <input
                checked={selectedIds.includes(folder.id)}
                type="checkbox"
                onChange={() => toggleFolder(folder.id)}
              />
              <span className="folder-glyph" aria-hidden="true">
                {folder.id === 'inbox' ? '↓' : '⌑'}
              </span>
              <span className="folder-copy">
                <strong>{folder.name}</strong>
                <small>{folder.path}</small>
              </span>
              <span className="folder-count">
                {folder.itemCount.toLocaleString('ko-KR')}개
              </span>
            </label>
          ))
        ) : (
          <div className="empty-state">
            <strong>일치하는 폴더가 없습니다.</strong>
            <span>다른 검색어를 입력해 보세요.</span>
          </div>
        )}
      </fieldset>

      {error && (
        <p className="form-message is-error" role="alert">
          {error}
        </p>
      )}

      <div className="form-actions is-split">
        <button className="button button-secondary" type="button" onClick={onBack}>
          이전
        </button>
        <button
          className="button button-primary"
          disabled={selectedIds.length === 0 || isSaving}
          type="button"
          onClick={handleSave}
        >
          {isSaving
            ? '저장하는 중…'
            : `선택한 ${selectedIds.length}개 폴더 저장`}
        </button>
      </div>
    </section>
  )
}
