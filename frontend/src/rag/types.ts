export type ExecutionMode = 'auto' | 'fast' | 'deep'
export type ResearchStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'cancelling'

export interface RetrievalFilters {
  teams: string[]
  weeks: string[]
  mail_type?: 'weekly_report' | 'daily_report' | 'other'
}

export interface ChatPayload {
  user_id: string
  message: string
  conversation_id?: string
  filters: RetrievalFilters
  response_mode: ExecutionMode
}

export interface QualityStatus {
  citation_valid: boolean | null
  limited_answer: boolean
  retrieval_mode: 'hybrid' | 'bm25' | 'deterministic' | 'not_used' | 'not_started'
}

export interface NodeRunMetrics {
  history_messages?: number
  task_count?: number
  search_count?: number
  candidate_count?: number
  evidence_count?: number
  rewrite_count?: number
  revision_count?: number
  retrieval_mode?: 'hybrid' | 'bm25' | 'deterministic' | 'not_used' | 'not_started' | null
  fallback_used?: boolean
}

export interface NodeRun {
  sequence: number
  node_name: string
  status: 'ok' | 'error' | 'cancelled'
  started_ms: number
  duration_ms: number
  attempt: number
  input: NodeRunMetrics
  output: NodeRunMetrics
  error_class?: string | null
}

export interface ExecutionMetadata {
  status: 'succeeded' | 'limited' | 'failed'
  failure_stage?: string | null
  error_code?: string | null
  retryable: boolean
  search_count: number
  evidence_count: number
  duration_ms: number
  include_in_llm_history: boolean
  node_runs: NodeRun[]
}

export interface RoutingDiagnostics {
  requested_mode: ExecutionMode
  route: 'general' | 'fast' | 'deep' | 'clarify' | 'diagnostic' | 'corpus_info'
  executed_system: 'general' | 'fast_rag' | 'deep_research' | 'clarification' | 'diagnostic' | 'corpus_info'
  reason_code: string
  confidence: number
  estimated_searches: number
}

export interface ChatReference {
  evidence_id: string
  source_type: 'mail' | 'wiki' | 'statistic' | 'domain_knowledge' | 'calendar'
  document_id: string
  title: string
  excerpt: string
  team?: string | null
  week?: string | null
}

export interface ChatResponse {
  conversation_id: string
  mode: 'fast_rag' | 'deep_research' | 'diagnostic' | 'corpus_info'
  answer?: string | null
  references: ChatReference[]
  quality?: QualityStatus | null
  disclosures: string[]
  trace_id: string
  routing: RoutingDiagnostics
  job_id?: string | null
  status?: ResearchStatus | null
  plan_summary?: string | null
  execution?: ExecutionMetadata | null
}

export interface ResearchJobResponse {
  job_id: string
  status: ResearchStatus
  progress: number
  plan_summary: string
  result_markdown?: string | null
  references: ChatReference[]
  disclosures: string[]
  error_code?: string | null
}

export interface ResearchEvent {
  job_id: string
  status: ResearchStatus
  progress: number
}

export interface RecordedResearchEvent extends ResearchEvent {
  receivedAt: string
  note?: string
}

export interface SafeApiError {
  code: string
  message: string
  retryable: boolean
  traceId?: string
}

export interface ApiRequestRecord {
  method: 'GET' | 'POST'
  path: string
  body?: unknown
}

export interface ApiExchange<T> {
  request: ApiRequestRecord
  response?: T
  status: number
  durationMs: number
  receivedAt: string
  error?: SafeApiError
}

export interface HealthResponse {
  status: 'ok'
}

export interface ReadinessResponse {
  status: 'ready' | 'not_ready'
  dependencies: Record<string, string>
}

export interface RagRequestSettings {
  userId: string
  mode: ExecutionMode
  conversationId?: string
  filters: RetrievalFilters
}

export interface ConversationTurn {
  id: number
  question: string
  chat?: ChatResponse
  job?: ResearchJobResponse
  error?: SafeApiError
  pending: boolean
}
