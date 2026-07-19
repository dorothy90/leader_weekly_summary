import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { useLocation, useSearchParams } from 'react-router-dom'

import { fetchTopics } from '../api/knowledge'
import { knowledgeAreaLabels, TopicList, topicStateLabels } from '../components/TopicList'
import type { KnowledgeArea, TopicListItem, TopicState } from '../types'

const topicStates = Object.keys(topicStateLabels) as TopicState[]
const knowledgeAreas = Object.keys(knowledgeAreaLabels) as KnowledgeArea[]

export function TopicIndexPage() {
  const location = useLocation()
  const [searchParams, setSearchParams] = useSearchParams()
  const [topics, setTopics] = useState<TopicListItem[]>([])
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const search = searchParams.toString()
  const filters = {
    q: searchParams.get('q') || undefined,
    state: (searchParams.get('state') || undefined) as TopicState | undefined,
    area: (searchParams.get('area') || undefined) as KnowledgeArea | undefined,
    team: searchParams.get('team') || undefined,
    lotcd: searchParams.get('lotcd') || undefined,
  }

  useEffect(() => {
    const controller = new AbortController()
    setStatus('loading')
    fetchTopics(filters, controller.signal)
      .then((items) => {
        setTopics(items)
        setStatus('ready')
      })
      .catch((error: unknown) => {
        if (error instanceof DOMException && error.name === 'AbortError') return
        setStatus('error')
      })
    return () => controller.abort()
  }, [search])

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const data = new FormData(event.currentTarget)
    const next = new URLSearchParams()
    for (const key of ['q', 'state', 'area', 'team', 'lotcd']) {
      const value = data.get(key)?.toString().trim()
      if (value) next.set(key, value)
    }
    setSearchParams(next)
  }

  return (
    <section className="topic-index">
      <header className="topic-index__header">
        <div>
          <span className="topic-index__eyebrow">CANONICAL TOPIC REGISTER</span>
          <h1>주제 색인</h1>
          <p>승인된 최신 리비전과 Agenda 근거를 기준으로 탐색합니다.</p>
        </div>
        <span className="topic-index__count">{status === 'ready' ? `${topics.length} TOPICS` : 'QUERYING'}</span>
      </header>

      <form className="topic-filters" onSubmit={applyFilters} key={search}>
        <label className="topic-filters__search">
          <span>검색어</span>
          <input name="q" type="search" defaultValue={filters.q} placeholder="제목 또는 기술 키워드" />
        </label>
        <label><span>상태</span><select name="state" defaultValue={filters.state ?? ''}>
          <option value="">전체</option>
          {topicStates.map((state) => <option key={state} value={state}>{topicStateLabels[state]}</option>)}
        </select></label>
        <label><span>지식 영역</span><select name="area" defaultValue={filters.area ?? ''}>
          <option value="">전체</option>
          {knowledgeAreas.map((area) => <option key={area} value={area}>{knowledgeAreaLabels[area]}</option>)}
        </select></label>
        <label><span>기여 팀</span><input name="team" defaultValue={filters.team} placeholder="팀명" /></label>
        <label><span>LOTCD</span><input name="lotcd" defaultValue={filters.lotcd} placeholder="예: 4SA" /></label>
        <button type="submit">필터 적용</button>
      </form>

      {status === 'loading' ? <p className="topic-page-status" role="status">주제를 조회하는 중입니다.</p> : null}
      {status === 'error' ? <p className="topic-page-status topic-page-status--error" role="alert">주제 목록을 불러오지 못했습니다.</p> : null}
      {status === 'ready' ? <TopicList topics={topics} from={location.pathname + location.search} /> : null}
    </section>
  )
}
