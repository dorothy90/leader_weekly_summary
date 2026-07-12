import { useMemo, useState } from 'react'

import { EvidenceText } from './EvidenceText'
import type {
  Agenda,
  AgendaDetailResponse,
  ClassificationRevision,
  Lotcd,
  Taxonomy,
} from '../types'

interface WikiMetaPaneProps {
  detail: AgendaDetailResponse | null
  agendas: Agenda[]
  taxonomy: Taxonomy | null
  revisions: ClassificationRevision[]
  onSelectAgenda: (agendaId: string) => void
}

function pathKey(path: Agenda['target_paths'][number]) {
  return `${path.domain}|${path.tech ?? ''}|${path.lotcd ?? ''}`
}

function pathLabel(path: Agenda['target_paths'][number]) {
  return [path.domain, path.tech, path.lotcd].filter(Boolean).join(' / ')
}

function findLotcd(taxonomy: Taxonomy | null, code: string | null) {
  if (!taxonomy || !code) return null
  for (const domain of taxonomy.domains) {
    for (const tech of domain.techs) {
      const lotcd = tech.lotcds.find((item) => item.code === code)
      if (lotcd) return { domain: domain.name, tech: tech.name, lotcd }
    }
  }
  return null
}

function findPair(taxonomy: Taxonomy | null, current: Lotcd | null) {
  if (!taxonomy || !current) return null
  for (const domain of taxonomy.domains) {
    for (const tech of domain.techs) {
      const pair = tech.lotcds.find(
        (item) =>
          item.code !== current.code &&
          item.product_code === current.product_code &&
          item.product === current.product,
      )
      if (pair) return { domain: domain.name, tech: tech.name, lotcd: pair }
    }
  }
  return null
}

const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
})

export function WikiMetaPane({
  detail,
  agendas,
  taxonomy,
  revisions,
  onSelectAgenda,
}: WikiMetaPaneProps) {
  const [showSource, setShowSource] = useState(false)
  const related = useMemo(() => {
    if (!detail) return []
    const selectedKeys = new Set(detail.agenda.target_paths.map(pathKey))
    return agendas
      .filter(
        (agenda) =>
          agenda.id !== detail.agenda.id &&
          (agenda.mail_id === detail.agenda.mail_id ||
            agenda.target_paths.some((path) => selectedKeys.has(pathKey(path)))),
      )
      .slice(0, 8)
  }, [agendas, detail])
  const timeline = useMemo(() => {
    const code = detail?.agenda.target_paths.find((path) => path.lotcd)?.lotcd
    if (!code) return []
    return agendas
      .filter((agenda) => agenda.target_paths.some((path) => path.lotcd === code))
      .sort((a, b) => b.received_at.localeCompare(a.received_at))
      .slice(0, 8)
  }, [agendas, detail])

  if (!detail) {
    return (
      <aside className="wiki-meta-pane wiki-meta-pane--empty">
        노트를 선택하면 연결 정보가 표시됩니다.
      </aside>
    )
  }

  const { agenda, mail } = detail
  const primaryCode = agenda.target_paths.find((path) => path.lotcd)?.lotcd ?? null
  const currentLotcd = findLotcd(taxonomy, primaryCode)
  const pair = findPair(taxonomy, currentLotcd?.lotcd ?? null)
  const pairAgenda = pair
    ? agendas.find((item) =>
        item.target_paths.some((path) => path.lotcd === pair.lotcd.code),
      )
    : null

  return (
    <>
    <aside className="wiki-meta-pane">
      <section className="wiki-meta-section">
        <h2>Metrics</h2>
        <div className="wiki-metrics">
          <div><span>Confidence</span><strong>{Math.round(agenda.confidence * 100)}%</strong></div>
          <div><span>Backlinks</span><strong>{related.length}</strong></div>
          <div><span>Sources</span><strong>1</strong></div>
          <div><span>Revisions</span><strong>{revisions.length}</strong></div>
        </div>
      </section>

      <section className="wiki-meta-section">
        <h2>Frontmatter</h2>
        {[
          ['scope', agenda.scope],
          ['topic', agenda.topic],
          ['state', agenda.state],
          ['review', agenda.review_status],
          ['sender', agenda.sender_team],
        ].map(([key, value]) => (
          <div className="wiki-field" key={key}>
            <span>{key}</span><strong>{value}</strong>
          </div>
        ))}
      </section>

      <section className="wiki-meta-section evidence-trail">
        <h2>Evidence trail</h2>
        <button
          type="button"
          onClick={() => setShowSource(true)}
        >
          <i>01</i><span>Source mail<small>{mail.subject}</small></span>
        </button>
        <div aria-hidden="true" />
        <button type="button" onClick={() => document.querySelector('.detail-title')?.scrollIntoView({ behavior: 'smooth' })}>
          <i>02</i><span>Agenda<small>{agenda.summary}</small></span>
        </button>
        {agenda.target_paths.map((path, index) => (
          <div className="evidence-trail__target" key={pathKey(path)}>
            <div aria-hidden="true" />
            <span><i>{String(index + 3).padStart(2, '0')}</i>{pathLabel(path)}</span>
          </div>
        ))}
      </section>

      {pair ? (
        <section className="wiki-meta-section fab-pair">
          <h2>Fab pair</h2>
          <button type="button" disabled={!pairAgenda} onClick={() => pairAgenda && onSelectAgenda(pairAgenda.id)}>
            <code>{currentLotcd?.lotcd.code}</code>
            <span>↔</span>
            <code>{pair.lotcd.code}</code>
            <small>{pair.lotcd.product}</small>
          </button>
        </section>
      ) : null}

      <section className="wiki-meta-section">
        <h2>Backlinks · {related.length}</h2>
        <div className="wiki-backlinks">
          {related.length ? related.map((item) => (
            <button type="button" onClick={() => onSelectAgenda(item.id)} key={item.id}>
              <span>↩</span><strong>{item.summary}</strong>
              <small>{dateFormatter.format(new Date(item.received_at))}</small>
            </button>
          )) : <p>연결된 다른 agenda가 없습니다.</p>}
        </div>
      </section>

      {timeline.length > 1 ? (
        <section className="wiki-meta-section">
          <h2>Timeline · {primaryCode}</h2>
          <div className="wiki-timeline">
            {timeline.map((item) => (
              <button type="button" onClick={() => onSelectAgenda(item.id)} key={item.id}>
                <span className={`state-dot state-dot--${item.state}`} />
                <div><strong>{item.summary}</strong><small>{dateFormatter.format(new Date(item.received_at))}</small></div>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      {revisions.length ? (
        <section className="wiki-meta-section">
          <h2>History</h2>
          {revisions.map((revision) => (
            <div className="wiki-history" key={revision.id}>
              <span />
              <div><strong>{revision.changed_by}</strong><small>{dateFormatter.format(new Date(revision.changed_at))}</small></div>
            </div>
          ))}
        </section>
      ) : null}
    </aside>
    {showSource ? (
      <div className="source-modal-backdrop" role="presentation" onMouseDown={() => setShowSource(false)}>
        <section className="source-modal" role="dialog" aria-modal="true" aria-label="원본 메일" onMouseDown={(event) => event.stopPropagation()}>
          <header>
            <div><span>Source mail</span><strong>{mail.subject}</strong></div>
            <button type="button" onClick={() => setShowSource(false)} aria-label="원본 메일 닫기">×</button>
          </header>
          <dl>
            <div><dt>sender</dt><dd>{mail.sender}</dd></div>
            <div><dt>team</dt><dd>{mail.sender_team}</dd></div>
            <div><dt>received</dt><dd>{dateFormatter.format(new Date(mail.received_at))}</dd></div>
          </dl>
          <div className="source-modal__body">
            <EvidenceText body={mail.body} quote={agenda.source_quote} />
          </div>
        </section>
      </div>
    ) : null}
    </>
  )
}
