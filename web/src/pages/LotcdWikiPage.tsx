import { useEffect, useState } from 'react'
import { Link, useLocation, useParams } from 'react-router-dom'

import { fetchLotcdWiki, fetchTaxonomy } from '../api/knowledge'
import { TaxonomyTree } from '../components/TaxonomyTree'
import { knowledgeAreaLabels, TopicList } from '../components/TopicList'
import type { DomainName, LotcdWikiView, Taxonomy, WikiEvidence } from '../types'

interface CanonicalLotcdPath {
  domain: DomainName
  tech: string
  lotcd: string
}

function routePath(params: Readonly<Record<string, string | undefined>>) {
  const parts = params['*']?.split('/').map((part) => decodeURIComponent(part)) ?? []
  return {
    domain: params.domain ?? parts[0],
    tech: params.tech ?? parts[1],
    lotcd: params.lotcd ?? parts[2],
  }
}

function isDomainName(value: string | undefined): value is DomainName {
  return value === 'DRAM' || value === 'NAND'
}

function groupActivity(activity: WikiEvidence[]) {
  const weeks = new Map<string, Map<string, WikiEvidence[]>>()
  activity.forEach((evidence) => {
    const teams = weeks.get(evidence.week) ?? new Map<string, WikiEvidence[]>()
    const entries = teams.get(evidence.team) ?? []
    entries.push(evidence)
    teams.set(evidence.team, entries)
    weeks.set(evidence.week, teams)
  })
  return weeks
}

function relatedPaths(taxonomy: Taxonomy, lotcdCode: string): CanonicalLotcdPath[] {
  return taxonomy.domains.flatMap((domain) => domain.techs.flatMap((tech) => (
    tech.lotcds.some((lotcd) => lotcd.code === lotcdCode)
      ? [{ domain: domain.name, tech: tech.name, lotcd: lotcdCode }]
      : []
  )))
}

function lotcdLink(path: CanonicalLotcdPath, from: string) {
  return `/wiki/lotcd/${encodeURIComponent(path.domain)}/${encodeURIComponent(path.tech)}/${encodeURIComponent(path.lotcd)}?from=${encodeURIComponent(from)}`
}

function RankedTopics({ topics, from }: { topics: LotcdWikiView['active_topics']; from: string }) {
  return <TopicList topics={topics} from={from} showRankReasons />
}

export function LotcdWikiPage() {
  const params = useParams()
  const location = useLocation()
  const { domain, tech, lotcd } = routePath(params)
  const hasSelection = isDomainName(domain) && Boolean(tech && lotcd)
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null)
  const [view, setView] = useState<LotcdWikiView | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    setTaxonomy(null)
    setView(null)
    setError(false)

    const taxonomyRequest = fetchTaxonomy(controller.signal)
    const requests = hasSelection
      ? Promise.all([
        taxonomyRequest,
        fetchLotcdWiki(domain, tech as string, lotcd as string, controller.signal),
      ]).then(([nextTaxonomy, nextView]) => {
        setTaxonomy(nextTaxonomy)
        setView(nextView)
      })
      : taxonomyRequest.then(setTaxonomy)

    requests.catch((fetchError: unknown) => {
      if (fetchError instanceof DOMException && fetchError.name === 'AbortError') return
      setError(true)
    })
    return () => controller.abort()
  }, [domain, hasSelection, lotcd, tech])

  if (error) {
    return <p className="lotcd-page-status lotcd-page-status--error" role="alert">LOTCD 문서를 불러오지 못했습니다.</p>
  }
  if (!taxonomy || (hasSelection && !view)) {
    return <p className="lotcd-page-status" role="status">LOTCD 문서를 불러오는 중입니다.</p>
  }

  const currentPath = view
    ? { domain: view.domain, tech: view.tech, lotcd: view.lotcd }
    : undefined
  const from = location.pathname + location.search
  const activity = view
    ? groupActivity(view.activity)
    : new Map<string, Map<string, WikiEvidence[]>>()

  return (
    <div className="lotcd-workspace">
      <aside className="lotcd-workspace__rail">
        <TaxonomyTree
          key={currentPath ? `${currentPath.domain}/${currentPath.tech}/${currentPath.lotcd}` : 'root'}
          taxonomy={taxonomy}
          currentPath={currentPath}
        />
      </aside>

      {view ? (
        <article className="lotcd-document">
          <header className="lotcd-document__header">
            <p className="lotcd-document__eyebrow">LOTCD PROJECTION · READ ONLY</p>
            <div className="lotcd-coordinate" aria-label="현재 분류 경로">
              <span>{view.domain}</span><span>{view.tech}</span><strong>{view.lotcd}</strong>
            </div>
            <h1>{view.lotcd} 운영 로그</h1>
            <p>{view.topic_ids.length}개 Topic의 현재 투영 · RULESET v{taxonomy.version}</p>
          </header>

          <div className="lotcd-document__sections">
            <section className="lotcd-section">
              <h2>현황 요약</h2>
              <p className="lotcd-summary">{view.summary}</p>
            </section>

            <section className="lotcd-section">
              <h2>주요 변화</h2>
              <RankedTopics topics={view.recent_changes} from={from} />
            </section>

            <section className="lotcd-section">
              <h2>진행 중 Topic</h2>
              <RankedTopics topics={view.active_topics} from={from} />
            </section>

            <section className="lotcd-section">
              <h2>지식 영역</h2>
              <div className="lotcd-groups">
                {Object.entries(view.knowledge_areas).map(([area, topics]) => (
                  <section key={area} className="lotcd-group">
                    <h3>{knowledgeAreaLabels[area as keyof typeof knowledgeAreaLabels]}</h3>
                    <RankedTopics topics={topics ?? []} from={from} />
                  </section>
                ))}
              </div>
            </section>

            <section className="lotcd-section">
              <h2>조치와 의사결정</h2>
              <RankedTopics topics={view.actions_and_decisions} from={from} />
            </section>

            <section className="lotcd-section">
              <h2>연관 LOTCD</h2>
              {view.related_lotcds.length > 0 ? (
                <ul className="lotcd-related">
                  {view.related_lotcds.map((related) => {
                    const paths = relatedPaths(taxonomy, related)
                    if (paths.length === 0) {
                      return <li key={related} className="lotcd-related__unavailable">{related} · 경로 확인 불가</li>
                    }
                    if (paths.length === 1) {
                      return <li key={related}><Link to={lotcdLink(paths[0], from)}>{related}</Link></li>
                    }
                    return (
                      <li key={related} className="lotcd-related__ambiguous">
                        <span>{related} · 경로 {paths.length}개</span>
                        <ul>
                          {paths.map((path) => {
                            const label = `${path.domain} / ${path.tech} / ${path.lotcd}`
                            return <li key={label}><Link to={lotcdLink(path, from)}>{label}</Link></li>
                          })}
                        </ul>
                      </li>
                    )
                  })}
                </ul>
              ) : <p className="lotcd-empty">연관 LOTCD가 없습니다.</p>}
            </section>

            <section className="lotcd-section">
              <h2>해결·종료된 Topic</h2>
              <div className="lotcd-groups">
                {Object.entries(view.closed_topics).map(([quarter, topics]) => (
                  <section key={quarter} className="lotcd-group">
                    <h3>{quarter}</h3>
                    <RankedTopics topics={topics} from={from} />
                  </section>
                ))}
              </div>
            </section>

            <section className="lotcd-section" aria-label="출처와 활동 이력">
              <h2>출처와 활동 이력</h2>
              <div className="lotcd-activity">
                {Array.from(activity, ([week, teams]) => (
                  <section key={week} className="lotcd-activity__week">
                    <h3>{week}</h3>
                    <div>
                      {Array.from(teams, ([team, entries]) => (
                        <section key={team} className="lotcd-activity__team">
                          <h4>{team}</h4>
                          <ol>
                            {entries.map((entry) => (
                              <li key={entry.agenda_id}>
                                <div><strong>{entry.subject}</strong><span>{entry.agenda_id} · {entry.mail_id}</span></div>
                                <blockquote>{entry.source_quote}</blockquote>
                                <small>{entry.source_path ?? '원문 경로 없음'}</small>
                              </li>
                            ))}
                          </ol>
                        </section>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            </section>
          </div>
        </article>
      ) : (
        <section className="lotcd-document lotcd-document--empty">
          <p className="lotcd-document__eyebrow">LOTCD PROJECTION · READ ONLY</p>
          <h1>LOTCD 운영 로그</h1>
          <p>왼쪽 분류 체계에서 Domain, Tech, LOTCD를 차례로 선택하세요.</p>
        </section>
      )}
    </div>
  )
}
