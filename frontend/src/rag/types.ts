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
  citation_valid: boolean
  limited_answer: boolean
  retrieval_mode: 'hybrid' | 'bm25' | 'not_used'
}

export interface RoutingDiagnostics {
  requested_mode: ExecutionMode
  route: 'general' | 'fast' | 'deep' | 'clarify'
  executed_system: 'general' | 'fast_rag' | 'deep_research' | 'clarification'
  reason_code: string
  confidence: number
  estimated_searches: number
}

export interface ChatReference {
  evidence_id: string
  source_type: 'mail' | 'wiki' | 'statistic'
  document_id: string
  title: string
  excerpt: string
  team?: string | null
  week?: string | null
}

export interface ChatResponse {
  conversation_id: string
  mode: 'fast_rag' | 'deep_research'
  answer?: string | null
  references: ChatReference[]
  quality?: QualityStatus | null
  disclosures: string[]
  trace_id: string
  routing: RoutingDiagnostics
  job_id?: string | null
  status?: ResearchStatus | null
  plan_summary?: string | null
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

export interface RagRequestDraft {
  userId: string
  mode: ExecutionMode
  conversationId?: string
  question: string
  filters: RetrievalFilters
}
