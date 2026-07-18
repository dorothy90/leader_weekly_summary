import type {
  AgendaDetailResponse,
  AgendaFilters,
  AgendaListResponse,
  AliasRecord,
  CategoryCount,
  CategoryPath,
  CategoryWikiPage,
  ClassificationRevision,
  MappingRevision,
  KnowledgeFacets,
  KnowledgeSession,
  ScopeMode,
  Selection,
  Taxonomy,
} from '../types'

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal })
  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}

export function fetchTaxonomy(signal?: AbortSignal): Promise<Taxonomy> {
  return getJson<Taxonomy>('/api/knowledge/taxonomy', signal)
}

export function fetchWikiPage(
  selection: Selection,
  signal?: AbortSignal,
): Promise<CategoryWikiPage> {
  if (!selection.domain) throw new Error('Wiki page requires a domain')
  const parts: string[] = [selection.domain]
  if (selection.tech) parts.push(selection.tech)
  if (selection.lotcd) parts.push(selection.lotcd)
  return getJson<CategoryWikiPage>(
    `/api/knowledge/wiki/pages/${parts.map(encodeURIComponent).join('/')}`,
    signal,
  )
}

export async function fetchCounts(signal?: AbortSignal): Promise<CategoryCount[]> {
  const response = await getJson<{ items: CategoryCount[] }>(
    '/api/knowledge/counts',
    signal,
  )
  return response.items
}

export function fetchAgendas(
  selection: Selection,
  scopeMode: ScopeMode,
  query: string,
  reviewOnly: boolean,
  filters: AgendaFilters,
  signal?: AbortSignal,
): Promise<AgendaListResponse> {
  const params = new URLSearchParams({ scope_mode: scopeMode })
  if (selection.domain) params.set('domain', selection.domain)
  if (selection.tech) params.set('tech', selection.tech)
  if (selection.lotcd) params.set('lotcd', selection.lotcd)
  if (query) params.set('q', query)
  if (reviewOnly) params.set('review_status', 'pending')
  if (filters.topic) params.set('topic', filters.topic)
  if (filters.state) params.set('state', filters.state)
  if (filters.senderTeam) params.set('sender_team', filters.senderTeam)
  if (filters.dateFrom) params.set('date_from', filters.dateFrom)
  if (filters.dateTo) params.set('date_to', filters.dateTo)
  if (filters.reviewStatus) params.set('review_status', filters.reviewStatus)
  return getJson<AgendaListResponse>(
    `/api/knowledge/agendas?${params.toString()}`,
    signal,
  )
}

export function fetchAllAgendas(signal?: AbortSignal): Promise<AgendaListResponse> {
  return getJson<AgendaListResponse>(
    '/api/knowledge/agendas?scope_mode=descendants',
    signal,
  )
}

export function fetchFacets(signal?: AbortSignal): Promise<KnowledgeFacets> {
  return getJson<KnowledgeFacets>('/api/knowledge/facets', signal)
}

export function fetchSession(signal?: AbortSignal): Promise<KnowledgeSession> {
  return getJson<KnowledgeSession>('/api/knowledge/session', signal)
}

export function fetchAgendaDetail(
  agendaId: string,
  signal?: AbortSignal,
): Promise<AgendaDetailResponse> {
  return getJson<AgendaDetailResponse>(
    `/api/knowledge/agendas/${encodeURIComponent(agendaId)}`,
    signal,
  )
}

export async function fetchReviewCount(signal?: AbortSignal): Promise<number> {
  const response = await getJson<AgendaListResponse>(
    '/api/knowledge/review-queue',
    signal,
  )
  return response.total
}

export async function fetchAgendaRevisions(
  agendaId: string,
  signal?: AbortSignal,
): Promise<ClassificationRevision[]> {
  const response = await getJson<{ items: ClassificationRevision[] }>(
    `/api/knowledge/agendas/${encodeURIComponent(agendaId)}/revisions`,
    signal,
  )
  return response.items
}

export function confirmClassification(
  agendaId: string,
  targetPaths: CategoryPath[],
): Promise<AgendaDetailResponse> {
  return fetch(
    `/api/knowledge/agendas/${encodeURIComponent(agendaId)}/classification`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target_paths: targetPaths,
        review_status: 'confirmed',
      }),
    },
  ).then(async (response) => {
    if (!response.ok) {
      throw new Error((await response.text()) || `HTTP ${response.status}`)
    }
    return response.json() as Promise<AgendaDetailResponse>
  })
}

export function holdClassification(
  agendaId: string,
): Promise<AgendaDetailResponse> {
  return fetch(
    `/api/knowledge/agendas/${encodeURIComponent(agendaId)}/classification`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target_paths: [], review_status: 'on_hold' }),
    },
  ).then(async (response) => {
    if (!response.ok) {
      throw new Error((await response.text()) || `HTTP ${response.status}`)
    }
    return response.json() as Promise<AgendaDetailResponse>
  })
}

export async function fetchAliases(signal?: AbortSignal): Promise<AliasRecord[]> {
  const response = await getJson<{ items: AliasRecord[] }>(
    '/api/knowledge/aliases',
    signal,
  )
  return response.items
}

export function createAlias(
  value: string,
  targetPaths: CategoryPath[],
): Promise<AliasRecord> {
  return fetch('/api/knowledge/aliases', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      value,
      target_paths: targetPaths,
    }),
  }).then(async (response) => {
    if (!response.ok) {
      if (response.status === 409) throw new Error('alias_conflict')
      throw new Error((await response.text()) || `HTTP ${response.status}`)
    }
    return response.json() as Promise<AliasRecord>
  })
}

export function updateAlias(
  aliasId: number,
  value: string,
  targetPaths: CategoryPath[],
): Promise<AliasRecord> {
  return fetch(`/api/knowledge/aliases/${aliasId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      value,
      target_paths: targetPaths,
    }),
  }).then(async (response) => {
    if (!response.ok) {
      if (response.status === 409) throw new Error('alias_conflict')
      throw new Error((await response.text()) || `HTTP ${response.status}`)
    }
    return response.json() as Promise<AliasRecord>
  })
}

export function deleteAlias(aliasId: number): Promise<AliasRecord> {
  return fetch(`/api/knowledge/aliases/${aliasId}`, { method: 'DELETE' }).then(
    async (response) => {
      if (!response.ok) {
        throw new Error((await response.text()) || `HTTP ${response.status}`)
      }
      return response.json() as Promise<AliasRecord>
    },
  )
}

export async function fetchMappingRevisions(
  aliasId: number,
): Promise<MappingRevision[]> {
  const response = await getJson<{ items: MappingRevision[] }>(
    `/api/knowledge/aliases/${aliasId}/revisions`,
  )
  return response.items
}

async function taxonomyMutation(
  url: string,
  method: 'POST' | 'PATCH' | 'DELETE',
  body?: Record<string, unknown>,
): Promise<Taxonomy> {
  const response = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(`${response.status}:${detail}`)
  }
  return response.json() as Promise<Taxonomy>
}

export function createTech(input: {
  domain: 'DRAM' | 'NAND'
  id: string
  name: string
  aliases: string[]
}): Promise<Taxonomy> {
  return taxonomyMutation('/api/knowledge/techs', 'POST', input)
}

export function updateTech(
  techId: string,
  input: { name: string; aliases: string[] },
): Promise<Taxonomy> {
  return taxonomyMutation(
    `/api/knowledge/techs/${encodeURIComponent(techId)}`,
    'PATCH',
    input,
  )
}

export function deleteTech(techId: string): Promise<Taxonomy> {
  return taxonomyMutation(
    `/api/knowledge/techs/${encodeURIComponent(techId)}`,
    'DELETE',
  )
}

export function createLotcd(input: {
  domain: 'DRAM' | 'NAND'
  tech: string
  code: string
  fab_id: string
  product_code: string
  product: string
  aliases: string[]
}): Promise<Taxonomy> {
  return taxonomyMutation('/api/knowledge/lotcds', 'POST', input)
}

export function updateLotcd(
  code: string,
  input: {
    fab_id: string
    product_code: string
    product: string
    aliases: string[]
  },
): Promise<Taxonomy> {
  return taxonomyMutation(
    `/api/knowledge/lotcds/${encodeURIComponent(code)}`,
    'PATCH',
    input,
  )
}

export function deleteLotcd(code: string): Promise<Taxonomy> {
  return taxonomyMutation(
    `/api/knowledge/lotcds/${encodeURIComponent(code)}`,
    'DELETE',
  )
}
