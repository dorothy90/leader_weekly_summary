import { useEffect, useState } from 'react'

import type { RagApiClient } from '../services/ragApiService'

interface ConnectionStatusProps {
  service: Pick<RagApiClient, 'getHealth' | 'getReadiness'>
}

type ConnectionState = 'checking' | 'ready' | 'degraded' | 'offline'

export function ConnectionStatus({ service }: ConnectionStatusProps) {
  const [state, setState] = useState<ConnectionState>('checking')
  const [latency, setLatency] = useState<number | null>(null)

  const refresh = async () => {
    setState('checking')
    const [health, readiness] = await Promise.all([
      service.getHealth(),
      service.getReadiness(),
    ])
    setLatency(Math.max(health.durationMs, readiness.durationMs))
    if (health.error || health.status === 0) {
      setState('offline')
    } else if (readiness.response?.status === 'ready') {
      setState('ready')
    } else {
      setState('degraded')
    }
  }

  useEffect(() => {
    void refresh()
  }, [service])

  return (
    <div className={`connection-status is-${state}`} aria-live="polite">
      <span aria-hidden="true" />
      <strong>API {state}</strong>
      {latency !== null ? <small>{latency} ms</small> : null}
      <button type="button" onClick={() => void refresh()} aria-label="연결 상태 새로고침">
        ↻
      </button>
    </div>
  )
}
