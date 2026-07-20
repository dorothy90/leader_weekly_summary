import { useEffect, useRef } from 'react'
import type { RefObject } from 'react'

import type { WikiEvidence } from '../types'

interface EvidenceDrawerProps {
  evidence: WikiEvidence
  onClose: () => void
  triggerRef: RefObject<HTMLButtonElement | null>
}

export function EvidenceDrawer({ evidence, onClose, triggerRef }: EvidenceDrawerProps) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const closeButtonRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (dialogRef.current && !dialogRef.current.open) dialogRef.current.showModal()
    closeButtonRef.current?.focus()
  }, [])

  function closeDrawer() {
    dialogRef.current?.close()
    onClose()
    triggerRef.current?.focus()
  }

  return (
    <dialog
      ref={dialogRef}
      className="evidence-drawer"
      aria-label="Agenda 근거"
      onCancel={(event) => {
        event.preventDefault()
        closeDrawer()
      }}
    >
      <header className="evidence-drawer__header">
        <div>
          <span className="evidence-drawer__eyebrow">AGENDA / {evidence.agenda_id}</span>
          <h2>{evidence.subject}</h2>
        </div>
        <button ref={closeButtonRef} type="button" onClick={closeDrawer} aria-label="근거 닫기">닫기</button>
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
      <footer className="evidence-drawer__source">
        {evidence.mail_html_available && evidence.original_mail_url
          ? <a href={evidence.original_mail_url} target="_blank" rel="noopener noreferrer">원본 메일을 새 탭에서 보기 ↗</a>
          : <span>보관된 원본 HTML이 없습니다.</span>}
      </footer>
    </dialog>
  )
}
