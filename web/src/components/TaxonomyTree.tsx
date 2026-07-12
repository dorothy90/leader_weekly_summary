import type { CategoryCount, Selection, Taxonomy } from '../types'

interface TaxonomyTreeProps {
  taxonomy: Taxonomy | null
  counts: CategoryCount[]
  selection: Selection
  scopeMode: 'direct' | 'descendants'
  onSelect: (selection: Selection) => void
}

function pathKey(domain: string, tech?: string | null, lotcd?: string | null) {
  return `${domain}|${tech ?? ''}|${lotcd ?? ''}`
}

export function TaxonomyTree({
  taxonomy,
  counts,
  selection,
  scopeMode,
  onSelect,
}: TaxonomyTreeProps) {
  const countMap = new Map(
    counts.map((item) => [
      pathKey(item.path.domain, item.path.tech, item.path.lotcd),
      scopeMode === 'direct' ? item.direct : item.descendants,
    ]),
  )

  if (!taxonomy) {
    return <div className="tree-loading">분류 기준을 불러오는 중</div>
  }

  return (
    <nav className="taxonomy-tree" aria-label="메일 분류">
      {taxonomy.domains.map((domain) => {
        const domainSelected = selection.domain === domain.name
        return (
          <section className={`tree-domain tree-domain--${domain.id}`} key={domain.id}>
            <button
              type="button"
              className={`tree-row tree-row--domain ${
                domainSelected && !selection.tech ? 'is-selected' : ''
              }`}
              onClick={() =>
                onSelect({ domain: domain.name, tech: null, lotcd: null })
              }
            >
              <span className="tree-domain-mark" aria-hidden="true" />
              <span>{domain.name}</span>
              <span className="tree-count">
                {countMap.get(pathKey(domain.name)) ?? 0}
              </span>
            </button>

            <div className="tree-techs">
              {domain.techs.map((tech) => {
                const techSelected =
                  domainSelected && selection.tech === tech.name
                return (
                  <div className="tree-tech" key={tech.id}>
                    <button
                      type="button"
                      className={`tree-row tree-row--tech ${
                        techSelected && !selection.lotcd ? 'is-selected' : ''
                      }`}
                      onClick={() =>
                        onSelect({
                          domain: domain.name,
                          tech: tech.name,
                          lotcd: null,
                        })
                      }
                    >
                      <span>{tech.name}</span>
                      <span className="tree-count">
                        {countMap.get(pathKey(domain.name, tech.name)) ?? 0}
                      </span>
                    </button>
                    <div className="tree-lotcds">
                      {tech.lotcds.map((lotcd) => (
                        <button
                          type="button"
                          className={`tree-row tree-row--lotcd ${
                            techSelected && selection.lotcd === lotcd.code
                              ? 'is-selected'
                              : ''
                          }`}
                          key={lotcd.code}
                          title={lotcd.product}
                          onClick={() =>
                            onSelect({
                              domain: domain.name,
                              tech: tech.name,
                              lotcd: lotcd.code,
                            })
                          }
                        >
                          <span>{lotcd.code}</span>
                          <span className="tree-product">{lotcd.product}</span>
                          <span className="tree-count">
                            {countMap.get(
                              pathKey(domain.name, tech.name, lotcd.code),
                            ) ?? 0}
                          </span>
                        </button>
                      ))}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        )
      })}
    </nav>
  )
}
