import { useEffect, useState } from 'react'

import { fetchWikiBuild } from '../api/knowledge'
import type { WikiBuildRun } from '../types'

const statusLabels: Record<WikiBuildRun['status'], string> = {
  linking: 'Topic 연결 중',
  review_required: '배정 검토 필요',
  generating: 'Topic 생성 중',
  validating: '검증 중',
  published: '게시 완료',
  partially_failed: '부분 실패',
  failed: '빌드 실패',
}

const transientStatuses = new Set<WikiBuildRun['status']>([
  'linking', 'generating', 'validating',
])

export function BuildStatusBanner({
  run,
  onRunChange,
}: {
  run: WikiBuildRun
  onRunChange?: (run: WikiBuildRun) => void
}) {
  const [currentRun, setCurrentRun] = useState(run)
  const [pollAttempt, setPollAttempt] = useState(0)

  useEffect(() => {
    if (!transientStatuses.has(currentRun.status)) return
    const controller = new AbortController()
    const timeout = window.setTimeout(() => {
      fetchWikiBuild(currentRun.run_id, controller.signal).then((nextRun) => {
        setCurrentRun(nextRun)
        onRunChange?.(nextRun)
      }).catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === 'AbortError')) {
          setPollAttempt((value) => value + 1)
        }
      })
    }, 2000)

    return () => {
      window.clearTimeout(timeout)
      controller.abort()
    }
  }, [currentRun, onRunChange, pollAttempt])

  return (
    <section className={`build-status build-status--${currentRun.status}`} role="status" aria-live="polite">
      <div>
        <span className="build-status__label">{statusLabels[currentRun.status]}</span>
        <strong>{currentRun.week}</strong>
        <span>{currentRun.run_id}</span>
      </div>
      {currentRun.status === 'review_required' ? (
        <p>미해결 Agenda 배정을 검토한 뒤 빌드를 다시 실행하세요.</p>
      ) : null}
      {currentRun.status === 'partially_failed' ? (
        <p>
          일부 Topic은 이전 정상 버전을 표시합니다.
          <span> 실패 Topic: {currentRun.failed_topic_ids.join(', ') || '기록 없음'}</span>
        </p>
      ) : null}
      {currentRun.status === 'failed' ? (
        <p>{currentRun.error || '빌드를 완료하지 못했습니다.'}</p>
      ) : null}
    </section>
  )
}
