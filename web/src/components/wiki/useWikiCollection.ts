import { useEffect, useState } from 'react'

import {
  fetchLotcdWiki,
  fetchTeamWiki,
  fetchTopics,
  fetchWeekWiki,
} from '../../api/knowledge'
import type { TopicListItem, WikiEvidence } from '../../types'
import type { WikiCollectionKind } from './wikiLocation'

export interface WikiCollectionState {
  kind: WikiCollectionKind
  path: string
  title: string
  summary: string
  topics: TopicListItem[]
  evidence: WikiEvidence[]
  status: 'loading' | 'ready' | 'error'
}

const EMPTY_COLLECTION: WikiCollectionState = {
  kind: 'topics', path: '/wiki/topics', title: '전체 주제', summary: '', topics: [], evidence: [], status: 'loading',
}

function uniqueTopics(values: TopicListItem[]): TopicListItem[] {
  return [...new Map(values.map((topic) => [topic.topic_id, topic])).values()]
}

function decodeParts(pathname: string): string[] {
  return pathname.split('/').filter(Boolean).map(decodeURIComponent)
}

export function useWikiCollection(collectionPath: string): WikiCollectionState {
  const [state, setState] = useState<WikiCollectionState>(EMPTY_COLLECTION)

  useEffect(() => {
    const controller = new AbortController()
    const url = new URL(collectionPath, 'http://wiki.local')
    const parts = decodeParts(url.pathname)
    const kind: WikiCollectionKind = parts[1] === 'lotcd' ? 'lotcd'
      : parts[1] === 'teams' ? 'team'
        : parts[1] === 'weeks' ? 'week' : 'topics'
    setState({ ...EMPTY_COLLECTION, kind, path: collectionPath })

    async function load(): Promise<WikiCollectionState> {
      if (kind === 'lotcd' && parts.length >= 5) {
        const view = await fetchLotcdWiki(parts[2] as 'DRAM' | 'NAND', parts[3], parts[4], controller.signal)
        const grouped = Object.values(view.knowledge_areas).flat()
        const closed = Object.values(view.closed_topics).flat()
        return {
          kind, path: collectionPath, title: `${view.lotcd} Wiki`, summary: view.summary,
          topics: uniqueTopics([...view.recent_changes, ...view.active_topics, ...grouped, ...closed]),
          evidence: view.activity, status: 'ready',
        }
      }
      if (kind === 'team' && parts.length >= 3) {
        const view = await fetchTeamWiki(parts[2], controller.signal)
        return {
          kind, path: collectionPath, title: view.team, summary: `${view.topics.length}개 Topic에 기여`,
          topics: view.topics, evidence: view.recent_activity, status: 'ready',
        }
      }
      if (kind === 'week' && parts.length >= 3) {
        const [view, topics] = await Promise.all([
          fetchWeekWiki(parts[2], controller.signal),
          fetchTopics({}, controller.signal),
        ])
        const ids = new Set([
          ...view.new_topic_ids, ...view.changed_topic_ids, ...view.resolved_topic_ids,
          ...view.reopened_topic_ids, ...view.actions_and_decisions.map((topic) => topic.topic_id),
        ])
        return {
          kind, path: collectionPath, title: `${view.week} Wiki`,
          summary: `신규 ${view.new_topic_ids.length} · 변경 ${view.changed_topic_ids.length} · 검토 ${view.pending_assignment_count}`,
          topics: topics.filter((topic) => ids.has(topic.topic_id)), evidence: [], status: 'ready',
        }
      }
      const filters = {
        q: url.searchParams.get('q') || undefined,
        state: url.searchParams.get('state') || undefined,
        area: url.searchParams.get('area') || undefined,
        team: url.searchParams.get('team') || undefined,
        lotcd: url.searchParams.get('lotcd') || undefined,
      }
      const topics = await fetchTopics(filters as Parameters<typeof fetchTopics>[0], controller.signal)
      const title = kind === 'lotcd' ? 'LOTCD를 선택하세요'
        : kind === 'team' ? '팀을 선택하세요'
          : kind === 'week' ? '주차를 선택하세요' : '전체 주제'
      return { kind, path: collectionPath, title, summary: `${topics.length}개 canonical Topic`, topics, evidence: [], status: 'ready' }
    }

    load().then(setState).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      setState((current) => ({ ...current, status: 'error' }))
    })
    return () => controller.abort()
  }, [collectionPath])

  return state
}
