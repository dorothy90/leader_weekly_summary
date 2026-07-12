import type { Agenda, WikiCitationDetail } from '../types'
import { EvidenceText } from './EvidenceText'

interface WikiCitationDrawerProps {
  detail: WikiCitationDetail | null
  loading: boolean
  error: string | null
  onClose: () => void
}

function pathLabel(path: Agenda['target_paths'][number]) {
  return [path.domain, path.tech, path.lotcd].filter(Boolean).join(' / ')
}

export function WikiCitationDrawer({
  detail,
  loading,
  error,
  onClose,
}: WikiCitationDrawerProps) {
  if (!detail && !loading && !error) {
    return null
  }

  return (
    <div
      className="wiki-citation-drawer-backdrop"
      role="presentation"
      style={{ position: 'fixed', inset: 0 }}
      onMouseDown={onClose}
    >
      <aside
        className="wiki-citation-drawer"
        role="dialog"
        aria-label="메일 근거"
        aria-modal="true"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header>
          <div>
            <span>메일 근거</span>
            <strong>{detail?.mail.subject ?? '메일 근거'}</strong>
          </div>
          <button type="button" onClick={onClose} aria-label="메일 근거 닫기">
            ×
          </button>
        </header>

        {loading ? (
          <p>메일 근거 불러오는 중</p>
        ) : error ? (
          <p>{error}</p>
        ) : detail ? (
          <div className="wiki-citation-drawer__content">
            <section>
              <h2>메일 정보</h2>
              <dl>
                <div><dt>보낸 팀</dt><dd>{detail.mail.sender_team}</dd></div>
                <div><dt>보낸 사람</dt><dd>{detail.mail.sender}</dd></div>
                <div><dt>받은 날짜</dt><dd>{detail.mail.received_at}</dd></div>
              </dl>
            </section>

            <section>
              <h2>사용된 섹션</h2>
              <ul>
                {detail.used_in_sections.map((section) => <li key={section}>{section}</li>)}
              </ul>
            </section>

            <section>
              <h2>지원 Agenda</h2>
              {detail.agendas.map((agenda) => (
                <article key={agenda.id}>
                  <header>
                    <strong>{agenda.summary}</strong>
                    <span>{agenda.state}</span>
                  </header>
                  <div className="wiki-citation-drawer__paths">
                    {agenda.target_paths.length > 0 ? (
                      agenda.target_paths.map((path) => {
                        const label = pathLabel(path)
                        return <span className="path-chip" key={`${agenda.id}:${label}`}>{label}</span>
                      })
                    ) : (
                      <span className="path-chip path-chip--unknown">미분류</span>
                    )}
                  </div>
                  <EvidenceText body={detail.mail.body} quote={agenda.source_quote} />
                </article>
              ))}
            </section>
          </div>
        ) : null}
      </aside>
    </div>
  )
}
