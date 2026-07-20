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
        {values.map((item) => <button key={item.agenda_id} type="button" onClick={(event: MouseEvent<HTMLButtonElement>) => onOpen(item, event.currentTarget)}>
          <code>{item.agenda_id}</code><span>{item.team}</span><small>{item.subject}</small>
        </button>)}
      </details>)}
    </div>
  )
}
