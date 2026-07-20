import type { MouseEvent } from 'react'

import type { WikiEvidence } from '../../types'

interface EvidenceTreeProps {
  evidence: WikiEvidence[]
  onOpen: (evidence: WikiEvidence, trigger: HTMLButtonElement) => void
}

export function EvidenceTree({ evidence, onOpen }: EvidenceTreeProps) {
  const grouped = new Map<string, WikiEvidence[]>()
  for (const item of evidence) grouped.set(item.week, [...(grouped.get(item.week) ?? []), item])
  return (
    <div className="document-evidence-tree">
      {[...grouped.entries()].sort(([a], [b]) => b.localeCompare(a)).map(([week, values]) => <details key={week} open>
        <summary>{week} <span>{values.length}</span></summary>
        {values.map((item) => <article key={item.agenda_id} id={`reference-${item.agenda_id}`} className="document-reference">
          <button type="button" aria-label={`참고문서 ${item.agenda_id} 상세`} onClick={(event: MouseEvent<HTMLButtonElement>) => onOpen(item, event.currentTarget)}>
            <code>{item.agenda_id}</code><span>{item.team}</span><small>{item.subject}</small>
          </button>
          {item.mail_html_available && item.original_mail_url
            ? <a href={item.original_mail_url} target="_blank" rel="noopener noreferrer">원본 메일 보기 ↗</a>
            : <span className="document-reference__unavailable">원본 메일 없음</span>}
        </article>)}
      </details>)}
    </div>
  )
}
