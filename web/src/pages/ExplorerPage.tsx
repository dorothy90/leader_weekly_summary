import { FormEvent, lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import {
  fetchAgendaDetail,
  fetchAgendaRevisions,
  fetchAgendas,
  fetchCounts,
  fetchFacets,
  fetchReviewCount,
  fetchSession,
  fetchTaxonomy,
  fetchWikiPage,
  confirmClassification,
  holdClassification,
} from '../api/knowledge'
import { AgendaDetail } from '../components/AgendaDetail'
import { AgendaList } from '../components/AgendaList'
import { AppBrand } from '../components/AppBrand'
import { CategoryMetaPane } from '../components/CategoryMetaPane'
import { CategoryOverview } from '../components/CategoryOverview'
import { CategoryRail } from '../components/CategoryRail'
import { FilterBar } from '../components/FilterBar'
import { ScopeToggle } from '../components/ScopeToggle'
import { TaxonomyTree } from '../components/TaxonomyTree'
import { WikiMetaPane } from '../components/WikiMetaPane'
import type {
  Agenda,
  AgendaFilters,
  AgendaDetailResponse,
  CategoryCount,
  CategoryPath,
  CategoryWikiPage,
  ClassificationRevision,
  DomainName,
  KnowledgeFacets,
  KnowledgeSession,
  ScopeMode,
  Selection,
  Taxonomy,
} from '../types'

const CategoryWikiReader = lazy(() => import('../components/CategoryWikiReader'))

function selectionFromParams(params: Readonly<Record<string, string | undefined>>): Selection {
  const domain = params.domain?.toUpperCase()
  return {
    domain: domain === 'DRAM' || domain === 'NAND' ? domain : null,
    tech: params.tech ?? null,
    lotcd: params.lotcd?.toUpperCase() ?? null,
  }
}

function selectionPath(selection: Selection, base = '/wiki/docs') {
  const parts = [base]
  if (selection.domain) parts.push(selection.domain.toLowerCase())
  if (selection.tech) parts.push(selection.tech.toLowerCase())
  if (selection.lotcd) parts.push(selection.lotcd.toLowerCase())
  return parts.join('/')
}

interface ExplorerPageProps {
  classic?: boolean
}

export function ExplorerPage({ classic = false }: ExplorerPageProps) {
  const params = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const routeSelection = selectionFromParams(params)
  const scopeMode: ScopeMode =
    searchParams.get('scope') === 'direct' ? 'direct' : 'descendants'
  const query = searchParams.get('q') ?? ''
  const agendaParam = searchParams.get('agenda')
  const reviewOnly = searchParams.get('review') === 'pending'
  const filters: AgendaFilters = {
    topic: searchParams.get('topic') ?? '',
    state: searchParams.get('state') ?? '',
    senderTeam: searchParams.get('sender') ?? '',
    dateFrom: searchParams.get('from') ?? '',
    dateTo: searchParams.get('to') ?? '',
    reviewStatus: (searchParams.get('reviewStatus') as AgendaFilters['reviewStatus']) ?? '',
  }

  const [taxonomy, setTaxonomy] = useState<Taxonomy | null>(null)
  const [counts, setCounts] = useState<CategoryCount[]>([])
  const [facets, setFacets] = useState<KnowledgeFacets | null>(null)
  const [session, setSession] = useState<KnowledgeSession | null>(null)
  const [agendas, setAgendas] = useState<Agenda[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [detail, setDetail] = useState<AgendaDetailResponse | null>(null)
  const [revisions, setRevisions] = useState<ClassificationRevision[]>([])
  const [wikiPage, setWikiPage] = useState<CategoryWikiPage | null>(null)
  const [wikiPageLoading, setWikiPageLoading] = useState(false)
  const [wikiPageError, setWikiPageError] = useState<string | null>(null)
  const [reviewCount, setReviewCount] = useState(0)
  const [refreshVersion, setRefreshVersion] = useState(0)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const taxonomyReady = taxonomy !== null
  const selectedDomain = taxonomy?.domains.find(
    (item) => item.name === routeSelection.domain,
  )
  const canonicalTech = selectedDomain?.techs.find(
    (item) => item.name.toLowerCase() === routeSelection.tech?.toLowerCase(),
  )?.name
  const selection: Selection = {
    ...(reviewOnly
      ? { domain: null, tech: null, lotcd: null }
      : routeSelection),
    tech: reviewOnly ? null : canonicalTech ?? routeSelection.tech,
  }
  const vaultStats = useMemo(() => {
    const domains = taxonomy?.domains.length ?? 0
    const techs = taxonomy?.domains.reduce((sum, domain) => sum + domain.techs.length, 0) ?? 0
    const lotcds = taxonomy?.domains.reduce(
      (sum, domain) => sum + domain.techs.reduce((techSum, tech) => techSum + tech.lotcds.length, 0),
      0,
    ) ?? 0
    return { domains, techs, lotcds }
  }, [taxonomy])

  useEffect(() => {
    if (reviewOnly && routeSelection.domain) {
      navigate({ pathname: '/wiki/docs', search: searchParams.toString() }, { replace: true })
    }
  }, [navigate, reviewOnly, routeSelection.domain, searchParams])

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetchTaxonomy(controller.signal),
      fetchCounts(controller.signal),
      fetchReviewCount(controller.signal),
      fetchFacets(controller.signal),
      fetchSession(controller.signal),
    ])
      .then(([taxonomyData, countData, nextReviewCount, facetData, sessionData]) => {
        setTaxonomy(taxonomyData)
        setCounts(countData)
        setReviewCount(nextReviewCount)
        setFacets(facetData)
        setSession(sessionData)
      })
      .catch((loadError: unknown) => {
        if (loadError instanceof Error && loadError.name !== 'AbortError') {
          setError('분류 기준을 불러오지 못했습니다.')
        }
      })
    return () => controller.abort()
  }, [refreshVersion])

  useEffect(() => {
    if (routeSelection.tech && !taxonomyReady) return
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    fetchAgendas(selection, scopeMode, query, reviewOnly, filters, controller.signal)
      .then((response) => {
        setAgendas(response.items)
        setSelectedId((current) => {
          if (agendaParam && response.items.some((item) => item.id === agendaParam)) {
            return agendaParam
          }
          if (current && response.items.some((item) => item.id === current)) {
            return current
          }
          return classic ? response.items[0]?.id ?? null : null
        })
      })
      .catch((loadError: unknown) => {
        if (loadError instanceof Error && loadError.name !== 'AbortError') {
          setError('agenda를 불러오지 못했습니다. API 연결을 확인하세요.')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [
    selection.domain,
    selection.tech,
    selection.lotcd,
    scopeMode,
    query,
    reviewOnly,
    filters.topic,
    filters.state,
    filters.senderTeam,
    filters.dateFrom,
    filters.dateTo,
    filters.reviewStatus,
    refreshVersion,
    taxonomyReady,
    agendaParam,
  ])

  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      setRevisions([])
      return
    }
    const controller = new AbortController()
    setDetailLoading(true)
    Promise.all([
      fetchAgendaDetail(selectedId, controller.signal),
      fetchAgendaRevisions(selectedId, controller.signal),
    ])
      .then(([nextDetail, nextRevisions]) => {
        setDetail(nextDetail)
        setRevisions(nextRevisions)
      })
      .catch((loadError: unknown) => {
        if (loadError instanceof Error && loadError.name !== 'AbortError') {
          setDetail(null)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false)
      })
    return () => controller.abort()
  }, [selectedId, refreshVersion])

  useEffect(() => {
    if (!selection.domain || reviewOnly) {
      setWikiPage(null)
      setWikiPageError(null)
      return
    }
    const controller = new AbortController()
    setWikiPageLoading(true)
    setWikiPageError(null)
    fetchWikiPage(selection, controller.signal)
      .then(setWikiPage)
      .catch((loadError: unknown) => {
        if (loadError instanceof Error && loadError.name !== 'AbortError') {
          setWikiPage(null)
          setWikiPageError('Wiki 문서를 불러오지 못했습니다.')
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setWikiPageLoading(false)
      })
    return () => controller.abort()
  }, [selection.domain, selection.tech, selection.lotcd, reviewOnly, refreshVersion])

  function changeSelection(next: Selection) {
    const updated = new URLSearchParams(searchParams)
    updated.delete('agenda')
    navigate({ pathname: selectionPath(next, classic ? '/explorer' : '/wiki/docs'), search: updated.toString() })
  }

  function selectAgenda(agendaId: string) {
    setSelectedId(agendaId)
    const updated = new URLSearchParams(searchParams)
    updated.set('agenda', agendaId)
    setSearchParams(updated, { replace: true })
  }

  function changeScope(next: ScopeMode) {
    const updated = new URLSearchParams(searchParams)
    if (next === 'direct') updated.set('scope', 'direct')
    else updated.delete('scope')
    setSearchParams(updated)
  }

  function submitSearch(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const nextQuery = String(form.get('query') ?? '').trim()
    const updated = new URLSearchParams(searchParams)
    if (nextQuery) updated.set('q', nextQuery)
    else updated.delete('q')
    setSearchParams(updated)
  }

  function changeFilter(name: keyof AgendaFilters, value: string) {
    const parameterNames: Record<keyof AgendaFilters, string> = {
      topic: 'topic',
      state: 'state',
      senderTeam: 'sender',
      dateFrom: 'from',
      dateTo: 'to',
      reviewStatus: 'reviewStatus',
    }
    const updated = new URLSearchParams(searchParams)
    const parameter = parameterNames[name]
    if (value) updated.set(parameter, value)
    else updated.delete(parameter)
    setSearchParams(updated)
  }

  function resetFilters() {
    const updated = new URLSearchParams(searchParams)
    for (const parameter of ['topic', 'state', 'sender', 'from', 'to', 'reviewStatus']) {
      updated.delete(parameter)
    }
    setSearchParams(updated)
  }

  async function handleConfirmClassification(paths: CategoryPath[]) {
    if (!selectedId) return
    await confirmClassification(selectedId, paths)
    setRefreshVersion((version) => version + 1)
  }

  async function handleHoldClassification() {
    if (!selectedId) return
    await holdClassification(selectedId)
    setRefreshVersion((version) => version + 1)
  }

  useEffect(() => {
    if (!reviewOnly) return
    function handleKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      if (target?.matches('input, select, textarea, button, [contenteditable="true"]')) return
      const currentIndex = agendas.findIndex((agenda) => agenda.id === selectedId)
      if (event.key.toLowerCase() === 'j' && agendas.length) {
        event.preventDefault()
        selectAgenda(agendas[(currentIndex + 1 + agendas.length) % agendas.length].id)
      } else if (event.key.toLowerCase() === 'k' && agendas.length) {
        event.preventDefault()
        selectAgenda(agendas[(currentIndex - 1 + agendas.length) % agendas.length].id)
      } else if (event.key.toLowerCase() === 'h' && selectedId && session?.can_edit) {
        event.preventDefault()
        void handleHoldClassification()
      } else if (
        event.key === 'Enter' &&
        detail?.agenda.target_paths.length &&
        session?.can_edit
      ) {
        event.preventDefault()
        void handleConfirmClassification(detail.agenda.target_paths)
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [agendas, detail, reviewOnly, selectedId, session?.can_edit])

  if (classic) {
    return (
      <div className="app-shell">
        <header className="app-header">
          <AppBrand />
          <form className="global-search" onSubmit={submitSearch} key={query}>
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              name="query"
              defaultValue={query}
              placeholder="수율, 장비 조건, LOTCD 검색"
              aria-label="agenda 검색"
            />
            <kbd>Enter</kbd>
          </form>
          <Link className="review-link" to="/review">검토 필요<span>{reviewCount}</span></Link>
          <Link className="mapping-link" to="/wiki/docs">Wiki</Link>
          <Link className="mapping-link" to="/mappings">Mapping</Link>
          <span className="session-user" title={session?.roles.join(', ')}>{session?.user_id ?? '—'}</span>
        </header>

        <div className="workbench">
          <aside className="taxonomy-panel">
            <div className="panel-label">
              <span>Category map</span>
              <button
                type="button"
                className={!selection.domain ? 'is-active' : ''}
                onClick={() => changeSelection({ domain: null, tech: null, lotcd: null })}
              >
                전체
              </button>
            </div>
            <TaxonomyTree
              taxonomy={taxonomy}
              counts={counts}
              selection={selection}
              scopeMode={scopeMode}
              onSelect={changeSelection}
            />
            {taxonomy?.is_dummy ? <div className="dummy-notice">DUMMY DATA · 운영 mapping 아님</div> : null}
          </aside>

          <main className="explorer-main">
            <CategoryRail selection={selection} total={agendas.length} reviewOnly={reviewOnly} />
            <div className="list-toolbar">
              <div><strong>{reviewOnly ? '검토 대기' : 'Agenda stream'}</strong><span>최신 메일부터 표시</span></div>
              <ScopeToggle value={scopeMode} onChange={changeScope} disabled={!selection.domain} />
            </div>
            <FilterBar facets={facets} filters={filters} onChange={changeFilter} onReset={resetFilters} />
            <AgendaList
              agendas={agendas}
              selectedId={selectedId}
              loading={loading}
              error={error}
              onSelect={selectAgenda}
            />
          </main>

          <AgendaDetail
            detail={detail}
            loading={detailLoading}
            taxonomy={taxonomy}
            revisions={revisions}
            canEdit={session?.can_edit ?? false}
            onConfirmClassification={handleConfirmClassification}
            onHoldClassification={handleHoldClassification}
            onClose={() => setSelectedId(null)}
          />
        </div>
      </div>
    )
  }

  return (
    <div className={`wiki-docs-shell ${!selection.domain && !selectedId ? 'wiki-docs-shell--wide' : ''}`}>
      <aside className="wiki-vault-pane">
        <div className="wiki-vault-header">
          <div className="wiki-vault-nav">
            <div>
              <Link to="/explorer">← Explorer</Link>
              <Link to="/wiki/graph">Graph</Link>
              <Link to="/review">Review {reviewCount ? `(${reviewCount})` : ''}</Link>
              <Link to="/mappings">Mapping</Link>
            </div>
            <button
              type="button"
              onClick={() => {
                navigate('/wiki/docs')
                setSelectedId(null)
              }}
            >
              ↺ 초기화
            </button>
          </div>
          <h1>Wiki Docs</h1>
          <p>{vaultStats.domains} domains · {vaultStats.techs} Tech · {vaultStats.lotcds} LOTCD</p>
          <form className="wiki-search" onSubmit={submitSearch} key={query}>
            <span aria-hidden="true">⌕</span>
            <input
              type="search"
              name="query"
              defaultValue={query}
              placeholder="Wiki 검색"
              aria-label="agenda 검색"
            />
            <kbd>↵</kbd>
          </form>
        </div>

        <div className="wiki-vault-tree">
          <div className="wiki-pane-label">
            <span>Vault</span>
            <button
              type="button"
              className={!selection.domain ? 'is-active' : ''}
              onClick={() => changeSelection({ domain: null, tech: null, lotcd: null })}
            >
              전체
            </button>
          </div>
          <TaxonomyTree
            taxonomy={taxonomy}
            counts={counts}
            selection={selection}
            scopeMode={scopeMode}
            onSelect={changeSelection}
          />
        </div>
      </aside>

      <main className="wiki-reader-stage">
        {selectedId ? (
          <AgendaDetail
            detail={detail}
            loading={detailLoading}
            taxonomy={taxonomy}
            revisions={revisions}
            canEdit={session?.can_edit ?? false}
            onConfirmClassification={handleConfirmClassification}
            onHoldClassification={handleHoldClassification}
            onClose={() => {
              setSelectedId(null)
              const updated = new URLSearchParams(searchParams)
              updated.delete('agenda')
              setSearchParams(updated, { replace: true })
            }}
          />
        ) : selection.domain && !reviewOnly ? (
          <Suspense fallback={<div className="reader-empty">Wiki Reader 불러오는 중</div>}>
            <CategoryWikiReader
              page={wikiPage}
              loading={wikiPageLoading}
              error={wikiPageError}
              onSelectAgenda={selectAgenda}
            />
          </Suspense>
        ) : (
          <CategoryOverview
            selection={selection}
            taxonomy={taxonomy}
            agendas={agendas}
            reviewOnly={reviewOnly}
            onSelectAgenda={selectAgenda}
          />
        )}
      </main>

      {selectedId ? (
        <WikiMetaPane
          detail={detail}
          agendas={agendas}
          taxonomy={taxonomy}
          revisions={revisions}
          onSelectAgenda={selectAgenda}
        />
      ) : selection.domain ? (
        <CategoryMetaPane
          selection={selection}
          taxonomy={taxonomy}
          agendas={agendas}
          scopeMode={scopeMode}
          onChangeScope={changeScope}
          onSelectAgenda={selectAgenda}
        />
      ) : null}
    </div>
  )
}
