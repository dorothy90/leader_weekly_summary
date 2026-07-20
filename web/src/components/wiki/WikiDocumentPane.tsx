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

function renderCitedText(
  body: string,
  evidence: WikiEvidence[],
  onOpen: (evidence: WikiEvidence, trigger: HTMLButtonElement) => void,
): ReactNode[] {
  const evidenceById = new Map(evidence.map((item, index) => [item.agenda_id, { item, index }]))
  return body.split(citationPattern).reduce<ReactNode[]>((nodes, value, index, parts) => {
    if (index % 3 === 2) return nodes
    if (index % 3 === 1) {
      const agendaId = parts[index + 1]
      const match = evidenceById.get(agendaId)
      nodes.push(match ? <button
        key={`${agendaId}-${index}`}
        type="button"
        className="document-citation"
        aria-label={`참고문서 ${agendaId} 상세 보기`}
        onClick={(event) => onOpen(match.item, event.currentTarget)}
      >[{match.index + 1}]</button> : value)
    } else if (value) nodes.push(value)
    return nodes
  }, [])
}

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
        nodes.push(evidence ? <a key={`${agendaId}-${index}`} className="document-citation" aria-label={`참고문서 ${agendaId}로 이동`} href={`#reference-${agendaId}`}>{agendaId}</a> : value)
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
        <a href="#document-relations">연결된 주제</a><a href="#document-evidence">참고문서</a>
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
          return evidence ? <a key={agendaId} className="document-citation" aria-label={`참고문서 ${agendaId}로 이동`} href={`#reference-${agendaId}`}>{agendaId}</a> : null
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
      <section id="document-evidence" className="canonical-document__evidence"><h2>참고문서</h2><EvidenceTree evidence={detail.evidence} onOpen={openEvidence} /></section>
      {selectedEvidence ? <EvidenceDrawer evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} triggerRef={evidenceTriggerRef} /> : null}
    </article>
  )
}

const collectionKindLabels: Record<WikiCollectionState['kind'], string> = {
  topics: 'TOPIC INDEX', lotcd: 'LOTCD WIKI', team: 'TEAM WIKI', week: 'WEEKLY WIKI',
}

function CollectionOverview({
  collection,
}: {
  collection: WikiCollectionState
}) {
  const [selectedEvidence, setSelectedEvidence] = useState<WikiEvidence | null>(null)
  const evidenceTriggerRef = useRef<HTMLButtonElement | null>(null)

  if (collection.status === 'loading') return <article className="collection-document collection-document--status" aria-label="Wiki 문서"><p className="topic-page-status" role="status">Wiki 문서를 합성하는 중입니다.</p></article>
  if (collection.status === 'error') return <article className="collection-document collection-document--status" aria-label="Wiki 문서"><p className="topic-page-status topic-page-status--error" role="alert">Wiki 문서를 불러오지 못했습니다.</p></article>

  const stateCount = new Set(collection.topics.map((topic) => topic.state)).size
  const teams = new Set(collection.topics.flatMap((topic) => topic.teams)).size
  const areas = new Map<string, WikiCollectionState['topics']>()
  collection.topics.forEach((topic) => areas.set(topic.primary_area, [...(areas.get(topic.primary_area) ?? []), topic]))
  const documents = collection.documents ?? []
  const projection = collection.projection ?? null
  const allEvidence = [...new Map([
    ...documents.flatMap((document) => document.evidence),
    ...collection.evidence,
  ].map((item) => [item.agenda_id, item])).values()]
  const categorizedEvidenceIds = new Set([
    ...collection.directEvidence,
    ...collection.rolledUpEvidence,
  ].map((item) => item.agenda_id))
  const additionalEvidence = allEvidence.filter((item) => !categorizedEvidenceIds.has(item.agenda_id))

  const backlinkEntries: Array<readonly [string, string]> = []
  for (const { topic, evidence } of documents) {
    for (const path of topic.target_paths) {
      const parts = [path.domain, path.tech, path.lotcd].filter((part): part is string => Boolean(part))
      backlinkEntries.push([`/wiki/lotcd/${parts.map(encodeURIComponent).join('/')}`, parts.join(' / ')])
    }
    for (const team of topic.teams) backlinkEntries.push([`/wiki/teams/${encodeURIComponent(team)}`, `${team} 팀 Wiki`])
    for (const item of evidence) backlinkEntries.push([`/wiki/weeks/${encodeURIComponent(item.week)}`, `${item.week} 주차 Wiki`])
  }
  const currentPath = decodeURIComponent(collection.path.split('?')[0])
  const backlinks = [...new Map(backlinkEntries
    .filter(([href]) => decodeURIComponent(href) !== currentPath)
    .map(([href, label]) => [href, { href, label }])).values()]

  function openEvidence(evidence: WikiEvidence, trigger: HTMLButtonElement) {
    evidenceTriggerRef.current = trigger
    setSelectedEvidence(evidence)
  }

  return <article className="collection-document" aria-label="Wiki 문서">
    <header className="collection-document__header">
      <small>{collectionKindLabels[collection.kind]} · SYNTHESIZED DOCUMENT</small>
      {collection.breadcrumb.length ? <p className="collection-document__breadcrumb">{collection.breadcrumb.join(' / ')}</p> : null}
      <h1>{collection.title}</h1>
      <p>{projection?.summary || collection.summary || '좌측 지식 트리에서 문서를 선택하세요.'}</p>
      <dl>
        <div><dt>연결 Topic</dt><dd>{collection.topics.length}</dd></div>
        <div><dt>기여 팀</dt><dd>{teams}</dd></div>
        {projection ? <><div><dt>누적 기준</dt><dd>{projection.as_of_week}</dd></div><div><dt>Revision</dt><dd>{projection.revision_id}</dd></div></>
          : <><div><dt>지식 영역</dt><dd>{areas.size}</dd></div><div><dt>활성 상태</dt><dd>{stateCount}</dd></div></>}
      </dl>
    </header>
    <nav className="canonical-document__toc" aria-label="문서 목차">
      <strong>ON THIS PAGE</strong>
      {projection ? <>
        {projection.sections.map((section, index) => <a key={`${section.key}-${index}`} href={`#projection-section-${index}`}>{section.title}</a>)}
        <a href="#collection-history">주차별 업데이트 이력</a>
      </> : <>
        <a href="#collection-summary">현황 요약</a>
        {documents.map((document) => <a key={document.topic.topic_id} href={`#topic-${document.topic.topic_id}`}>{document.topic.title}</a>)}
        <a href="#collection-areas">지식 영역</a>
      </>}
      <a href="#collection-evidence">참고문서</a>
      <a href="#collection-backlinks">Backlinks</a>
    </nav>
    <div className="canonical-document__spine">
      {projection ? <>
        {projection.sections.map((section, index) => <section key={`${section.key}-${index}`} id={`projection-section-${index}`} className="canonical-document__section collection-document__projection-section">
          <span className="canonical-document__node">{String(index + 1).padStart(2, '0')}</span>
          <h2>{section.title}</h2>
          <p>{renderCitedText(section.body, allEvidence, openEvidence)}</p>
        </section>)}
        <section id="collection-history" className="canonical-document__section collection-document__history">
          <span className="canonical-document__node">{String(projection.sections.length + 1).padStart(2, '0')}</span>
          <h2>주차별 업데이트 이력</h2>
          {projection.weekly_history.length ? projection.weekly_history.slice().reverse().map((entry, index) => <details key={entry.week} open={index === 0}>
            <summary>{entry.week}</summary>
            <p>{renderCitedText(entry.body, allEvidence, openEvidence)}</p>
          </details>) : <p>누적된 주차별 변경 이력이 없습니다.</p>}
        </section>
      </> : <>
      <section id="collection-summary" className="canonical-document__section">
        <span className="canonical-document__node">01</span><h2>현황 요약</h2>
        <p>{collection.summary || `${collection.topics.length}개 Topic을 하나의 Wiki 문서로 합성했습니다.`}</p>
      </section>
      <section id="collection-topics" className="canonical-document__section collection-document__topics">
        <span className="canonical-document__node">02</span><h2>주요 주제</h2>
        {documents.length === 0 ? <p>합성할 Topic 본문이 없습니다.</p> : documents.map((document) => {
          const topic = document.topic
          return <section key={topic.topic_id} id={`topic-${topic.topic_id}`} className="collection-topic-section">
            <header>
              <span><code>{topic.topic_id}</code><time>{topic.last_updated_week}</time></span>
              <h3>{topic.title}</h3>
              <small>{topicStateLabels[topic.state]} · {knowledgeAreaLabels[topic.primary_area]} · {topic.teams.join(' · ') || '팀 미지정'}</small>
            </header>
            {document.sections.map((section, index) => <section key={`${section.key}-${index}`}>
              <h4>{section.title}</h4>
              <p>{renderCitedText(section.body, document.evidence, openEvidence)}</p>
            </section>)}
            {!document.sections.length && document.body_markdown ? <p>{renderCitedText(document.body_markdown, document.evidence, openEvidence)}</p> : null}
            {document.claims.length ? <div className="collection-topic-section__claims">
              <h4>근거가 있는 주장</h4>
              {document.claims.map((claim) => <p key={claim.text}>{claim.text} {claim.agenda_ids.map((agendaId) => {
                const item = document.evidence.find((value) => value.agenda_id === agendaId)
                return item ? <button key={agendaId} type="button" className="document-citation" aria-label={`참고문서 ${agendaId} 상세 보기`} onClick={(event) => openEvidence(item, event.currentTarget)}>[{document.evidence.indexOf(item) + 1}]</button> : null
              })}</p>)}
            </div> : null}
          </section>
        })}
      </section>
      <section id="collection-areas" className="canonical-document__section collection-document__areas">
        <span className="canonical-document__node">03</span><h2>지식 영역</h2>
        {areas.size === 0 ? <p>분류된 지식 영역이 없습니다.</p> : <div>{[...areas.entries()].map(([area, topics]) => <section key={area}>
          <h3>{knowledgeAreaLabels[area as keyof typeof knowledgeAreaLabels]}</h3>
          <p>{topics.map((topic) => topic.title).join(' · ')}</p>
        </section>)}</div>}
      </section>
      </>}
      <section id="collection-evidence" className="canonical-document__section collection-document__evidence">
        <span className="canonical-document__node">04</span><h2>참고문서</h2>
        {collection.scopeLevel ? <>
          <div className="collection-document__reference-group">
            <h3>직접 분류된 참고문서 <span>{collection.directEvidence.length}</span></h3>
            {collection.directEvidence.length ? <EvidenceTree evidence={collection.directEvidence} onOpen={openEvidence} /> : <p>이 계층에 직접 분류된 Agenda가 없습니다.</p>}
          </div>
          <div className="collection-document__reference-group is-rollup">
            <h3>하위 계층에서 롤업된 참고문서 <span>{collection.rolledUpEvidence.length}</span></h3>
            {collection.rolledUpEvidence.length ? <EvidenceTree evidence={collection.rolledUpEvidence} onOpen={openEvidence} /> : <p>하위 계층에서 포함된 Agenda가 없습니다.</p>}
          </div>
          {additionalEvidence.length ? <div className="collection-document__reference-group">
            <h3>연관 Topic의 추가 참고문서 <span>{additionalEvidence.length}</span></h3>
            <EvidenceTree evidence={additionalEvidence} onOpen={openEvidence} />
          </div> : null}
        </> : allEvidence.length > 0 ? <EvidenceTree evidence={allEvidence} onOpen={openEvidence} /> : <p>이 문서 범위에 표시할 원문 근거가 없습니다.</p>}
      </section>
      <section id="collection-backlinks" className="canonical-document__section collection-document__backlinks">
        <span className="canonical-document__node">05</span><h2>Backlinks</h2>
        {backlinks.length ? <ul>{backlinks.map((link) => <li key={link.href}><a href={link.href}>↩ {link.label}</a></li>)}</ul> : <p>이 문서를 참조하는 다른 합성 문서가 없습니다.</p>}
      </section>
    </div>
    {selectedEvidence ? <EvidenceDrawer evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} triggerRef={evidenceTriggerRef} /> : null}
  </article>
}
