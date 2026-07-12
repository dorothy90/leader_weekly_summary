import { FormEvent, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import {
  createAlias,
  deleteAlias,
  deleteLotcd,
  fetchAliases,
  fetchMappingRevisions,
  fetchSession,
  fetchTaxonomy,
  updateAlias,
} from '../api/knowledge'
import { AppBrand } from '../components/AppBrand'
import {
  TaxonomyEditor,
  type EditableLotcd,
} from '../components/TaxonomyEditor'
import type {
  AliasRecord,
  CategoryPath,
  MappingRevision,
  KnowledgeSession,
  Taxonomy,
} from '../types'

function pathLabel(path: CategoryPath) {
  return [path.domain, path.tech, path.lotcd].filter(Boolean).join(' / ')
}

export function MappingPage() {
  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null)
  const [aliases, setAliases] = useState<AliasRecord[]>([])
  const [selectedCodes, setSelectedCodes] = useState<Set<string>>(new Set())
  const [aliasValue, setAliasValue] = useState('')
  const [editingAliasId, setEditingAliasId] = useState<number | null>(null)
  const [mappingRevisions, setMappingRevisions] = useState<MappingRevision[]>([])
  const [session, setSession] = useState<KnowledgeSession | null>(null)
  const [editingLotcd, setEditingLotcd] = useState<EditableLotcd | null>(null)
  const [deleteConfirmCode, setDeleteConfirmCode] = useState<string | null>(null)
  const [taxonomyEditorVersion, setTaxonomyEditorVersion] = useState(0)
  const [deleteConfirmAliasId, setDeleteConfirmAliasId] = useState<number | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchTaxonomy(controller.signal),
      fetchAliases(controller.signal),
      fetchSession(controller.signal),
    ])
      .then(([taxonomyData, aliasData, sessionData]) => {
        setTaxonomy(taxonomyData)
        setAliases(aliasData)
        setSession(sessionData)
      })
      .catch(() => setMessage('Mapping 데이터를 불러오지 못했습니다.'))
    return () => controller.abort()
  }, [])

  function toggleCode(code: string) {
    setSelectedCodes((current) => {
      const next = new Set(current)
      if (next.has(code)) next.delete(code)
      else next.add(code)
      return next
    })
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!taxonomy) return
    const value = aliasValue.trim()
    if (!value || selectedCodes.size === 0) {
      setMessage('Alias와 연결할 LOTCD를 선택하세요.')
      return
    }

    const targetPaths: CategoryPath[] = []
    for (const domain of taxonomy.domains) {
      for (const tech of domain.techs) {
        for (const lotcd of tech.lotcds) {
          if (selectedCodes.has(lotcd.code)) {
            targetPaths.push({
              domain: domain.name,
              tech: tech.name,
              lotcd: lotcd.code,
            })
          }
        }
      }
    }

    setSaving(true)
    setMessage(null)
    try {
      const saved = editingAliasId
        ? await updateAlias(editingAliasId, value, targetPaths)
        : await createAlias(value, targetPaths)
      setAliases((current) =>
        editingAliasId
          ? current.map((item) => (item.id === saved.id ? saved : item))
          : [...current, saved],
      )
      setSelectedCodes(new Set())
      setAliasValue('')
      setEditingAliasId(null)
      setMappingRevisions([])
      setMessage(`Alias “${saved.value}” 저장 완료`)
    } catch (error) {
      setMessage(error instanceof Error && error.message === 'alias_conflict'
        ? '이미 등록된 Alias입니다.'
        : 'Alias를 저장하지 못했습니다.')
    } finally {
      setSaving(false)
    }
  }

  async function startEditing(alias: AliasRecord) {
    setEditingAliasId(alias.id)
    setAliasValue(alias.value)
    setSelectedCodes(
      new Set(
        alias.target_paths
          .map((path) => path.lotcd)
          .filter((code): code is string => Boolean(code)),
      ),
    )
    setMessage(null)
    try {
      setMappingRevisions(await fetchMappingRevisions(alias.id))
    } catch {
      setMappingRevisions([])
    }
  }

  function cancelEditing() {
    setEditingAliasId(null)
    setMappingRevisions([])
    setAliasValue('')
    setSelectedCodes(new Set())
  }

  function taxonomySaved(next: Taxonomy, nextMessage: string) {
    setTaxonomy(next)
    setEditingLotcd(null)
    setDeleteConfirmCode(null)
    setTaxonomyEditorVersion((version) => version + 1)
    setMessage(nextMessage)
    void fetchAliases().then(setAliases)
  }

  async function removeLotcd(code: string) {
    if (deleteConfirmCode !== code) {
      setDeleteConfirmCode(code)
      setMessage(`${code} 삭제를 다시 눌러 확인하세요.`)
      return
    }
    try {
      taxonomySaved(await deleteLotcd(code), `LOTCD “${code}” 삭제 완료`)
    } catch (deleteError) {
      setMessage(
        deleteError instanceof Error && deleteError.message.startsWith('409:')
          ? '연결된 agenda가 있어 삭제할 수 없습니다.'
          : 'LOTCD를 삭제하지 못했습니다.',
      )
      setDeleteConfirmCode(null)
    }
  }

  async function removeAlias(alias: AliasRecord) {
    if (deleteConfirmAliasId !== alias.id) {
      setDeleteConfirmAliasId(alias.id)
      setMessage(`Alias “${alias.value}” 삭제를 다시 눌러 확인하세요.`)
      return
    }
    try {
      await deleteAlias(alias.id)
      setAliases((current) => current.filter((item) => item.id !== alias.id))
      setDeleteConfirmAliasId(null)
      setMessage(`Alias “${alias.value}” 삭제 완료`)
    } catch {
      setDeleteConfirmAliasId(null)
      setMessage('Alias를 삭제하지 못했습니다.')
    }
  }

  return (
    <div className="app-shell mapping-shell">
      <header className="app-header mapping-header">
        <AppBrand />
        <div className="mapping-header-title">
          <strong>Mapping Admin</strong>
          <span>canonical category · alias</span>
        </div>
        <Link className="review-link" to="/explorer">
          Explorer로 돌아가기
        </Link>
      </header>

      <main className="mapping-page">
        <section className="mapping-catalog">
          <div className="mapping-section-title">
            <span>Canonical hierarchy</span>
            <strong>LOTCD 기준정보</strong>
          </div>
          {taxonomy && session?.can_edit ? (
            <TaxonomyEditor
              key={`${editingLotcd?.lotcd.code ?? 'new'}-${taxonomyEditorVersion}`}
              taxonomy={taxonomy}
              editing={editingLotcd}
              onSaved={taxonomySaved}
              onCancel={() => setEditingLotcd(null)}
            />
          ) : null}
          <div className="mapping-table-wrap">
            <table className="mapping-table">
              <thead>
                <tr>
                  <th>대분류</th>
                  <th>Tech</th>
                  <th>LOTCD</th>
                  <th>Fab</th>
                  <th>제품</th>
                  <th>관리</th>
                </tr>
              </thead>
              <tbody>
                {taxonomy?.domains.flatMap((domain) =>
                  domain.techs.flatMap((tech) =>
                    tech.lotcds.map((lotcd) => (
                      <tr key={lotcd.code}>
                        <td><span className={`domain-mini domain-mini--${domain.id}`}>{domain.name}</span></td>
                        <td>{tech.name}</td>
                        <td><code>{lotcd.code}</code></td>
                        <td>{lotcd.fab_id}</td>
                        <td>{lotcd.product}</td>
                        <td className="mapping-actions">
                          {session?.can_edit ? (
                            <>
                              <button
                                type="button"
                                onClick={() => {
                                  setEditingLotcd({ domain: domain.name, tech: tech.name, lotcd })
                                  setDeleteConfirmCode(null)
                                }}
                              >
                                수정
                              </button>
                              <button
                                type="button"
                                className={deleteConfirmCode === lotcd.code ? 'is-confirming' : ''}
                                onClick={() => void removeLotcd(lotcd.code)}
                              >
                                {deleteConfirmCode === lotcd.code ? '삭제 확인' : '삭제'}
                              </button>
                            </>
                          ) : '—'}
                        </td>
                      </tr>
                    )),
                  ),
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="alias-manager">
          <div className="mapping-section-title">
            <span>Resolver dictionary</span>
            <strong>Alias 관리</strong>
          </div>

          {session?.can_edit ? (
          <form className="alias-form" onSubmit={submit}>
            <label>
              <span>{editingAliasId ? 'Alias 수정' : '새 Alias'}</span>
              <input
                name="alias"
                value={aliasValue}
                onChange={(event) => setAliasValue(event.target.value)}
                placeholder="예: SP 24G"
                autoComplete="off"
              />
            </label>
            <fieldset>
              <legend>연결 LOTCD</legend>
              {taxonomy?.domains.map((domain) => (
                <div className="alias-domain-group" key={domain.id}>
                  <strong>{domain.name}</strong>
                  <div>
                    {domain.techs.flatMap((tech) =>
                      tech.lotcds.map((lotcd) => (
                        <label key={lotcd.code}>
                          <input
                            type="checkbox"
                            checked={selectedCodes.has(lotcd.code)}
                            onChange={() => toggleCode(lotcd.code)}
                          />
                          <code>{lotcd.code}</code>
                        </label>
                      )),
                    )}
                  </div>
                </div>
              ))}
            </fieldset>
            <button type="submit" disabled={saving}>
              {saving ? '저장 중' : editingAliasId ? '변경 저장' : 'Alias 추가'}
            </button>
            {editingAliasId ? (
              <button className="alias-cancel" type="button" onClick={cancelEditing}>
                수정 취소
              </button>
            ) : null}
            {message ? <p role="status">{message}</p> : null}
            {editingAliasId && mappingRevisions.length > 0 ? (
              <div className="mapping-revisions">
                <strong>변경 이력</strong>
                {mappingRevisions.map((revision) => (
                  <span key={revision.id}>
                    {revision.changed_by} ·{' '}
                    {new Intl.DateTimeFormat('ko-KR', {
                      dateStyle: 'short',
                      timeStyle: 'short',
                    }).format(new Date(revision.changed_at))}
                  </span>
                ))}
              </div>
            ) : null}
          </form>
          ) : (
            <div className="mapping-readonly">
              조회 권한으로 접속했습니다. Alias 변경은 knowledge-editor role이 필요합니다.
            </div>
          )}

          <div className="alias-list">
            {aliases.map((alias) => (
              <div className="alias-row" key={alias.id}>
                <div className="alias-row__heading">
                  <strong>{alias.value}</strong>
                  {session?.can_edit ? (
                    <div>
                      {alias.target_paths.every((path) => path.lotcd) ? (
                        <button type="button" onClick={() => void startEditing(alias)}>
                          수정
                        </button>
                      ) : null}
                      <button
                        type="button"
                        className={deleteConfirmAliasId === alias.id ? 'is-confirming' : ''}
                        onClick={() => void removeAlias(alias)}
                      >
                        {deleteConfirmAliasId === alias.id ? '삭제 확인' : '삭제'}
                      </button>
                    </div>
                  ) : null}
                </div>
                <div className="alias-row__targets">
                  {alias.target_paths.map((path) => (
                    <span key={pathLabel(path)}>{pathLabel(path)}</span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  )
}
