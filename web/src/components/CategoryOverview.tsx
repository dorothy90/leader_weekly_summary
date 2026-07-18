import type { Agenda, Selection, Taxonomy } from '../types'

interface CategoryOverviewProps {
  selection: Selection
  taxonomy: Taxonomy | null
  agendas: Agenda[]
  reviewOnly: boolean
  onSelectAgenda: (agendaId: string) => void
}

const dateFormatter = new Intl.DateTimeFormat('ko-KR', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

export function CategoryOverview({
  selection,
  taxonomy,
  agendas,
  reviewOnly,
  onSelectAgenda,
}: CategoryOverviewProps) {
  const domain = taxonomy?.domains.find((item) => item.name === selection.domain)
  const tech = domain?.techs.find((item) => item.name === selection.tech)
  const lotcd = tech?.lotcds.find((item) => item.code === selection.lotcd)
  const title = reviewOnly
    ? 'Review inbox'
    : lotcd?.code ?? tech?.name ?? domain?.name ?? 'Yield Knowledge Index'
  const path = [selection.domain, selection.tech, selection.lotcd].filter(Boolean)
  const states = new Map<string, number>()
  for (const agenda of agendas) states.set(agenda.state, (states.get(agenda.state) ?? 0) + 1)

  return (
    <article className="category-note">
      <header>
        <span className="category-note__file">
          {reviewOnly ? 'inbox/review.md' : `${path.join('/').toLowerCase() || 'index'}.md`}
        </span>
        <div className="category-note__crumbs">
          {(path.length ? path : ['Index']).map((node) => (
            <span key={node}>[[{node}]]</span>
          ))}
        </div>
        <h1>{title}</h1>
        <p>
          {reviewOnly
            ? '사람의 판단이 필요한 분류 결과를 검토합니다.'
            : lotcd
              ? `${lotcd.product} · Fab ${lotcd.fab_id}의 연결된 메일 지식을 탐색합니다.`
              : '메일에서 축적된 수율 지식을 category와 source 기준으로 탐색합니다.'}
        </p>
      </header>

      {lotcd ? (
        <section className="category-note__properties">
          <h2>Properties</h2>
          <dl>
            <div><dt>domain</dt><dd>{domain?.name}</dd></div>
            <div><dt>tech</dt><dd>{tech?.name}</dd></div>
            <div><dt>lotcd</dt><dd>{lotcd.code}</dd></div>
            <div><dt>fab</dt><dd>{lotcd.fab_id}</dd></div>
            <div><dt>product</dt><dd>{lotcd.product}</dd></div>
            <div><dt>aliases</dt><dd>{lotcd.aliases.join(', ') || '—'}</dd></div>
          </dl>
        </section>
      ) : null}

      <section>
        <div className="category-note__section-title">
          <h2>Current state</h2><span>{agendas.length} linked notes</span>
        </div>
        <div className="category-note__state-line">
          {[...states].map(([state, count]) => (
            <span key={state}><i className={`state-dot state-dot--${state}`} />{state}<strong>{count}</strong></span>
          ))}
        </div>
      </section>

      <section>
        <div className="category-note__section-title">
          <h2>Recent agenda</h2><span>source traceable</span>
        </div>
        <div className="category-note__agendas">
          {(reviewOnly ? agendas : agendas.slice(0, 8)).map((agenda) => (
            <button type="button" onClick={() => onSelectAgenda(agenda.id)} key={agenda.id}>
              <span className={`state-dot state-dot--${agenda.state}`} />
              <span><strong>[[{agenda.summary}]]</strong><small>{agenda.sender_team} · {dateFormatter.format(new Date(agenda.received_at))}</small></span>
              <i>→</i>
            </button>
          ))}
          {agendas.length === 0 ? <p>현재 범위에 연결된 agenda가 없습니다.</p> : null}
        </div>
      </section>

      {!selection.domain && taxonomy ? (
        <section>
          <div className="category-note__section-title"><h2>Vault map</h2><span>2 domains</span></div>
          <div className="category-note__domains">
            {taxonomy.domains.map((item) => (
              <div className={`is-${item.name.toLowerCase()}`} key={item.id}>
                <strong>[[{item.name}]]</strong>
                <span>{item.techs.length} Tech · {item.techs.reduce((sum, value) => sum + value.lotcds.length, 0)} LOTCD</span>
              </div>
            ))}
          </div>
        </section>
      ) : null}
    </article>
  )
}
