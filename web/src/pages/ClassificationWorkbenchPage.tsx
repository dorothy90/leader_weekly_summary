import { useCallback, useEffect, useMemo, useState } from 'react'

import {
  approveClassificationWeek,
  correctClassificationItem,
  createClassificationAlias,
  fetchClassificationItem,
  fetchClassificationItems,
  fetchClassificationWeeks,
  fetchSession,
  fetchTaxonomy,
  runClassificationWeek,
  setClassificationDisposition,
  splitClassificationItem,
} from '../api/knowledge'
import { ClassificationItemDetail } from '../components/ClassificationItemDetail'
import { ClassificationItemList } from '../components/ClassificationItemList'
import { ClassificationWeekNav } from '../components/ClassificationWeekNav'
import type {
  ClassificationAliasRequest,
  ClassificationItem,
  ClassificationSplitPart,
  ClassificationWeek,
  DecisionStatus,
  KnowledgeSession,
  Taxonomy,
} from '../types'

function preferredWeek(weeks: ClassificationWeek[]) {
  const sorted = [...weeks].sort((left, right) => left.week.localeCompare(right.week))
  return sorted.find((week) => week.workflow_state !== 'approved')?.week
    ?? sorted.at(-1)?.week
    ?? ''
}

export function ClassificationWorkbenchPage() {
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null)
  const [session, setSession] = useState<KnowledgeSession | null>(null)
  const [weeks, setWeeks] = useState<ClassificationWeek[]>([])
  const [selectedWeek, setSelectedWeek] = useState('')
  const [selectedLotcd, setSelectedLotcd] = useState('')
  const [selectedStatus, setSelectedStatus] = useState<'' | DecisionStatus>('')
  const [query, setQuery] = useState('')
  const [items, setItems] = useState<ClassificationItem[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<ClassificationItem | null>(null)
  const [reloadKey, setReloadKey] = useState(0)
  const [message, setMessage] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchTaxonomy(controller.signal),
      fetchSession(controller.signal),
      fetchClassificationWeeks(controller.signal),
    ]).then(([taxonomyData, sessionData, weekData]) => {
      setTaxonomy(taxonomyData)
      setSession(sessionData)
      setWeeks(weekData)
      setSelectedWeek(preferredWeek(weekData))
    }).catch((error) => {
      if ((error as Error).name !== 'AbortError') setMessage('분류 작업대를 불러오지 못했습니다.')
    }).finally(() => setLoading(false))
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!selectedWeek) return
    const controller = new AbortController()
    fetchClassificationItems(selectedWeek, {
      lotcd: selectedLotcd || undefined,
      status: selectedStatus || undefined,
      q: query || undefined,
    }, controller.signal).then((response) => {
      setItems(response.items)
      setSelectedId((current) => response.items.some((item) => item.agenda_id === current)
        ? current
        : response.items[0]?.agenda_id ?? null)
    }).catch((error) => {
      if ((error as Error).name !== 'AbortError') setMessage('분류 항목을 불러오지 못했습니다.')
    })
    return () => controller.abort()
  }, [selectedWeek, selectedLotcd, selectedStatus, query, reloadKey])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    const controller = new AbortController()
    fetchClassificationItem(selectedId, controller.signal)
      .then(setDetail)
      .catch((error) => {
        if ((error as Error).name !== 'AbortError') setMessage('항목 상세를 불러오지 못했습니다.')
      })
    return () => controller.abort()
  }, [selectedId, reloadKey])

  const selectedWeekData = weeks.find((week) => week.week === selectedWeek)
  const canEdit = Boolean(session?.can_edit && selectedWeekData &&
    ['review_in_progress', 'ready_for_approval'].includes(selectedWeekData.workflow_state))
  const canRun = Boolean(session?.can_edit && selectedWeekData &&
    ['not_started', 'failed', 'revalidation_required'].includes(selectedWeekData.workflow_state))
  const lotcdCounts = useMemo(() => items.reduce<Record<string, number>>((counts, item) => {
    const lotcd = item.decision.target_path?.lotcd
    if (lotcd) counts[lotcd] = (counts[lotcd] ?? 0) + 1
    return counts
  }, {}), [items])

  const refresh = useCallback(async (selectNextWeek = false) => {
    const nextWeeks = await fetchClassificationWeeks()
    setWeeks(nextWeeks)
    if (selectNextWeek) setSelectedWeek(preferredWeek(nextWeeks))
    setReloadKey((value) => value + 1)
  }, [])

  async function mutate(action: () => Promise<unknown>, success: string, selectNextWeek = false) {
    setMessage(null)
    try {
      await action()
      await refresh(selectNextWeek)
      setMessage(success)
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '요청을 처리하지 못했습니다.')
      throw error
    }
  }

  if (loading) return <main className="classification-workbench classification-workbench--state">분류 작업대를 불러오는 중…</main>
  if (!taxonomy) return <main className="classification-workbench classification-workbench--state" role="alert">{message}</main>

  return (
    <main className="classification-workbench">
      <header className="classification-workbench__header">
        <div><h1>LOTCD Classification Workbench</h1><p>LOTCD를 확인한 뒤 Tech와 Device를 자동으로 결정합니다.</p></div>
        <div className="classification-workbench__actions">
          {canRun ? <button type="button" onClick={() => void mutate(
            () => runClassificationWeek(selectedWeek, selectedWeekData?.workflow_state === 'revalidation_required'),
            '분류를 실행했습니다.',
          )}>{selectedWeekData?.workflow_state === 'revalidation_required' ? '재분류' : '분류 실행'}</button> : null}
          <button type="button" disabled={!session?.can_edit || selectedWeekData?.workflow_state !== 'ready_for_approval'} onClick={() => void mutate(
            () => approveClassificationWeek(selectedWeek), '검수를 완료했습니다.',
            true,
          )}>검수 완료</button>
        </div>
        {message ? <p className="classification-workbench__message" role="status">{message}</p> : null}
      </header>
      <ClassificationWeekNav weeks={weeks} selectedWeek={selectedWeek} lotcdCounts={lotcdCounts}
        selectedLotcd={selectedLotcd} selectedStatus={selectedStatus}
        onWeekChange={(week) => { setSelectedWeek(week); setSelectedId(null) }}
        onLotcdChange={setSelectedLotcd} onStatusChange={setSelectedStatus} />
      <ClassificationItemList items={items} selectedId={selectedId} query={query}
        onQueryChange={setQuery} onSelect={setSelectedId} />
      <section className="classification-workbench__detail">
        {detail ? <ClassificationItemDetail item={detail} taxonomy={taxonomy} canEdit={canEdit}
          onCorrect={(lotcd, reason) => mutate(() => correctClassificationItem(detail.agenda_id, lotcd, reason), '항목을 수정했습니다.')}
          onDisposition={(status, reason) => mutate(() => setClassificationDisposition(detail.agenda_id, status, reason), '항목 상태를 수정했습니다.')}
          onSplit={(parts: ClassificationSplitPart[], reason) => mutate(() => splitClassificationItem(detail.agenda_id, parts, reason), '항목을 분할했습니다.')}
          onCreateAlias={(input: ClassificationAliasRequest) => mutate(async () => {
            await createClassificationAlias(input)
            if (selectedWeekData?.workflow_state !== 'approved') await runClassificationWeek(selectedWeek, true)
          }, '유의어를 등록하고 재분류했습니다.')}
        /> : <p className="classification-workbench__empty">항목을 선택하세요.</p>}
      </section>
    </main>
  )
}
