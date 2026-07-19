import { useEffect, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'

import { fetchTeamWiki, fetchWikiTeams } from '../api/knowledge'
import { formatTargetPath, TopicList } from '../components/TopicList'
import type { TeamWikiView } from '../types'

function selectedTeam(params: Readonly<Record<string, string | undefined>>) {
  const value = params.team ?? params['*']?.split('/')[0]
  return value ? decodeURIComponent(value) : undefined
}

export function TeamWikiPage() {
  const params = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const team = selectedTeam(params)
  const [view, setView] = useState<TeamWikiView | null>(null)
  const [error, setError] = useState(false)
  const [index, setIndex] = useState<string[]>([])

  useEffect(() => {
    if (!team) return
    const controller = new AbortController()
    setView(null)
    setError(false)
    fetchTeamWiki(team, controller.signal).then(setView).catch((fetchError: unknown) => {
      if (fetchError instanceof DOMException && fetchError.name === 'AbortError') return
      setError(true)
    })
    return () => controller.abort()
  }, [team])

  useEffect(() => {
    if (team) return
    const controller = new AbortController()
    fetchWikiTeams(controller.signal).then((value) => setIndex(value.values)).catch(() => setError(true))
    return () => controller.abort()
  }, [team])

  if (error) return <p className="projection-page-status projection-page-status--error" role="alert">팀 기여 보기를 불러오지 못했습니다.</p>
  if (!team) {
    return (
      <section className="projection-page projection-page--empty">
        <p className="projection-page__eyebrow">TEAM CONTRIBUTION PROJECTION</p>
        <h1>팀 기여 보기</h1>
        <p>게시된 Topic의 기여 팀을 선택하세요.</p>
        {index.length > 0 ? <ul>{index.map((value) => (
          <li key={value}><button type="button" onClick={() => navigate(`/wiki/teams/${encodeURIComponent(value)}`)}>{value}</button></li>
        ))}</ul> : <p>선택할 팀이 없습니다.</p>}
      </section>
    )
  }
  if (!view) return <p className="projection-page-status" role="status">팀 기여 보기를 불러오는 중입니다.</p>

  const from = location.pathname + location.search
  const teamFacets = [view.team, ...view.partner_teams]
  const sharedTopics = view.topics.filter((topic) => topic.teams.some((contributor) => contributor !== view.team))

  return (
    <article className="projection-page">
      <header className="projection-page__header">
        <div>
          <p className="projection-page__eyebrow">TEAM CONTRIBUTION · BACKEND PROJECTION</p>
          <h1>{view.team}</h1>
          <p>현재 Topic {view.topic_ids.length}건 · 최근 활동은 백엔드가 산정한 4주 범위입니다.</p>
        </div>
        <label className="projection-selector">
          <span>팀 선택</span>
          <select
            value={view.team}
            onChange={(event) => navigate(`/wiki/teams/${encodeURIComponent(event.target.value)}`)}
          >
            {teamFacets.map((facet) => <option key={facet} value={facet}>{facet}</option>)}
          </select>
        </label>
      </header>

      <dl className="projection-facts">
        <div><dt>협업 팀</dt><dd>{view.partner_teams.join(', ') || '없음'}</dd></div>
        <div>
          <dt>분류 커버리지</dt>
          <dd>{view.target_paths.length > 0 ? view.target_paths.map(formatTargetPath).join(', ') : '공통'}</dd>
        </div>
        <div><dt>보고 근거</dt><dd>{view.recent_activity.length}건</dd></div>
      </dl>

      <div className="projection-page__sections">
        <section className="projection-section">
          <h2>현재·최근 기여 Topic</h2>
          <TopicList topics={view.topics} from={from} showRankReasons />
        </section>

        <section className="projection-section">
          <h2>공동 기여 Topic</h2>
          <TopicList topics={sharedTopics} from={from} showRankReasons />
        </section>

        <section className="projection-section">
          <h2>조치와 의사결정</h2>
          <TopicList topics={view.actions_and_decisions} from={from} showRankReasons />
        </section>

        <section className="projection-section" aria-label="최근 4주 보고 근거">
          <h2>최근 4주 보고 근거</h2>
          {view.recent_activity.length > 0 ? (
            <ol className="projection-evidence">
              {view.recent_activity.map((entry) => (
                <li key={entry.agenda_id}>
                  <header><strong>{entry.subject}</strong><span>{entry.week} · {entry.team}</span></header>
                  <blockquote>{entry.source_quote}</blockquote>
                  <footer><span>{entry.agenda_id} · {entry.mail_id}</span><span>{entry.source_path ?? '원문 경로 없음'}</span></footer>
                </li>
              ))}
            </ol>
          ) : <p className="projection-empty">최근 4주 보고 근거가 없습니다.</p>}
        </section>
      </div>
    </article>
  )
}
