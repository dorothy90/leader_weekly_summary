import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { fetchTaxonomy, fetchWeekWiki, fetchWikiBuild, fetchWikiTeams, fetchWikiWeeks } from '../../api/knowledge'
import type { Taxonomy, WikiBuildRun } from '../../types'

interface KnowledgeTreeProps {
  activePath: string
  mode: 'knowledge' | 'evidence'
  onModeChange: (mode: 'knowledge' | 'evidence') => void
  collapsed: boolean
  onCollapse: () => void
}

const areaLinks = [
  ['yield_defect', '수율·불량'], ['process_equipment', '공정·장비'],
  ['quality_analysis', '품질·분석'], ['experiment_validation', '실험·검증'],
] as const

const buildStatusLabels: Record<WikiBuildRun['status'], string> = {
  linking: '연결 중', review_required: '검토 필요', generating: '생성 중', validating: '검증 중',
  published: '게시 완료', partially_failed: '부분 실패', failed: '실패',
}

export function KnowledgeTree({ activePath, mode, onModeChange, collapsed, onCollapse }: KnowledgeTreeProps) {
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null)
  const [teams, setTeams] = useState<string[]>([])
  const [weeks, setWeeks] = useState<string[]>([])
  const [latestBuild, setLatestBuild] = useState<WikiBuildRun | null>(null)

  useEffect(() => {
    const controller = new AbortController()
    Promise.allSettled([
      fetchTaxonomy(controller.signal), fetchWikiTeams(controller.signal), fetchWikiWeeks(controller.signal),
    ]).then(([taxonomyResult, teamResult, weekResult]) => {
      if (taxonomyResult.status === 'fulfilled') setTaxonomy(taxonomyResult.value)
      if (teamResult.status === 'fulfilled') setTeams(teamResult.value.values)
      if (weekResult.status === 'fulfilled') {
        const sourceWeeks = weekResult.value.values
        setWeeks([...sourceWeeks].reverse())
        const latestWeek = sourceWeeks[sourceWeeks.length - 1]
        if (latestWeek) {
          fetchWeekWiki(latestWeek, controller.signal)
            .then((week) => week.build_run_id ? fetchWikiBuild(week.build_run_id, controller.signal) : null)
            .then((build) => setLatestBuild(build))
            .catch(() => setLatestBuild(null))
        }
      }
    })
    return () => controller.abort()
  }, [])

  const current = (path: string) => activePath.split('?')[0] === path ? 'page' as const : undefined

  return (
    <nav className={`knowledge-tree ${collapsed ? 'is-collapsed' : ''}`} aria-label="지식 탐색">
      <header className="knowledge-tree__header">
        <div><strong>KNOWLEDGE TREE</strong><small>JSON CANONICAL STORE</small></div>
        <button type="button" onClick={onCollapse} aria-label={collapsed ? '지식 탐색 열기' : '지식 탐색 접기'}>{collapsed ? '›' : '‹'}</button>
      </header>
      {collapsed ? null : <>
        <div className="knowledge-tree__tabs" role="tablist" aria-label="탐색 데이터">
          <button type="button" role="tab" aria-selected={mode === 'knowledge'} onClick={() => onModeChange('knowledge')}>지식</button>
          <button type="button" role="tab" aria-selected={mode === 'evidence'} onClick={() => onModeChange('evidence')}>근거</button>
        </div>
        <div className="knowledge-tree__scroll" id="evidence-tree">
          {mode === 'knowledge' ? <>
            <details open><summary>주제 <span>{areaLinks.length}</span></summary>
              <Link to="/wiki/topics" aria-current={current('/wiki/topics')}>전체 주제</Link>
              {areaLinks.map(([area, label]) => <Link key={area} to={`/wiki/topics?area=${area}`}>{label}</Link>)}
            </details>
            <details open><summary>LOTCD <span>{taxonomy?.domains.reduce((count, domain) => count + domain.techs.reduce((sum, tech) => sum + tech.lotcds.length, 0), 0) ?? 0}</span></summary>
              {taxonomy?.domains.map((domain) => <details key={domain.id} className="knowledge-tree__nested"><summary>{domain.name}</summary>
                {domain.techs.map((tech) => <details key={tech.id} className="knowledge-tree__nested"><summary>{tech.name}</summary>
                  {tech.lotcds.map((lotcd) => {
                    const path = `/wiki/lotcd/${domain.name}/${encodeURIComponent(tech.name)}/${encodeURIComponent(lotcd.code)}`
                    return <Link key={lotcd.code} to={path} aria-current={current(path)}>{lotcd.code}</Link>
                  })}
                </details>)}
              </details>)}
            </details>
            <details open><summary>팀 <span>{teams.length}</span></summary>
              {teams.map((team) => {
                const path = `/wiki/teams/${encodeURIComponent(team)}`
                return <Link key={team} to={path} aria-current={current(path)}>{team}</Link>
              })}
            </details>
            <details open><summary>주차 <span>{weeks.length}</span></summary>
              {weeks.map((week) => {
                const path = `/wiki/weeks/${week}`
                return <Link key={week} to={path} aria-current={current(path)}>{week}</Link>
              })}
            </details>
          </> : <>
            <p className="knowledge-tree__hint">승인된 주차와 팀별 원문 근거</p>
            <details open><summary>주차별 Evidence <span>{weeks.length}</span></summary>
              {weeks.map((week) => <Link key={week} to={`/wiki/weeks/${week}`}>{week}</Link>)}
            </details>
            <details open><summary>팀별 Evidence <span>{teams.length}</span></summary>
              {teams.map((team) => <Link key={team} to={`/wiki/teams/${encodeURIComponent(team)}`}>{team}</Link>)}
            </details>
          </>}
        </div>
        {latestBuild ? <aside className={`knowledge-tree__activity is-${latestBuild.status}`} aria-label="최근 빌드 상태">
          <div><strong>최근 빌드</strong><span>{latestBuild.week}</span></div>
          <b>{buildStatusLabels[latestBuild.status]}</b>
          <small>{latestBuild.model}</small>
          {latestBuild.failed_topic_ids.length ? <small>실패 Topic {latestBuild.failed_topic_ids.length}</small> : null}
        </aside> : null}
      </>}
    </nav>
  )
}
