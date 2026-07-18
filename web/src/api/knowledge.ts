import type {
  ClassificationAliasRequest,
  ClassificationAliasResponse,
  ClassificationFilters,
  ClassificationItem,
  ClassificationItemList,
  ClassificationRunComparison,
  ClassificationSplitPart,
  ClassificationWeek,
  KnowledgeSession,
  Taxonomy,
} from '../types'

async function getJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(url, { signal })
  if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`)
  return response.json() as Promise<T>
}

async function mutate<T>(url: string, method: 'POST' | 'PATCH', body?: object): Promise<T> {
  const response = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!response.ok) throw new Error(await response.text() || `HTTP ${response.status}`)
  return response.json() as Promise<T>
}

export const fetchClassificationWeeks = (signal?: AbortSignal) =>
  getJson<ClassificationWeek[]>('/api/knowledge/classification/weeks', signal)

export function fetchClassificationItems(
  week: string,
  filters: ClassificationFilters = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams()
  if (filters.lotcd) params.set('lotcd', filters.lotcd)
  if (filters.status) params.set('status', filters.status)
  if (filters.q) params.set('q', filters.q)
  const query = params.toString()
  return getJson<ClassificationItemList>(
    `/api/knowledge/classification/weeks/${encodeURIComponent(week)}/items${query ? `?${query}` : ''}`,
    signal,
  )
}

export const fetchClassificationItem = (agendaId: string, signal?: AbortSignal) =>
  getJson<ClassificationItem>(
    `/api/knowledge/classification/items/${encodeURIComponent(agendaId)}`,
    signal,
  )

export const fetchClassificationRunComparison = (
  oldRunId: string,
  newRunId: string,
  signal?: AbortSignal,
) => getJson<ClassificationRunComparison>(
  `/api/knowledge/classification/runs/${encodeURIComponent(oldRunId)}/comparison/${encodeURIComponent(newRunId)}`,
  signal,
)

export const runClassificationWeek = (week: string, rerun = false) =>
  mutate<ClassificationWeek>(
    `/api/knowledge/classification/weeks/${encodeURIComponent(week)}/run`,
    'POST',
    { rerun },
  )

export const approveClassificationWeek = (week: string) =>
  mutate<ClassificationWeek>(
    `/api/knowledge/classification/weeks/${encodeURIComponent(week)}/approve`,
    'POST',
  )

export const correctClassificationItem = (
  agendaId: string,
  lotcd: string,
  reason: string,
) => mutate<ClassificationItem>(
  `/api/knowledge/classification/items/${encodeURIComponent(agendaId)}`,
  'PATCH',
  { lotcd, reason },
)

export const setClassificationDisposition = (
  agendaId: string,
  status: 'aggregate' | 'excluded',
  reason: string,
) => mutate<ClassificationItem>(
  `/api/knowledge/classification/items/${encodeURIComponent(agendaId)}/disposition`,
  'PATCH',
  { status, reason },
)

export const splitClassificationItem = (
  agendaId: string,
  parts: ClassificationSplitPart[],
  reason: string,
) => mutate<ClassificationItem[]>(
  `/api/knowledge/classification/items/${encodeURIComponent(agendaId)}/split`,
  'POST',
  { parts, reason },
)

export const createClassificationAlias = (input: ClassificationAliasRequest) =>
  mutate<ClassificationAliasResponse>(
    '/api/knowledge/classification/aliases',
    'POST',
    input,
  )

export const fetchTaxonomy = (signal?: AbortSignal) =>
  getJson<Taxonomy>('/api/knowledge/taxonomy', signal)

export const fetchSession = (signal?: AbortSignal) =>
  getJson<KnowledgeSession>('/api/knowledge/session', signal)
