import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { fetchTaxonomy, fetchWikiTeams, fetchWikiWeeks } from '../../api/knowledge'
import type { Taxonomy } from '../../types'
import type { WikiCollectionKind } from './wikiLocation'

interface KnowledgeTreeProps {
  activePath: string
  kind: WikiCollectionKind
  collapsed: boolean
  onCollapse: () => void
}

const areaLinks = [
  ['yield_defect', '수율·불량'], ['process_equipment', '공정·장비'],
  ['quality_analysis', '품질·분석'], ['experiment_validation', '실험·검증'],
] as const

const modeLabels: Record<WikiCollectionKind, string> = {
  topics: '주제', lotcd: 'LOTCD', team: '팀', week: '주차',
}

export function KnowledgeTree({ activePath, kind, collapsed, onCollapse }: KnowledgeTreeProps) {
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null)
  const [values, setValues] = useState<string[]>([])

  useEffect(() => {
    const controller = new AbortController()
    setTaxonomy(null)
    setValues([])
    const request = kind === 'lotcd' ? fetchTaxonomy(controller.signal)
      : kind === 'team' ? fetchWikiTeams(controller.signal)
        : kind === 'week' ? fetchWikiWeeks(controller.signal) : null
    if (request) {
      request.then((result) => {
        if ('domains' in result) setTaxonomy(result)
        else setValues(kind === 'week' ? [...result.values].reverse() : result.values)
      }).catch(() => undefined)
    }
    return () => controller.abort()
  }, [kind])

  const current = (path: string) => activePath.split('?')[0] === path ? 'page' as const : undefined

  return <nav className={`knowledge-tree ${collapsed ? 'is-collapsed' : ''}`} aria-label={`${modeLabels[kind]} Library`}>
    <header className="knowledge-tree__header">
      <div><strong>{modeLabels[kind]} LIBRARY</strong><small>승인된 Wiki 문서</small></div>
      <button type="button" onClick={onCollapse} aria-label={collapsed ? 'Library 열기' : 'Library 접기'}>{collapsed ? '›' : '‹'}</button>
    </header>
    {collapsed ? null : <div className="knowledge-tree__scroll">
      {kind === 'topics' ? <div className="knowledge-tree__group">
        <Link to="/wiki/topics" aria-current={current('/wiki/topics')}>전체 주제</Link>
        {areaLinks.map(([area, label]) => <Link key={area} to={`/wiki/topics?area=${area}`}>{label}</Link>)}
      </div> : null}
      {kind === 'lotcd' ? <ul className="knowledge-tree__hierarchy">
        {taxonomy?.domains.map((domain) => {
          const domainPath = `/wiki/lotcd/${domain.name}`
          return <li key={domain.id}>
            <Link to={domainPath} aria-current={current(domainPath)}>{domain.name}</Link>
            <ul>{domain.techs.map((tech) => {
              const techPath = `${domainPath}/${encodeURIComponent(tech.name)}`
              return <li key={tech.id}>
                <Link to={techPath} aria-current={current(techPath)}>{tech.name}</Link>
                <ul>{tech.lotcds.map((lotcd) => {
                  const lotcdPath = `${techPath}/${encodeURIComponent(lotcd.code)}`
                  return <li key={lotcd.code}><Link to={lotcdPath} aria-current={current(lotcdPath)}>{lotcd.code}</Link></li>
                })}</ul>
              </li>
            })}</ul>
          </li>
        })}
      </ul> : null}
      {kind === 'team' ? <div className="knowledge-tree__group">{values.map((team) => {
        const path = `/wiki/teams/${encodeURIComponent(team)}`
        return <Link key={team} to={path} aria-current={current(path)}>{team}</Link>
      })}</div> : null}
      {kind === 'week' ? <div className="knowledge-tree__group">{values.map((week) => {
        const path = `/wiki/weeks/${week}`
        return <Link key={week} to={path} aria-current={current(path)}>{week}</Link>
      })}</div> : null}
    </div>}
  </nav>
}
