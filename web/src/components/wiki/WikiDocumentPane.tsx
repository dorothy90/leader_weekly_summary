import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { fetchTopic } from '../../api/knowledge'
import type { WikiEvidence, WikiTopicDetail } from '../../types'
import { EvidenceDrawer } from '../EvidenceDrawer'
import { formatTargetPath, knowledgeAreaLabels, topicStateLabels } from '../TopicList'
import { EvidenceTree } from './EvidenceTree'
import type { WikiCollectionState } from './useWikiCollection'

const relationLabels = {
  possible_cause: '가능 원인', affects: '영향', measurement_effect: '측정 영향', comparison: '비교',
  follow_up: '후속', supports: '지지', contradicts: '상충', shares_condition: '조건 공유',
} as const
const citationPattern = /(\[agenda:([^\]\s]+)\])/g

interface WikiDocumentPaneProps {
  topicId: string | null
  collection: WikiCollectionState
  onSelectTopic: (topicId: string) => void
}

export function WikiDocumentPane({ topicId, collection, onSelectTopic }: WikiDocumentPaneProps) {
  const [detail, setDetail] = useState<WikiTopicDetail | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'error'>('idle')
  const [selectedEvidence, setSelectedEvidence] = useState<WikiEvidence | null>(null)
  const evidenceTriggerRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!topicId) {
      setDetail(null); setStatus('idle'); setSelectedEvidence(null)
      return
    }
    const controller = new AbortController()
    setStatus('loading'); setDetail(null); setSelectedEvidence(null)
    fetchTopic(topicId, controller.signal).then((value) => {
      setDetail(value); setStatus('idle')
    }).catch((error: unknown) => {
      if (error instanceof DOMException && error.name === 'AbortError') return
      setStatus('error')
    })
    return () => controller.abort()
  }, [topicId])

  function openEvidence(evidence: WikiEvidence, trigger: HTMLButtonElement) {
    evidenceTriggerRef.current = trigger
    setSelectedEvidence(evidence)
  }

  if (!topicId) return <CollectionOverview collection={collection} />
  if (status === 'loading') return <p className="topic-page-status" role="status">Topic 문서를 불러오는 중입니다.</p>
  if (status === 'error' || !detail) return <p className="topic-page-status topic-page-status--error" role="alert">Topic 문서를 불러오지 못했습니다.</p>

  const { topic } = detail
  const evidenceById = new Map(detail.evidence.map((item) => [item.agenda_id, item]))
  function renderBody(body: string): ReactNode[] {
    return body.split(citationPattern).reduce<ReactNode[]>((nodes, value, index, parts) => {
      if (index % 3 === 2) return nodes
      if (index % 3 === 1) {
        const agendaId = parts[index + 1]
        const evidence = evidenceById.get(agendaId)
        nodes.push(evidence ? <button key={`${agendaId}-${index}`} type="button" className="document-citation" aria-label={`Agenda 근거 ${agendaId} 보기`} onClick={(event) => openEvidence(evidence, event.currentTarget)}>{agendaId}</button> : value)
      } else if (value) nodes.push(value)
      return nodes
    }, [])
  }

  return (
    <article className="canonical-document" aria-label="Wiki 문서">
      <header className="canonical-document__header">
        <div className="canonical-document__id"><span>{topic.topic_id}</span><span>REV {topic.current_revision_id}</span></div>
        <h1>{topic.title}</h1>
        <div className="canonical-document__badges">
          <span className={`topic-badge topic-badge--${topic.state}`}>{topicStateLabels[topic.state]}</span>
          <span className={`topic-importance topic-importance--${topic.importance}`}>{topic.importance.toUpperCase()}</span>
          <span>{knowledgeAreaLabels[topic.primary_area]}</span>
        </div>
        <dl>
          <div><dt>관찰</dt><dd>{topic.first_seen_week} → {topic.last_updated_week}</dd></div>
          <div><dt>경로</dt><dd>{topic.target_paths.map(formatTargetPath).join(', ')}</dd></div>
          <div><dt>기여 팀</dt><dd>{topic.teams.join(' · ')}</dd></div>
        </dl>
      </header>
      <nav className="canonical-document__toc" aria-label="문서 목차">
        <strong>ON THIS PAGE</strong>
        {detail.sections.map((section, index) => <a key={`${section.key}-${index}`} href={`#section-${index}`}>{section.title}</a>)}
        <a href="#document-relations">연결된 주제</a><a href="#document-evidence">출처 기록</a>
      </nav>
      <div className="canonical-document__spine">
        {detail.sections.map((section, index) => <section key={`${section.key}-${index}`} id={`section-${index}`} className="canonical-document__section">
          <span className="canonical-document__node">{String(index + 1).padStart(2, '0')}</span>
          <h2>{section.title}</h2><p>{renderBody(section.body)}</p>
        </section>)}
      </div>
      {detail.claims.length > 0 ? <section className="canonical-document__claims"><h2>근거가 있는 주장</h2>
        {detail.claims.map((claim) => <div key={claim.text}><p>{claim.text}</p><span>{claim.agenda_ids.map((agendaId) => {
          const evidence = evidenceById.get(agendaId)
          return evidence ? <button key={agendaId} type="button" aria-label={`Agenda 근거 ${agendaId} 보기`} onClick={(event) => openEvidence(evidence, event.currentTarget)}>{agendaId}</button> : null
        })}</span></div>)}
      </section> : null}
      <section id="document-relations" className="canonical-document__relations">
        <h2>연결된 주제</h2>
        {detail.relations.length === 0 ? <p>승인되거나 검토 중인 관계가 없습니다.</p> : <ul>{detail.relations.map((relation) => {
          const otherId = relation.source_topic_id === topic.topic_id ? relation.target_topic_id : relation.source_topic_id
          return <li key={relation.relation_id} className={`is-${relation.review_state}`}>
            <button type="button" onClick={() => onSelectTopic(otherId)}>{otherId}</button>
            <span>{relationLabels[relation.kind]}</span><small>{Math.round(relation.confidence * 100)}% · {relation.review_state}</small>
          </li>
        })}</ul>}
      </section>
      <section id="document-evidence" className="canonical-document__evidence"><h2>출처 기록</h2><EvidenceTree evidence={detail.evidence} onOpen={openEvidence} /></section>
      {selectedEvidence ? <EvidenceDrawer evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} triggerRef={evidenceTriggerRef} /> : null}
    </article>
  )
}

function CollectionOverview({ collection }: { collection: WikiCollectionState }) {
  const stateCount = new Set(collection.topics.map((topic) => topic.state)).size
  return <article className="wiki-document-pane__overview" aria-label="Wiki 문서">
    <small>COLLECTION OVERVIEW</small><h1>{collection.title}</h1>
    <p>{collection.summary || '좌측 트리에서 분류 축을 선택하거나 중앙에서 Topic을 선택하세요.'}</p>
    <dl><div><dt>표시 Topic</dt><dd>{collection.topics.length}</dd></div><div><dt>활성 상태</dt><dd>{stateCount}</dd></div></dl>
    {collection.topics.length > 0 ? <section className="collection-overview__recent"><h2>최근 갱신</h2>{collection.topics.slice(0, 5).map((topic) => <p key={topic.topic_id}><code>{topic.last_updated_week}</code>{topic.title}</p>)}</section> : null}
  </article>
}
