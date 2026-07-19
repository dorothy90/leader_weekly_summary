import type { WikiEvidence } from '../types'

interface EvidenceDrawerProps {
  evidence: WikiEvidence
  onClose: () => void
}

export function EvidenceDrawer({ evidence, onClose }: EvidenceDrawerProps) {
  return (
    <dialog className="evidence-drawer" aria-label="Agenda 근거" open>
      <header className="evidence-drawer__header">
        <div>
          <span className="evidence-drawer__eyebrow">AGENDA / {evidence.agenda_id}</span>
          <h2>{evidence.subject}</h2>
        </div>
        <button type="button" onClick={onClose} aria-label="근거 닫기">닫기</button>
      </header>
      <dl className="evidence-drawer__metadata">
        <div><dt>팀</dt><dd>{evidence.team}</dd></div>
        <div><dt>주차</dt><dd>{evidence.week}</dd></div>
        <div><dt>메일</dt><dd>{evidence.mail_id}</dd></div>
      </dl>
      <section className="evidence-drawer__quote" aria-label="원문 인용">
        <h3>원문 인용</h3>
        <blockquote>{evidence.source_quote}</blockquote>
      </section>
      <dl className="evidence-drawer__source">
        <dt>원본 경로</dt>
        <dd>{evidence.source_path ?? '경로 정보 없음'}</dd>
      </dl>
    </dialog>
  )
}

