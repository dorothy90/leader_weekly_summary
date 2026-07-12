import { useMemo } from 'react'

import type { Agenda, ScopeMode, Selection, Taxonomy } from '../types'
import { ScopeToggle } from './ScopeToggle'

interface CategoryMetaPaneProps {
  selection: Selection
  taxonomy: Taxonomy | null
  agendas: Agenda[]
  scopeMode: ScopeMode
  onChangeScope: (scope: ScopeMode) => void
  onSelectAgenda: (agendaId: string) => void
}

const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

export function CategoryMetaPane({
  selection,
  taxonomy,
  agendas,
  scopeMode,
  onChangeScope,
  onSelectAgenda,
}: CategoryMetaPaneProps) {
  const domain = taxonomy?.domains.find((item) => item.name === selection.domain)
  const tech = domain?.techs.find((item) => item.name === selection.tech)
  const lotcd = tech?.lotcds.find((item) => item.code === selection.lotcd)
  const sources = new Set(agendas.map((agenda) => agenda.mail_id)).size
  const averageConfidence = agendas.length
    ? Math.round(agendas.reduce((sum, agenda) => sum + agenda.confidence, 0) / agendas.length * 100)
    : 0
  const level = lotcd ? 'LOTCD' : tech ? 'Tech' : 'Domain'
  const latest = agendas[0]?.received_at
  const recent = useMemo(() => agendas.slice(0, 8), [agendas])

  return (
    <aside className="wiki-meta-pane">
      <section className="wiki-meta-section">
        <h2>Metrics</h2>
        <div className="wiki-metrics">
          <div><span>Linked notes</span><strong>{agendas.length}</strong></div>
          <div><span>Sources</span><strong>{sources}</strong></div>
          <div><span>Confidence</span><strong>{averageConfidence}%</strong></div>
          <div><span>Last active</span><strong>{latest ? dateFormatter.format(new Date(latest)) : '—'}</strong></div>
        </div>
      </section>

      <section className="wiki-meta-section">
        <h2>Frontmatter</h2>
        {[
          ['level', level],
          ['domain', domain?.name],
          ['tech', tech?.name],
          ['lotcd', lotcd?.code],
          ['fab', lotcd?.fab_id],
          ['product', lotcd?.product],
        ].filter(([, value]) => value).map(([key, value]) => (
          <div className="wiki-field" key={key}>
            <span>{key}</span><strong>{value}</strong>
          </div>
        ))}
      </section>

      <section className="wiki-meta-section wiki-scope-section">
        <h2>Agenda scope</h2>
        <ScopeToggle value={scopeMode} onChange={onChangeScope} disabled={false} />
      </section>

      <section className="wiki-meta-section">
        <h2>Linked notes · {recent.length}</h2>
        <div className="wiki-backlinks">
          {recent.map((agenda) => (
            <button type="button" onClick={() => onSelectAgenda(agenda.id)} key={agenda.id}>
              <span>↩</span><strong>{agenda.summary}</strong>
              <small>{agenda.sender_team} · {dateFormatter.format(new Date(agenda.received_at))}</small>
            </button>
          ))}
          {recent.length === 0 ? <p>연결된 agenda가 없습니다.</p> : null}
        </div>
      </section>
    </aside>
  )
}
