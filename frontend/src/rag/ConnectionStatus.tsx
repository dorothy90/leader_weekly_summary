import { useEffect, useState } from 'react'

import type { RagApiClient } from '../services/ragApiService'

interface ConnectionStatusProps {
  service: Pick<RagApiClient, 'getHealth' | 'getReadiness'>
}

type ConnectionState = 'checking' | 'ready' | 'degraded' | 'offline'

interface ConnectionResult {
  state: Exclude<ConnectionState, 'checking'>
  latency: number
}

async function checkConnection(
  service: ConnectionStatusProps['service'],
): Promise<ConnectionResult> {
  const [health, readiness] = await Promise.all([
    service.getHealth(),
    service.getReadiness(),
  ])

  if (health.error || health.status === 0) {
    return { state: 'offline', latency: Math.max(health.durationMs, readiness.durationMs) }
  }

  return {
    state: readiness.response?.status === 'ready' ? 'ready' : 'degraded',
    latency: Math.max(health.durationMs, readiness.durationMs),
  }
}

export function ConnectionStatus({ service }: ConnectionStatusProps) {
  const [state, setState] = useState<ConnectionState>('checking')
  const [latency, setLatency] = useState<number | null>(null)

  const refresh = async () => {
    setState('checking')
    const result = await checkConnection(service)
    setLatency(result.latency)
    setState(result.state)
  }

  useEffect(() => {
    let ignore = false

    void checkConnection(service).then((result) => {
      if (ignore) return
      setLatency(result.latency)
      setState(result.state)
    })

    return () => {
      ignore = true
    }
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
