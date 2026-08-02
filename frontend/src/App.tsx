import { useEffect, useMemo, useState } from 'react'

import { CompletionState } from './components/CompletionState'
import { ConnectionStep } from './components/ConnectionStep'
import { FolderSelectionStep } from './components/FolderSelectionStep'
import { ProductNav } from './components/ProductNav'
import { StepIndicator } from './components/StepIndicator'
import { ConnectionStatus } from './rag/ConnectionStatus'
import { RagLabApp } from './rag/RagLabApp'
import { DummyMailSourceService } from './services/mailSourceService'
import { RagApiService, type RagApiClient } from './services/ragApiService'
import type { MailFolder, SavedMailSource } from './types'

type WizardPhase = 'connection' | 'folders' | 'complete'

interface AppProps {
  service?: DummyMailSourceService
  ragService?: RagApiClient
}

interface FolderSetupProps {
  service: DummyMailSourceService
}

function FolderSetupApp({ service }: FolderSetupProps) {
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
  )
}

export default function App({ service: serviceProp, ragService: ragServiceProp }: AppProps) {
  const folderService = useMemo(
    () => serviceProp ?? new DummyMailSourceService(),
    [serviceProp],
  )
  const ragService = useMemo(
    () => ragServiceProp ?? new RagApiService('/api'),
    [ragServiceProp],
  )
  const [path, setPath] = useState(window.location.pathname === '/rag' ? '/rag' : '/')

  useEffect(() => {
    const syncPath = () => setPath(window.location.pathname === '/rag' ? '/rag' : '/')
    window.addEventListener('popstate', syncPath)
    return () => window.removeEventListener('popstate', syncPath)
  }, [])

  const navigate = (nextPath: '/' | '/rag') => {
    window.history.pushState({}, '', nextPath)
    setPath(nextPath)
  }

  return (
    <main className={`app-shell ${path === '/rag' ? 'is-rag' : ''}`}>
      <div className="brand-bar">
        <a className="brand" href="/" aria-label="Weekly Mail 홈" onClick={(event) => {
          event.preventDefault()
          navigate('/')
        }}>
          <span className="brand-mark" aria-hidden="true">W</span>
          <span>Weekly Mail</span>
        </a>
        <ProductNav currentPath={path} onNavigate={navigate} />
        {path === '/rag' ? (
          <ConnectionStatus service={ragService} />
        ) : (
          <span className="demo-status"><span aria-hidden="true" /> 더미 데이터</span>
        )}
      </div>

      {path === '/rag' ? (
        <RagLabApp service={ragService} />
      ) : (
        <FolderSetupApp service={folderService} />
      )}
    </main>
  )
}
