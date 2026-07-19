import { useEffect, useState } from 'react'
import { Link, useLocation, useParams, useSearchParams } from 'react-router-dom'

import { fetchTopic } from '../api/knowledge'
import { EvidenceDrawer } from '../components/EvidenceDrawer'
import { formatTargetPath, knowledgeAreaLabels, topicStateLabels } from '../components/TopicList'
import type { WikiEvidence, WikiTopicDetail } from '../types'

const relationLabels = {
  possible_cause: '가능 원인', affects: '영향', measurement_effect: '측정 영향', comparison: '비교',
  follow_up: '후속', supports: '지지', contradicts: '상충', shares_condition: '조건 공유',
} as const

export function TopicPage() {
  const { topicId = '' } = useParams()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const [detail, setDetail] = useState<WikiTopicDetail | null>(null)
  const [selectedEvidence, setSelectedEvidence] = useState<WikiEvidence | null>(null)
  const [error, setError] = useState(false)
  const from = searchParams.get('from')
  const previousPath = from?.startsWith('/wiki/') ? from : '/wiki/topics'
  const relationOrigin = from?.startsWith('/wiki/') ? from : location.pathname + location.search

  useEffect(() => {
    const controller = new AbortController()
    setDetail(null)
    setSelectedEvidence(null)
    setError(false)
    fetchTopic(topicId, controller.signal)
      .then(setDetail)
      .catch((fetchError: unknown) => {
        if (fetchError instanceof DOMException && fetchError.name === 'AbortError') return
        setError(true)
      })
    return () => controller.abort()
  }, [topicId])

  if (error) return <p className="topic-page-status topic-page-status--error" role="alert">주제 문서를 불러오지 못했습니다.</p>
  if (!detail) return <p className="topic-page-status" role="status">주제 문서를 불러오는 중입니다.</p>

  const { topic } = detail
  const evidenceById = new Map(detail.evidence.map((evidence) => [evidence.agenda_id, evidence]))
  const acceptedRelations = detail.relations.filter((relation) => relation.review_state === 'accepted')

  return (
    <article className="topic-document">
      <header className="topic-document__header">
        <Link className="topic-document__back" to={previousPath} aria-label="이전 화면">← 이전 화면</Link>
        <div className="topic-document__identity">
          <span>{topic.topic_id}</span><span>REV {topic.current_revision_id}</span>
        </div>
        <h1>{topic.title}</h1>
        <div className="topic-document__badges">
          <span className={`topic-badge topic-badge--${topic.state}`}>{topicStateLabels[topic.state]}</span>
          <span className={`topic-importance topic-importance--${topic.importance}`}>{topic.importance.toUpperCase()}</span>
          <span>{knowledgeAreaLabels[topic.primary_area]}</span>
        </div>
        <dl className="topic-document__metadata">
          <div><dt>관찰 기간</dt><dd>{topic.first_seen_week} → {topic.last_updated_week}</dd></div>
          <div><dt>대상 경로</dt><dd>{topic.target_paths.map(formatTargetPath).join(', ') || '공통'}</dd></div>
          <div><dt>기여 팀</dt><dd>{topic.teams.join(' · ') || '미지정'}</dd></div>
          <div><dt>Agenda</dt><dd>{topic.source_agenda_ids.length}건</dd></div>
        </dl>
      </header>

      <div className="topic-document__layout">
        <main className="topic-document__main">
          {detail.sections.map((section) => (
            <section key={section.key} className="topic-document__section">
              <h2>{section.title}</h2>
              <p className="topic-document__section-body">{section.body}</p>
            </section>
          ))}
        </main>

        <aside className="topic-document__rail" aria-label="주제 근거와 관계">
          <section>
            <h2>주장과 근거</h2>
            <ol className="topic-claims">
              {detail.claims.map((claim, index) => (
                <li key={`${claim.text}-${index}`}>
                  <p>{claim.text}</p>
                  <div className="topic-claims__citations">
                    {claim.agenda_ids.map((agendaId) => {
                      const evidence = evidenceById.get(agendaId)
                      return evidence ? (
                        <button key={agendaId} type="button" onClick={() => setSelectedEvidence(evidence)}>
                          근거 {agendaId} 보기
                        </button>
                      ) : <span key={agendaId}>{agendaId} · 원문 없음</span>
                    })}
                  </div>
                </li>
              ))}
            </ol>
          </section>
          <section>
            <h2>승인된 관계</h2>
            {acceptedRelations.length > 0 ? (
              <ul className="topic-relations">
                {acceptedRelations.map((relation) => {
                  const relatedId = relation.source_topic_id === topic.topic_id
                    ? relation.target_topic_id : relation.source_topic_id
                  return (
                    <li key={relation.relation_id}>
                      <span>{relationLabels[relation.kind]}</span>
                      <Link to={`/wiki/topics/${encodeURIComponent(relatedId)}?from=${encodeURIComponent(relationOrigin)}`}>
                        {relatedId}
                      </Link>
                      <small>{Math.round(relation.confidence * 100)}%</small>
                    </li>
                  )
                })}
              </ul>
            ) : <p className="topic-document__muted">승인된 관계가 없습니다.</p>}
          </section>
        </aside>
      </div>

      {selectedEvidence ? <EvidenceDrawer evidence={selectedEvidence} onClose={() => setSelectedEvidence(null)} /> : null}
    </article>
  )
}
