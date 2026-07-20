import { useEffect, useMemo, useRef, useState } from 'react'

import { formatTargetPath, knowledgeAreaLabels, topicStateLabels } from '../TopicList'
import type { WikiCollectionState } from './useWikiCollection'

interface CollectionExplorerProps {
  collection: WikiCollectionState
  selectedTopicId: string | null
  onSelectTopic: (topicId: string) => void
}

export function CollectionExplorer({ collection, selectedTopicId, onSelectTopic }: CollectionExplorerProps) {
  const [query, setQuery] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    const focus = () => searchRef.current?.focus()
    window.addEventListener('weekly-wiki:focus-search', focus)
    return () => window.removeEventListener('weekly-wiki:focus-search', focus)
  }, [])

  useEffect(() => setQuery(''), [collection.path])

  const topics = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase()
    if (!normalized) return collection.topics
    return collection.topics.filter((topic) => [
      topic.title, topic.topic_id, ...topic.teams,
      ...topic.target_paths.flatMap((path) => [path.domain, path.tech ?? '', path.lotcd ?? '']),
    ].some((value) => value.toLocaleLowerCase().includes(normalized)))
  }, [collection.topics, query])

  if (collection.status === 'loading') return <p className="topic-page-status" role="status">Wiki 컬렉션을 불러오는 중입니다.</p>
  if (collection.status === 'error') return <p className="topic-page-status topic-page-status--error" role="alert">Wiki 컬렉션을 불러오지 못했습니다.</p>

  return (
    <div className="collection-explorer">
      <div className="collection-explorer__summary">
        <div><span>{collection.summary}</span><b>{topics.length} / {collection.topics.length} TOPICS</b></div>
        <label>
          <span className="visually-hidden">컬렉션 내 검색</span>
          <input ref={searchRef} type="search" value={query} onChange={(event) => setQuery(event.target.value)} aria-label="컬렉션 내 검색" placeholder="제목, 팀, LOTCD 검색" />
        </label>
      </div>
      {topics.length === 0 ? <p className="topic-list__empty">조건에 맞는 주제가 없습니다.</p> : <ol className="collection-topic-list">
        {topics.map((topic) => <li key={topic.topic_id} className={selectedTopicId === topic.topic_id ? 'is-selected' : ''}>
          <button type="button" onClick={() => onSelectTopic(topic.topic_id)} aria-label={topic.title}>
            <span className="collection-topic-list__rail" />
            <span className="collection-topic-list__body">
              <span className="collection-topic-list__identity">
                <code>{topic.topic_id}</code>
                <i className={`topic-badge topic-badge--${topic.state}`}>{topicStateLabels[topic.state]}</i>
                <i className={`topic-importance topic-importance--${topic.importance}`}>{topic.importance.toUpperCase()}</i>
              </span>
              <strong>{topic.title}</strong>
              <span className="collection-topic-list__meta">
                <span>{knowledgeAreaLabels[topic.primary_area]}</span>
                <span>{topic.target_paths.map(formatTargetPath).join(', ') || '공통'}</span>
                <span>{topic.teams.join(' · ') || '미지정'}</span>
              </span>
            </span>
            <span className="collection-topic-list__facts">
              <time>{topic.last_updated_week}</time>
              <span>근거 {topic.evidence_count}</span>
              <small>{topic.rank_reasons[0] ?? 'canonical revision'}</small>
            </span>
          </button>
        </li>)}
      </ol>}
    </div>
  )
}
