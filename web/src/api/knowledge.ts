import type {
  ClassificationAliasRequest,
  ClassificationAliasResponse,
  ClassificationFilters,
  ClassificationItem,
  ClassificationItemList,
  ClassificationRunComparison,
  ClassificationSplitPart,
  ClassificationWeek,
  DomainName,
  KnowledgeArea,
  KnowledgeSession,
  LotcdWikiView,
  Taxonomy,
  TeamWikiView,
  TopicListItem,
  TopicState,
  WeekWikiView,
  WikiBuildRun,
  WikiGraphView,
  WikiReview,
  WikiReviewResolution,
  WikiTopicDetail,
  WikiIndex,
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

export function fetchTopics(
  filters: {
    q?: string
    state?: TopicState
    area?: KnowledgeArea
    team?: string
    lotcd?: string
  } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value)
  })
  const query = params.toString()
  return getJson<TopicListItem[]>(
    `/api/knowledge/wiki/topics${query ? `?${query}` : ''}`,
    signal,
  )
}

export const fetchTopic = (topicId: string, signal?: AbortSignal) =>
  getJson<WikiTopicDetail>(
    `/api/knowledge/wiki/topics/${encodeURIComponent(topicId)}`,
    signal,
  )

export const fetchWikiGraph = (signal?: AbortSignal) =>
  getJson<WikiGraphView>('/api/knowledge/wiki/graph', signal)

export const fetchLotcdWiki = (
  domain: DomainName,
  tech: string,
  lotcd: string,
  signal?: AbortSignal,
) => getJson<LotcdWikiView>(
  `/api/knowledge/wiki/lotcd/${encodeURIComponent(domain)}/${encodeURIComponent(tech)}/${encodeURIComponent(lotcd)}`,
  signal,
)

export const resolveWikiReview = (
  reviewId: string,
  input: WikiReviewResolution,
) => mutate<WikiReview>(
  `/api/knowledge/wiki/reviews/${encodeURIComponent(reviewId)}/resolve`,
  'POST',
  input,
)

export const fetchTeamWiki = (team: string, signal?: AbortSignal) =>
  getJson<TeamWikiView>(
    `/api/knowledge/wiki/teams/${encodeURIComponent(team)}`,
    signal,
  )

export const fetchWikiTeams = (signal?: AbortSignal) =>
  getJson<WikiIndex>('/api/knowledge/wiki/teams', signal)

export const fetchWeekWiki = (week: string, signal?: AbortSignal) =>
  getJson<WeekWikiView>(
    `/api/knowledge/wiki/weeks/${encodeURIComponent(week)}`,
    signal,
  )

export const fetchWikiWeeks = (signal?: AbortSignal) =>
  getJson<WikiIndex>('/api/knowledge/wiki/weeks', signal)

export const fetchWikiReviews = (signal?: AbortSignal) =>
  getJson<WikiReview[]>('/api/knowledge/wiki/reviews?status=pending', signal)

export const startWikiBuild = (week: string) =>
  mutate<WikiBuildRun>(
    `/api/knowledge/wiki/builds/${encodeURIComponent(week)}`,
    'POST',
  )

export const fetchWikiBuild = (runId: string, signal?: AbortSignal) =>
  getJson<WikiBuildRun>(
    `/api/knowledge/wiki/builds/${encodeURIComponent(runId)}`,
    signal,
  )
