import { FormEvent, useEffect, useMemo, useState } from 'react'

import type {
  ClassificationAliasRequest,
  ClassificationItem,
  ClassificationSplitPart,
  DomainName,
  Taxonomy,
} from '../types'

interface Props {
  item: ClassificationItem
  taxonomy: Taxonomy
  canEdit: boolean
  onCorrect: (lotcd: string, reason: string) => Promise<unknown>
  onDisposition: (status: 'aggregate' | 'excluded', reason: string) => Promise<unknown>
  onSplit: (parts: ClassificationSplitPart[], reason: string) => Promise<unknown>
  onCreateAlias: (input: ClassificationAliasRequest) => Promise<unknown>
}

interface LotcdOption {
  code: string
  domain: DomainName
  tech: string
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : '요청을 처리하지 못했습니다.'
}

export function ClassificationItemDetail({
  item, taxonomy, canEdit, onCorrect, onDisposition, onSplit, onCreateAlias,
}: Props) {
  const lotcds = useMemo<LotcdOption[]>(() => taxonomy.domains.flatMap((domain) =>
    domain.techs.flatMap((tech) => tech.lotcds.map((lotcd) => ({
      code: lotcd.code, domain: domain.name, tech: tech.name,
    })))), [taxonomy])
  const initialLotcd = item.decision.target_path?.lotcd ?? ''
  const [lotcd, setLotcd] = useState(initialLotcd)
  const [reason, setReason] = useState('')
  const [dispositionReason, setDispositionReason] = useState('')
  const [aliasValue, setAliasValue] = useState('')
  const [aliasLotcd, setAliasLotcd] = useState(initialLotcd)
  const [aliasContext, setAliasContext] = useState('')
  const [showSplit, setShowSplit] = useState(false)
  const [splitReason, setSplitReason] = useState('')
  const [parts, setParts] = useState<ClassificationSplitPart[]>([
    { source_quote: '', summary: '', lotcd: '' },
    { source_quote: '', summary: '', lotcd: '' },
  ])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const next = item.decision.target_path?.lotcd ?? ''
    setLotcd(next)
    setAliasLotcd(next)
    setError(null)
  }, [item.agenda_id, item.decision.target_path?.lotcd])

  async function perform(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (caught) {
      setError(errorMessage(caught))
    } finally {
      setBusy(false)
    }
  }

  function submitCorrection(event: FormEvent) {
    event.preventDefault()
    if (!lotcd || !reason.trim()) return setError('LOTCD와 수정 사유를 입력하세요.')
    void perform(() => onCorrect(lotcd, reason.trim()))
  }

  function submitDisposition(status: 'aggregate' | 'excluded') {
    if (!dispositionReason.trim()) return setError('처리 사유를 입력하세요.')
    void perform(() => onDisposition(status, dispositionReason.trim()))
  }

  function submitSplit(event: FormEvent) {
    event.preventDefault()
    if (!splitReason.trim() || parts.some((part) => !part.source_quote.trim() || !part.summary.trim() || !part.lotcd)) {
      return setError('두 항목의 원문, 요약, LOTCD와 분할 사유를 입력하세요.')
    }
    void perform(() => onSplit(parts, splitReason.trim()))
  }

  function submitAlias(event: FormEvent) {
    event.preventDefault()
    const target = lotcds.find((entry) => entry.code === aliasLotcd)
    if (!aliasValue.trim() || !target) return setError('유의어 문구와 LOTCD를 입력하세요.')
    void perform(() => onCreateAlias({
      value: aliasValue.trim(), lotcd: target.code, origin_agenda_id: item.agenda_id,
      context_domain: target.domain, context_tech: aliasContext.trim() || target.tech,
    }))
  }

  const selectedPath = item.decision.target_path
  return (
    <aside className="classification-detail" aria-label="분류 상세">
      <header><h2>{item.summary}</h2><small>수정 {item.revision_count}회</small></header>
      <section><h3>원문</h3><blockquote>{item.source_quote}</blockquote><p>{item.classification_context}</p></section>
      <section><h3>현재 경로</h3><p>{selectedPath ? [selectedPath.domain, selectedPath.tech, selectedPath.lotcd].filter(Boolean).join(' / ') : '미분류'}</p></section>
      <section><h3>매칭 근거</h3>{item.decision.matches.length ? <ul>{item.decision.matches.map((match) =>
        <li key={`${match.rule_id}-${match.phrase}`}><strong>{match.phrase}</strong> <code>{match.rule_id}</code> <small>{match.score}</small></li>)}</ul> : <p>매칭 없음</p>}
        {item.decision.diagnostics.length ? <ul>{item.decision.diagnostics.map((code) => <li key={code}><code>{code}</code></li>)}</ul> : null}
      </section>

      {error ? <p role="alert">{error}</p> : null}
      <form onSubmit={submitCorrection}><h3>이 항목만 수정</h3>
        <label>LOTCD<select aria-label="LOTCD" value={lotcd} onChange={(event) => setLotcd(event.target.value)} disabled={!canEdit || busy}><option value="">선택</option>{lotcds.map((entry) => <option key={entry.code} value={entry.code}>{entry.code}</option>)}</select></label>
        <label>수정 사유<input value={reason} onChange={(event) => setReason(event.target.value)} disabled={!canEdit || busy} /></label>
        <button type="submit" disabled={!canEdit || busy}>이 항목만 수정</button>
      </form>

      <section><h3>항목 처리</h3><label>처리 사유<input value={dispositionReason} onChange={(event) => setDispositionReason(event.target.value)} disabled={!canEdit || busy} /></label>
        <div><button type="button" disabled={!canEdit || busy} onClick={() => submitDisposition('aggregate')}>종합지표로 전환</button><button type="button" disabled={!canEdit || busy} onClick={() => submitDisposition('excluded')}>제외</button></div>
      </section>

      <section><button type="button" disabled={!canEdit || busy} onClick={() => setShowSplit((value) => !value)}>항목 분할</button>
        {showSplit ? <form onSubmit={submitSplit}><label>분할 사유<input value={splitReason} onChange={(event) => setSplitReason(event.target.value)} /></label>
          {parts.map((part, index) => <fieldset key={index}><legend>{index + 1}번째 항목</legend>
            <label>{index === 0 ? '첫 번째 원문' : '두 번째 원문'}<input value={part.source_quote} onChange={(event) => setParts((current) => current.map((value, partIndex) => partIndex === index ? { ...value, source_quote: event.target.value } : value))} /></label>
            <label>{index === 0 ? '첫 번째 요약' : '두 번째 요약'}<input value={part.summary} onChange={(event) => setParts((current) => current.map((value, partIndex) => partIndex === index ? { ...value, summary: event.target.value } : value))} /></label>
            <label>{index === 0 ? '첫 번째 LOTCD' : '두 번째 LOTCD'}<select value={part.lotcd} onChange={(event) => setParts((current) => current.map((value, partIndex) => partIndex === index ? { ...value, lotcd: event.target.value } : value))}><option value="">선택</option>{lotcds.map((entry) => <option key={entry.code} value={entry.code}>{entry.code}</option>)}</select></label>
          </fieldset>)}<button type="submit" disabled={!canEdit || busy}>분할 저장</button></form> : null}
      </section>

      <form onSubmit={submitAlias}><h3>유의어 규칙 추가</h3>
        <label>유의어 문구<input value={aliasValue} onChange={(event) => setAliasValue(event.target.value)} disabled={!canEdit || busy} /></label>
        <label>유의어 LOTCD<select value={aliasLotcd} onChange={(event) => setAliasLotcd(event.target.value)} disabled={!canEdit || busy}><option value="">선택</option>{lotcds.map((entry) => <option key={entry.code} value={entry.code}>{entry.code}</option>)}</select></label>
        <label>적용 문맥 (선택)<input value={aliasContext} onChange={(event) => setAliasContext(event.target.value)} disabled={!canEdit || busy} /></label>
        <button type="submit" disabled={!canEdit || busy}>유의어 등록</button>
      </form>
    </aside>
  )
}
