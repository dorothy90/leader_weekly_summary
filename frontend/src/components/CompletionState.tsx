import type { MailFolder, SavedMailSource } from '../types'

interface CompletionStateProps {
  userId: string
  folders: MailFolder[]
  saved: SavedMailSource
  onEdit: () => void
}

export function CompletionState({
  userId,
  folders,
  saved,
  onEdit,
}: CompletionStateProps) {
  const selectedFolders = folders.filter((folder) =>
    saved.folderIds.includes(folder.id),
  )

  return (
    <section className="wizard-panel completion-panel" aria-labelledby="complete-title">
      <div className="completion-mark" aria-hidden="true">
        ✓
      </div>
      <div className="panel-heading">
        <span className="eyebrow">Setup complete</span>
        <h2 id="complete-title">설정이 저장되었습니다</h2>
        <p>
          <strong>{userId}</strong> 계정에서 선택한 폴더를 RAG 데이터 소스로
          사용할 준비가 되었습니다.
        </p>
      </div>

      <div className="saved-folders" role="status" aria-live="polite">
        <div className="saved-folders-heading">
          <span>저장된 폴더</span>
          <strong>{selectedFolders.length}개</strong>
        </div>
        <ul>
          {selectedFolders.map((folder) => (
            <li key={folder.id}>
              <span>{folder.name}</span>
              <small>{folder.path}</small>
            </li>
          ))}
        </ul>
      </div>

      <div className="form-actions is-end">
        <button className="button button-secondary" type="button" onClick={onEdit}>
          폴더 선택 수정
        </button>
      </div>
    </section>
  )
}
