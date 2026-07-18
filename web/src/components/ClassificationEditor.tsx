import { FormEvent, useState } from 'react'

import type { CategoryPath, DomainName, Taxonomy } from '../types'

interface ClassificationEditorProps {
  taxonomy: Taxonomy
  candidate: CategoryPath | null
  initialPaths: CategoryPath[]
  allowHold: boolean
  onConfirm: (paths: CategoryPath[]) => Promise<void>
  onHold: () => Promise<void>
  onCancel: () => void
}

function pathKey(path: CategoryPath) {
  return `${path.domain}|${path.tech ?? ''}|${path.lotcd ?? ''}`
}

function pathLabel(path: CategoryPath) {
  return [path.domain, path.tech, path.lotcd].filter(Boolean).join(' / ')
}

export function ClassificationEditor({
  taxonomy,
  candidate,
  initialPaths,
  allowHold,
  onConfirm,
  onHold,
  onCancel,
}: ClassificationEditorProps) {
  const initialPath = initialPaths[0] ?? candidate
  const [domain, setDomain] = useState<DomainName>(
    initialPath?.domain ?? taxonomy.domains[0].name,
  )
  const [tech, setTech] = useState(initialPath?.tech ?? '')
  const [lotcd, setLotcd] = useState(initialPath?.lotcd ?? '')
  const [selectedPaths, setSelectedPaths] = useState<CategoryPath[]>(initialPaths)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const selectedDomain = taxonomy.domains.find((item) => item.name === domain)
  const selectedTech = selectedDomain?.techs.find((item) => item.name === tech)

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    let paths = selectedPaths
    if (paths.length === 0 && tech && lotcd) {
      paths = [{ domain, tech, lotcd }]
    }
    if (paths.length === 0) {
      setError('분류 경로를 하나 이상 추가하세요.')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onConfirm(paths)
    } catch {
      setError('분류를 저장하지 못했습니다.')
      setSaving(false)
    }
  }

  function addPath() {
    const next: CategoryPath = {
      domain,
      tech: tech || null,
      lotcd: lotcd || null,
    }
    setSelectedPaths((current) =>
      current.some((path) => pathKey(path) === pathKey(next))
        ? current
        : [...current, next],
    )
    setError(null)
  }

  async function hold() {
    setSaving(true)
    setError(null)
    try {
      await onHold()
    } catch {
      setError('보류 상태를 저장하지 못했습니다.')
      setSaving(false)
    }
  }

  return (
    <form className="classification-editor" onSubmit={submit}>
      <div className="classification-editor__heading">
        <span>Classification review</span>
        <strong>{initialPaths.length ? '분류 경로 수정' : '미등록 LOTCD 확인'}</strong>
      </div>
      {candidate?.lotcd ? (
        <div className="candidate-code">
          원문 후보 <code>{candidate.lotcd}</code>
        </div>
      ) : null}
      <label>
        <span>대분류</span>
        <select
          value={domain}
          onChange={(event) => {
            setDomain(event.target.value as DomainName)
            setTech('')
            setLotcd('')
          }}
        >
          {taxonomy.domains.map((item) => (
            <option value={item.name} key={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>Tech</span>
        <select
          value={tech}
          onChange={(event) => {
            setTech(event.target.value)
            setLotcd('')
          }}
        >
          <option value="">선택</option>
          {selectedDomain?.techs.map((item) => (
            <option value={item.name} key={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      <label>
        <span>LOTCD</span>
        <select
          value={lotcd}
          onChange={(event) => setLotcd(event.target.value)}
          disabled={!selectedTech}
        >
          <option value="">선택</option>
          {selectedTech?.lotcds.map((item) => (
            <option value={item.code} key={item.code}>
              {item.code} · {item.product}
            </option>
          ))}
        </select>
      </label>
      <button className="classification-editor__add" type="button" onClick={addPath}>
        경로 추가
      </button>
      {selectedPaths.length > 0 ? (
        <div className="classification-editor__paths">
          {selectedPaths.map((path) => (
            <span key={pathKey(path)}>
              {pathLabel(path)}
              <button
                type="button"
                aria-label={`${pathLabel(path)} 제거`}
                onClick={() =>
                  setSelectedPaths((current) =>
                    current.filter((item) => pathKey(item) !== pathKey(path)),
                  )
                }
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
      <div className="classification-editor__actions">
        {allowHold ? (
          <button type="button" onClick={() => void hold()} disabled={saving}>
            보류
          </button>
        ) : (
          <button type="button" onClick={onCancel} disabled={saving}>
            취소
          </button>
        )}
        <button type="submit" disabled={saving}>
          {saving ? '저장 중' : '분류 확정'}
        </button>
      </div>
    </form>
  )
}
