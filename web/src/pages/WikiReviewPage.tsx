import { useEffect, useRef, useState } from 'react'

import {
  fetchSession,
  fetchTopic,
  fetchWikiReviews,
  resolveWikiReview,
  startWikiBuild,
} from '../api/knowledge'
import { BuildStatusBanner } from '../components/BuildStatusBanner'
import { WIKI_ASSIGNMENT_REVIEWS_CHANGED } from '../reviewEvents'
import type {
  KnowledgeSession,
  WikiBuildRun,
  WikiReview,
  WikiReviewResolution,
} from '../types'

function AssignmentReviewCard({
  review,
  candidateDetails,
  canEdit,
  busy,
  onResolve,
}: {
  review: WikiReview
  candidateDetails: Record<string, { title: string; path: string }>
  canEdit: boolean
  busy: boolean
  onResolve: (input: WikiReviewResolution) => Promise<void>
}) {
  const [title, setTitle] = useState('')

  return (
    <article className="review-card">
      <header>
        <div>
          <span className="review-card__kind review-card__kind--blocking">발행 차단</span>
          <h3>Agenda {review.agenda_id ?? '기록 없음'}</h3>
        </div>
        <span className="review-card__id">{review.review_id}</span>
      </header>
      <p className="review-card__rationale">{review.rationale || '검토 근거가 없습니다.'}</p>
      <ol className="review-candidates">
        {review.candidates.map((candidate) => (
          <li key={candidate.topic_id}>
            <div className="review-candidate__identity">
              <strong>{candidate.topic_id}</strong>
              <span>{candidateDetails[candidate.topic_id]?.title ?? '제목 정보 없음'}</span>
            </div>
            <dl>
              <div>
                <dt>경로 및 근거</dt>
                <dd>
                  {candidateDetails[candidate.topic_id]?.path ?? '경로 정보 없음'}
                  {candidate.rank_reasons.length > 0 ? ` · ${candidate.rank_reasons.join(' · ')}` : ''}
                </dd>
              </div>
              <div><dt>점수</dt><dd>{candidate.score.toFixed(2)}</dd></div>
            </dl>
            <button type="button" disabled={!canEdit || busy} onClick={() => void onResolve({
              action: 'attach', topic_id: candidate.topic_id,
            })}>{candidate.topic_id}에 연결</button>
          </li>
        ))}
      </ol>
      <form className="review-create" onSubmit={(event) => {
        event.preventDefault()
        if (title.trim()) void onResolve({ action: 'create', title: title.trim() })
      }}>
        <label>
          <span>새 Topic 제목</span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} disabled={!canEdit || busy} />
        </label>
        <button type="submit" disabled={!canEdit || busy || !title.trim()}>새 Topic 생성</button>
        <button type="button" disabled={!canEdit || busy} onClick={() => void onResolve({ action: 'hold' })}>
          보류
        </button>
      </form>
    </article>
  )
}

function RelationReviewCard({
  review,
  canEdit,
  busy,
  onResolve,
}: {
  review: WikiReview
  canEdit: boolean
  busy: boolean
  onResolve: (input: WikiReviewResolution) => Promise<void>
}) {
  return (
    <article className="review-card">
      <header>
        <div>
          <span className="review-card__kind">발행 비차단</span>
          <h3>관계 {review.relation_id ?? '기록 없음'}</h3>
        </div>
        <span className="review-card__id">{review.review_id}</span>
      </header>
      <dl className="review-relation">
        <div><dt>관계 종류</dt><dd>{review.relation_kind ?? '정보 없음'}</dd></div>
        <div><dt>Agenda 근거</dt><dd>{review.relation_agenda_ids?.join(', ') || '정보 없음'}</dd></div>
        <div><dt>근거</dt><dd>{review.rationale || '근거 정보 없음'}</dd></div>
      </dl>
      <div className="review-card__actions">
        <button type="button" disabled={!canEdit || busy} onClick={() => void onResolve({ action: 'accept' })}>
          관계 승인
        </button>
        <button type="button" disabled={!canEdit || busy} onClick={() => void onResolve({ action: 'reject' })}>
          관계 거절
        </button>
      </div>
    </article>
  )
}

export function WikiReviewPage() {
  const headingRef = useRef<HTMLHeadingElement>(null)
  const [reviews, setReviews] = useState<WikiReview[]>([])
  const [session, setSession] = useState<KnowledgeSession | null>(null)
  const [candidateDetails, setCandidateDetails] = useState<Record<string, { title: string; path: string }>>({})
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [error, setError] = useState(false)
  const [week, setWeek] = useState('')
  const [build, setBuild] = useState<WikiBuildRun | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchWikiReviews(controller.signal),
      fetchSession(controller.signal),
    ]).then(async ([nextReviews, nextSession]) => {
      const candidateIds = [...new Set(nextReviews.flatMap((review) => (
        review.kind === 'assignment' ? review.candidates.map((candidate) => candidate.topic_id) : []
      )))]
      const details = await Promise.allSettled(candidateIds.map((topicId) => (
        fetchTopic(topicId, controller.signal)
      )))
      if (controller.signal.aborted) return
      const nextDetails: Record<string, { title: string; path: string }> = {}
      details.forEach((result, index) => {
        if (result.status !== 'fulfilled') return
        nextDetails[candidateIds[index]] = {
          title: result.value.topic.title,
          path: result.value.topic.target_paths.map((path) => (
            [path.domain, path.tech, path.lotcd].filter(Boolean).join(' › ')
          )).join(', ') || '경로 정보 없음',
        }
      })
      setReviews(nextReviews)
      setSession(nextSession)
      setCandidateDetails(nextDetails)
    }).catch((fetchError: unknown) => {
      if (!(fetchError instanceof DOMException && fetchError.name === 'AbortError')) setError(true)
    }).finally(() => setLoading(false))
    return () => controller.abort()
  }, [])

  async function resolve(reviewId: string, input: WikiReviewResolution) {
    const review = reviews.find((value) => value.review_id === reviewId)
    setBusyId(reviewId)
    setMessage(null)
    setError(false)
    try {
      await resolveWikiReview(reviewId, input)
      setReviews((current) => current.filter((review) => review.review_id !== reviewId))
      setMessage('검토 결정을 저장했습니다.')
      headingRef.current?.focus()
      if (review?.kind === 'assignment') {
        window.dispatchEvent(new Event(WIKI_ASSIGNMENT_REVIEWS_CHANGED))
      }
    } catch {
      setError(true)
      setMessage('검토 결정을 저장하지 못했습니다.')
    } finally {
      setBusyId(null)
    }
  }

  async function startBuild() {
    if (!week) return
    setBusyId('build')
    setMessage(null)
    setError(false)
    try {
      const nextBuild = await startWikiBuild(week)
      setBuild(nextBuild)
      setMessage('Wiki 빌드를 시작했습니다.')
    } catch {
      setError(true)
      setMessage('승인된 주차의 Wiki 빌드를 시작하지 못했습니다.')
    } finally {
      setBusyId(null)
    }
  }

  if (loading || !session) return (
    <div className="review-page">
      <header className="review-page__header">
        <div><p className="review-page__eyebrow">OPERATOR REVIEW · HIDDEN ROUTE</p><h1>분류 검토</h1></div>
      </header>
      <p className={`review-page-status${loading ? '' : ' review-page-status--error'}`} role={loading ? 'status' : 'alert'}>
        {loading ? '검토 대기열을 불러오는 중입니다.' : '검토 대기열을 불러오지 못했습니다.'}
      </p>
    </div>
  )

  const assignments = reviews.filter((review) => review.kind === 'assignment')
  const relations = reviews.filter((review) => review.kind === 'relation')

  return (
    <div className="review-page">
      <header className="review-page__header">
        <div>
          <p className="review-page__eyebrow">OPERATOR REVIEW · HIDDEN ROUTE</p>
          <h1 ref={headingRef} tabIndex={-1}>분류 검토</h1>
          <p>불확실한 Topic 배정은 발행을 차단하고, 관계 제안은 읽기를 차단하지 않습니다.</p>
        </div>
        <span className="review-page__count">차단 {assignments.length}건</span>
      </header>

      {!session.can_edit ? <p className="review-page__notice">읽기 전용 계정입니다. 검토 내용은 볼 수 있지만 결정은 변경할 수 없습니다.</p> : null}
      {message ? <p className={`review-page__message${error ? ' review-page__message--error' : ''}`} role={error ? 'alert' : 'status'}>{message}</p> : null}

      <section className="review-page__section" aria-labelledby="assignment-reviews-title">
        <header>
          <div><h2 id="assignment-reviews-title">배정 검토</h2><p>미해결 항목은 Topic 발행을 차단합니다.</p></div>
          <span>{assignments.length}건</span>
        </header>
        <div className="review-page__cards">
          {assignments.map((review) => (
            <AssignmentReviewCard key={review.review_id} review={review} candidateDetails={candidateDetails} canEdit={session.can_edit}
              busy={busyId === review.review_id} onResolve={(input) => resolve(review.review_id, input)} />
          ))}
          {assignments.length === 0 ? <p className="review-page__empty">차단 중인 배정 검토가 없습니다.</p> : null}
        </div>
      </section>

      <section className="review-page__section" aria-labelledby="relation-reviews-title">
        <header>
          <div><h2 id="relation-reviews-title">관계 검토</h2><p>대기 중인 관계는 사실 서술에서 숨겨지며 발행을 차단하지 않습니다.</p></div>
          <span>{relations.length}건</span>
        </header>
        <div className="review-page__cards">
          {relations.map((review) => (
            <RelationReviewCard key={review.review_id} review={review} canEdit={session.can_edit}
              busy={busyId === review.review_id} onResolve={(input) => resolve(review.review_id, input)} />
          ))}
          {relations.length === 0 ? <p className="review-page__empty">대기 중인 관계 검토가 없습니다.</p> : null}
        </div>
      </section>

      <section className="review-page__section review-build" aria-labelledby="wiki-build-title">
        <header>
          <div><h2 id="wiki-build-title">Wiki 빌드</h2><p>분류 승인이 완료된 주차만 명시적으로 빌드합니다.</p></div>
        </header>
        <form onSubmit={(event) => { event.preventDefault(); void startBuild() }}>
          <label><span>승인된 주차</span><input type="week" value={week} onChange={(event) => setWeek(event.target.value)} disabled={!session.can_edit || busyId === 'build'} /></label>
          <button type="submit" disabled={!session.can_edit || busyId === 'build' || !week}>Wiki 빌드 시작</button>
        </form>
        {build ? <BuildStatusBanner key={build.run_id} run={build} onRunChange={setBuild} /> : null}
      </section>
    </div>
  )
}
