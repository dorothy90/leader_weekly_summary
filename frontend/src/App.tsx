import { useMemo, useState } from 'react'

import { CompletionState } from './components/CompletionState'
import { ConnectionStep } from './components/ConnectionStep'
import { FolderSelectionStep } from './components/FolderSelectionStep'
import { StepIndicator } from './components/StepIndicator'
import { DummyMailSourceService } from './services/mailSourceService'
import type { MailFolder, SavedMailSource } from './types'

type WizardPhase = 'connection' | 'folders' | 'complete'

interface AppProps {
  service?: DummyMailSourceService
}

export default function App({ service: serviceProp }: AppProps) {
  const service = useMemo(
    () => serviceProp ?? new DummyMailSourceService(),
    [serviceProp],
  )
  const [phase, setPhase] = useState<WizardPhase>('connection')
  const [userId, setUserId] = useState('')
  const [folders, setFolders] = useState<MailFolder[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [saved, setSaved] = useState<SavedMailSource | null>(null)

  const connect = async (nextUserId: string, password: string) => {
    const availableFolders = await service.connect(nextUserId, password)
    setUserId(nextUserId.trim())
    setFolders(availableFolders)
    setSelectedIds(
      availableFolders
        .filter((folder) => folder.defaultSelected)
        .map((folder) => folder.id),
    )
    setPhase('folders')
  }

  const save = async () => {
    const nextSaved = await service.saveSelection(userId, selectedIds)
    setSaved(nextSaved)
    setPhase('complete')
  }

  const goBack = () => {
    setFolders([])
    setSelectedIds([])
    setSaved(null)
    setPhase('connection')
  }

  return (
    <main className="app-shell">
      <div className="brand-bar">
        <a className="brand" href="/" aria-label="Weekly Mail 홈">
          <span className="brand-mark" aria-hidden="true">
            W
          </span>
          <span>Weekly Mail</span>
        </a>
        <span className="demo-status">
          <span aria-hidden="true" /> 더미 데이터
        </span>
      </div>

      <div className="wizard-shell">
        {phase !== 'complete' && (
          <StepIndicator currentStep={phase === 'connection' ? 1 : 2} />
        )}

        {phase === 'connection' && (
          <ConnectionStep initialUserId={userId} onConnect={connect} />
        )}
        {phase === 'folders' && (
          <FolderSelectionStep
            folders={folders}
            selectedIds={selectedIds}
            onBack={goBack}
            onSave={save}
            onSelectionChange={setSelectedIds}
          />
        )}
        {phase === 'complete' && saved && (
          <CompletionState
            folders={folders}
            saved={saved}
            userId={userId}
            onEdit={() => setPhase('folders')}
          />
        )}
      </div>
    </main>
  )
}
