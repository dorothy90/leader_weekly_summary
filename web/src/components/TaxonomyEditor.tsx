import { FormEvent, useState } from 'react'

import {
  createLotcd,
  createTech,
  deleteTech,
  updateLotcd,
  updateTech,
} from '../api/knowledge'
import type { DomainName, Lotcd, Taxonomy, Tech } from '../types'

export interface EditableLotcd {
  domain: DomainName
  tech: string
  lotcd: Lotcd
}

interface TaxonomyEditorProps {
  taxonomy: Taxonomy
  editing: EditableLotcd | null
  onSaved: (taxonomy: Taxonomy, message: string) => void
  onCancel: () => void
}

function aliasesFrom(value: FormDataEntryValue | null) {
  return String(value ?? '')
    .split(',')
    .map((alias) => alias.trim())
    .filter(Boolean)
}

export function TaxonomyEditor({
  taxonomy,
  editing,
  onSaved,
  onCancel,
}: TaxonomyEditorProps) {
  const [domain, setDomain] = useState<DomainName>(editing?.domain ?? 'DRAM')
  const [tech, setTech] = useState(editing?.tech ?? '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [editingTech, setEditingTech] = useState<{
    domain: DomainName
    tech: Tech
  } | null>(null)
  const [deleteConfirmTechId, setDeleteConfirmTechId] = useState<string | null>(null)
  const selectedDomain = taxonomy.domains.find((item) => item.name === domain)

  async function saveLotcd(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setSaving(true)
    setError(null)
    try {
      const metadata = {
        fab_id: String(form.get('fab_id') ?? '').trim(),
        product_code: String(form.get('product_code') ?? '').trim(),
        product: String(form.get('product') ?? '').trim(),
        aliases: aliasesFrom(form.get('aliases')),
      }
      const next = editing
        ? await updateLotcd(editing.lotcd.code, metadata)
        : await createLotcd({
            domain,
            tech,
            code: String(form.get('code') ?? '').trim().toUpperCase(),
            ...metadata,
          })
      onSaved(next, editing ? 'LOTCD 변경 완료' : 'LOTCD 추가 완료')
    } catch (saveError) {
      setError(
        saveError instanceof Error && saveError.message.startsWith('409:')
          ? '이미 등록된 LOTCD입니다.'
          : 'LOTCD를 저장하지 못했습니다.',
      )
      setSaving(false)
    }
  }

  async function saveTech(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    setSaving(true)
    setError(null)
    try {
      const name = String(form.get('name') ?? '').trim()
      const aliases = aliasesFrom(form.get('aliases'))
      const next = editingTech
        ? await updateTech(editingTech.tech.id, { name, aliases })
        : await createTech({
            domain: String(form.get('domain')) as DomainName,
            id: String(form.get('id') ?? '').trim().toLowerCase(),
            name,
            aliases,
          })
      setEditingTech(null)
      onSaved(next, `Tech “${name}” ${editingTech ? '변경' : '추가'} 완료`)
    } catch {
      setError('Tech를 저장하지 못했습니다. ID와 중복 여부를 확인하세요.')
      setSaving(false)
    }
  }

  async function removeTech(item: Tech) {
    if (deleteConfirmTechId !== item.id) {
      setDeleteConfirmTechId(item.id)
      setError(`Tech “${item.name}” 삭제를 다시 눌러 확인하세요.`)
      return
    }
    try {
      const next = await deleteTech(item.id)
      setDeleteConfirmTechId(null)
      onSaved(next, `Tech “${item.name}” 삭제 완료`)
    } catch (deleteError) {
      setDeleteConfirmTechId(null)
      setError(
        deleteError instanceof Error && deleteError.message.startsWith('409:')
          ? '하위 LOTCD 또는 연결된 agenda가 있어 Tech를 삭제할 수 없습니다.'
          : 'Tech를 삭제하지 못했습니다.',
      )
    }
  }

  return (
    <div className="taxonomy-editor">
      <form className="lotcd-form" onSubmit={saveLotcd}>
        <div className="taxonomy-editor__title">
          <strong>{editing ? `${editing.lotcd.code} 수정` : 'LOTCD 추가'}</strong>
          {editing ? <button type="button" onClick={onCancel}>취소</button> : null}
        </div>
        <label>
          <span>대분류</span>
          <select
            value={domain}
            disabled={Boolean(editing)}
            onChange={(event) => {
              setDomain(event.target.value as DomainName)
              setTech('')
            }}
          >
            {taxonomy.domains.map((item) => (
              <option value={item.name} key={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>Tech</span>
          <select
            value={tech}
            disabled={Boolean(editing)}
            required
            onChange={(event) => setTech(event.target.value)}
          >
            <option value="">선택</option>
            {selectedDomain?.techs.map((item) => (
              <option value={item.name} key={item.id}>{item.name}</option>
            ))}
          </select>
        </label>
        <label>
          <span>LOTCD</span>
          <input
            name="code"
            defaultValue={editing?.lotcd.code ?? ''}
            disabled={Boolean(editing)}
            required
          />
        </label>
        <label>
          <span>Fab ID</span>
          <input name="fab_id" defaultValue={editing?.lotcd.fab_id ?? ''} required />
        </label>
        <label>
          <span>제품 코드</span>
          <input
            name="product_code"
            defaultValue={editing?.lotcd.product_code ?? ''}
            required
          />
        </label>
        <label className="lotcd-form__product">
          <span>제품</span>
          <input name="product" defaultValue={editing?.lotcd.product ?? ''} required />
        </label>
        <label className="lotcd-form__aliases">
          <span>Alias · 쉼표 구분</span>
          <input name="aliases" defaultValue={editing?.lotcd.aliases.join(', ') ?? ''} />
        </label>
        <button type="submit" disabled={saving}>
          {saving ? '저장 중' : editing ? '변경 저장' : 'LOTCD 추가'}
        </button>
      </form>

      {!editing ? (
        <div className="tech-admin">
          <div className="tech-admin__list">
            {taxonomy.domains.flatMap((itemDomain) =>
              itemDomain.techs.map((itemTech) => (
                <span key={itemTech.id}>
                  <code>{itemDomain.name}</code> {itemTech.name}
                  <button
                    type="button"
                    onClick={() => {
                      setEditingTech({ domain: itemDomain.name, tech: itemTech })
                      setError(null)
                    }}
                  >
                    수정
                  </button>
                  <button
                    type="button"
                    className={deleteConfirmTechId === itemTech.id ? 'is-confirming' : ''}
                    onClick={() => void removeTech(itemTech)}
                  >
                    {deleteConfirmTechId === itemTech.id ? '삭제 확인' : '삭제'}
                  </button>
                </span>
              )),
            )}
          </div>
          {editingTech ? (
            <form className="tech-form" onSubmit={saveTech}>
              <input value={editingTech.domain} aria-label="Tech 대분류" disabled />
              <input value={editingTech.tech.id} aria-label="Tech ID" disabled />
              <input name="name" aria-label="Tech 이름" defaultValue={editingTech.tech.name} required />
              <input name="aliases" aria-label="Tech Alias" defaultValue={editingTech.tech.aliases.join(', ')} />
              <button type="submit" disabled={saving}>Tech 변경 저장</button>
              <button type="button" onClick={() => setEditingTech(null)}>취소</button>
            </form>
          ) : (
            <details className="tech-form-wrap">
              <summary>새 Tech 추가</summary>
              <form className="tech-form" onSubmit={saveTech}>
                <select name="domain" aria-label="Tech 대분류">
                  <option value="DRAM">DRAM</option>
                  <option value="NAND">NAND</option>
                </select>
                <input name="id" placeholder="stable ID · orion" required />
                <input name="name" placeholder="Tech 이름" required />
                <input name="aliases" placeholder="Alias · 쉼표 구분" />
                <button type="submit" disabled={saving}>Tech 추가</button>
              </form>
            </details>
          )}
        </div>
      ) : null}
      {error ? <p role="alert">{error}</p> : null}
    </div>
  )
}
