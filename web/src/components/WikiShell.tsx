import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet } from 'react-router-dom'

import { fetchWikiReviews } from '../api/knowledge'
import { WIKI_ASSIGNMENT_REVIEWS_CHANGED } from '../reviewEvents'

const wikiModes = [
  { label: '주제', to: '/wiki/topics' },
  { label: 'LOTCD', to: '/wiki/lotcd' },
  { label: '팀', to: '/wiki/teams' },
  { label: '주차', to: '/wiki/weeks' },
]

export function WikiShell() {
  const [blockingReviewCount, setBlockingReviewCount] = useState(0)

  useEffect(() => {
    let controller: AbortController | null = null
    const refresh = () => {
      controller?.abort()
      const request = new AbortController()
      controller = request
      fetchWikiReviews(request.signal).then((reviews) => {
        if (request.signal.aborted) return
        setBlockingReviewCount(reviews.filter((review) => review.kind === 'assignment').length)
      }).catch((error: unknown) => {
        if (!(error instanceof DOMException && error.name === 'AbortError')) setBlockingReviewCount(0)
      })
    }
    refresh()
    window.addEventListener(WIKI_ASSIGNMENT_REVIEWS_CHANGED, refresh)
    return () => {
      window.removeEventListener(WIKI_ASSIGNMENT_REVIEWS_CHANGED, refresh)
      controller?.abort()
    }
  }, [])

  return (
    <div className="wiki-shell">
      <header className="wiki-shell__header">
        <Link to="/wiki/topics" className="wiki-shell__brand">
          <strong>Weekly Knowledge Wiki</strong>
          <span>주간 기술 로그북</span>
        </Link>
        <nav className="wiki-shell__modes" aria-label="Wiki 탐색 모드">
          {wikiModes.map((mode) => (
            <NavLink key={mode.to} to={mode.to}>{mode.label}</NavLink>
          ))}
        </nav>
        <div className="wiki-shell__operator-links">
          {blockingReviewCount > 0 ? (
            <Link className="wiki-shell__review-link" to="/wiki/reviews" aria-label={`차단 중인 배정 검토 ${blockingReviewCount}건`}>
              검토 <span>{blockingReviewCount}</span>
            </Link>
          ) : null}
          <Link className="wiki-shell__classification-link" to="/classification">분류 작업대</Link>
        </div>
      </header>
      <main className="wiki-shell__content">
        <Outlet />
      </main>
    </div>
  )
}
