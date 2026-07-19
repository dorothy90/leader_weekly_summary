import { useState, type Dispatch, type SetStateAction } from 'react'
import { Link } from 'react-router-dom'

import type { CategoryPath, Taxonomy } from '../types'

interface TaxonomyTreeProps {
  taxonomy: Taxonomy
  currentPath?: CategoryPath
}

export function TaxonomyTree({ taxonomy, currentPath }: TaxonomyTreeProps) {
  const [openDomains, setOpenDomains] = useState<Set<string>>(
    () => new Set(currentPath ? [currentPath.domain] : []),
  )
  const [openTechs, setOpenTechs] = useState<Set<string>>(
    () => new Set(currentPath?.tech ? [`${currentPath.domain}/${currentPath.tech}`] : []),
  )

  function toggle(setter: Dispatch<SetStateAction<Set<string>>>, key: string) {
    setter((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <nav className="taxonomy-tree" aria-label="LOTCD 분류 체계">
      <div className="taxonomy-tree__header">
        <span>분류 체계</span>
        <small>RULESET v{taxonomy.version}</small>
      </div>
      {taxonomy.notice ? <p className="taxonomy-tree__notice">{taxonomy.notice}</p> : null}
      <ul className="taxonomy-tree__domains">
        {taxonomy.domains.map((domain) => {
          const domainOpen = openDomains.has(domain.name)
          return (
            <li key={domain.id}>
              <button
                type="button"
                className="taxonomy-tree__branch taxonomy-tree__branch--domain"
                aria-expanded={domainOpen}
                aria-label={`${domain.name} ${domainOpen ? '접기' : '펼치기'}`}
                onClick={() => toggle(setOpenDomains, domain.name)}
              >
                <span aria-hidden="true">{domainOpen ? '−' : '+'}</span>
                <strong>{domain.name}</strong>
                <small>{domain.techs.length} TECH</small>
              </button>
              {domainOpen ? (
                <ul className="taxonomy-tree__techs">
                  {domain.techs.map((tech) => {
                    const techKey = `${domain.name}/${tech.name}`
                    const techOpen = openTechs.has(techKey)
                    return (
                      <li key={tech.id}>
                        <button
                          type="button"
                          className="taxonomy-tree__branch"
                          aria-expanded={techOpen}
                          aria-label={`${tech.name} ${techOpen ? '접기' : '펼치기'}`}
                          onClick={() => toggle(setOpenTechs, techKey)}
                        >
                          <span aria-hidden="true">{techOpen ? '−' : '+'}</span>
                          <strong>{tech.name}</strong>
                          <small>{tech.lotcds.length}</small>
                        </button>
                        {techOpen ? (
                          <ul className="taxonomy-tree__lotcds">
                            {tech.lotcds.map((lotcd) => {
                              const isCurrent = currentPath?.domain === domain.name
                                && currentPath.tech === tech.name
                                && currentPath.lotcd === lotcd.code
                              return (
                                <li key={lotcd.code}>
                                  <Link
                                    to={`/wiki/lotcd/${encodeURIComponent(domain.name)}/${encodeURIComponent(tech.name)}/${encodeURIComponent(lotcd.code)}`}
                                    aria-current={isCurrent ? 'page' : undefined}
                                  >
                                    <strong>{lotcd.code}</strong>
                                    <span>{lotcd.product}</span>
                                  </Link>
                                </li>
                              )
                            })}
                          </ul>
                        ) : null}
                      </li>
                    )
                  })}
                </ul>
              ) : null}
            </li>
          )
        })}
      </ul>
    </nav>
  )
}
