import { useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'

import { fetchTopics, fetchWeekWiki, fetchWikiBuild } from '../api/knowledge'
import { TopicList } from '../components/TopicList'
import type { TopicListItem, WeekWikiView, WikiBuildRun } from '../types'

const buildStatusLabels: Record<WikiBuildRun['status'], string> = {
  linking: '연결 중', review_required: '배정 검토 필요', generating: '생성 중',
  validating: '검증 중', published: '게시 완료', partially_failed: '부분 실패', failed: '실패',
}

function selectedWeek(params: Readonly<Record<string, string | undefined>>) {
  const value = params.week ?? params['*']?.split('/')[0]
  return value ? decodeURIComponent(value) : undefined
}

function topicsFor(ids: string[], topicsById: Map<string, TopicListItem>) {
  return ids.flatMap((id) => {
    const topic = topicsById.get(id)
    return topic ? [topic] : []
  })
}

function TopicGroup({
  title, ids, topicsById, from,
}: {
  title: string
  ids: string[]
  topicsById: Map<string, TopicListItem>
  from: string
}) {
  return (
    <section className="projection-section projection-section--group">
      <h2>{title}</h2>
      <TopicList topics={topicsFor(ids, topicsById)} from={from} />
      {ids.some((id) => !topicsById.has(id)) ? (
        <p className="projection-note">목록에 없는 Topic ID: {ids.filter((id) => !topicsById.has(id)).join(', ')}</p>
      ) : null}
    </section>
  )
}

export function WeekWikiPage() {
  const params = useParams()
  const location = useLocation()
  const navigate = useNavigate()
  const week = selectedWeek(params)
  const [view, setView] = useState<WeekWikiView | null>(null)
  const [topics, setTopics] = useState<TopicListItem[]>([])
  const [build, setBuild] = useState<WikiBuildRun | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    if (!week) return
    const controller = new AbortController()
    setView(null)
    setTopics([])
    setBuild(null)
    setError(false)

    Promise.all([fetchWeekWiki(week, controller.signal), fetchTopics({}, controller.signal)])
      .then(async ([nextView, nextTopics]) => {
        const nextBuild = nextView.build_run_id
          ? await fetchWikiBuild(nextView.build_run_id, controller.signal)
          : null
        setView(nextView)
        setTopics(nextTopics)
        setBuild(nextBuild)
      })
      .catch((fetchError: unknown) => {
        if (fetchError instanceof DOMException && fetchError.name === 'AbortError') return
        setError(true)
      })
    return () => controller.abort()
  }, [week])

  const topicsById = useMemo(() => new Map(topics.map((topic) => [topic.topic_id, topic])), [topics])

  if (!week) {
    return (
      <section className="projection-page projection-page--empty">
        <p className="projection-page__eyebrow">VERSIONED WEEK SNAPSHOT</p>
        <h1>주차 변경 보기</h1>
        <p>주차 경로를 선택해 게시된 변경 스냅샷을 확인하세요.</p>
      </section>
    )
  }
  if (error) return <p className="projection-page-status projection-page-status--error" role="alert">주차 스냅샷을 불러오지 못했습니다.</p>
  if (!view || (view.build_run_id && !build)) return <p className="projection-page-status" role="status">주차 스냅샷을 불러오는 중입니다.</p>

  const from = location.pathname + location.search
  const actions = topicsFor(view.changed_topic_ids, topicsById)
    .filter((topic) => topic.primary_area === 'decision_action')

  return (
    <article className="projection-page">
      <header className="projection-page__header">
        <div>
          <p className="projection-page__eyebrow">VERSIONED WEEK SNAPSHOT · READ ONLY</p>
          <h1>{view.week}</h1>
          <p>게시된 변경 집합과 빌드 출처를 그대로 표시합니다.</p>
        </div>
        <label className="projection-selector">
          <span>주차 선택</span>
          <input type="week" value={view.week} onChange={(event) => {
            if (event.target.value) navigate(`/wiki/weeks/${event.target.value}`)
          }} />
        </label>
      </header>

      <dl className="projection-provenance" aria-label="스냅샷 빌드 출처">
        <div><dt>스냅샷 리비전</dt><dd>{view.revision_id}</dd></div>
        <div><dt>게시 시각</dt><dd><time dateTime={view.published_at}>{view.published_at}</time></dd></div>
        <div><dt>빌드 실행</dt><dd>{view.build_run_id || '기록 없음'}</dd></div>
        <div><dt>빌드 상태</dt><dd>{build ? buildStatusLabels[build.status] : '기록 없음'}</dd></div>
        {build?.status === 'partially_failed' ? (
          <div className="projection-provenance__wide">
            <dt>이전 유효 리비전 유지</dt><dd>{build.failed_topic_ids.join(', ') || '없음'}</dd>
          </div>
        ) : null}
      </dl>

      <div className="projection-page__sections projection-page__sections--week">
        <TopicGroup title="새 Topic" ids={view.new_topic_ids} topicsById={topicsById} from={from} />
        <TopicGroup title="변경된 Topic" ids={view.changed_topic_ids} topicsById={topicsById} from={from} />
        <TopicGroup title="해결된 Topic" ids={view.resolved_topic_ids} topicsById={topicsById} from={from} />
        <TopicGroup title="재발한 Topic" ids={view.reopened_topic_ids} topicsById={topicsById} from={from} />

        <section className="projection-section">
          <h2>중요 조치와 의사결정</h2>
          <TopicList topics={actions} from={from} />
        </section>

        <section className="projection-section">
          <h2>검토와 관계 감사</h2>
          <dl className="projection-audit">
            <div><dt>배정 검토</dt><dd>미해결 배정 {view.pending_assignment_count}건</dd></div>
            <div><dt>새 승인 관계</dt><dd>{view.new_relation_ids.join(', ') || '없음'}</dd></div>
            <div><dt>모순 관계</dt><dd>{view.contradictions.join(', ') || '없음'}</dd></div>
            <div><dt>기여 팀</dt><dd>{view.teams.join(', ') || '없음'}</dd></div>
          </dl>
        </section>
      </div>
    </article>
  )
}
