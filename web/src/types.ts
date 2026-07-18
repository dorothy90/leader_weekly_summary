export type DomainName = 'DRAM' | 'NAND'
export type ScopeMode = 'direct' | 'descendants'

export interface Lotcd {
  code: string
  fab_id: string
  product_code: string
  product: string
  aliases: string[]
}

export interface Tech {
  id: string
  name: string
  aliases: string[]
  lotcds: Lotcd[]
}

export interface Domain {
  id: string
  name: DomainName
  techs: Tech[]
}

export interface Taxonomy {
  version: number
  is_dummy: boolean
  notice: string
  domains: Domain[]
  group_aliases: Array<{ alias: string; target_lotcds: string[] }>
}

export interface CategoryPath {
  domain: DomainName
  tech: string | null
  lotcd: string | null
}

export interface CategoryCount {
  path: CategoryPath
  direct: number
  descendants: number
}

export interface Agenda {
  id: string
  mail_id: string
  source_quote: string
  summary: string
  scope: 'domain' | 'tech' | 'lotcd' | 'multi_lotcd' | 'cross_domain' | 'unknown'
  target_paths: CategoryPath[]
  candidate_paths: CategoryPath[]
  topic: string
  state: string
  confidence: number
  review_required: boolean
  review_status: 'pending' | 'confirmed' | 'on_hold'
  subject: string
  sender_team: string
  received_at: string
}

export interface Mail {
  id: string
  subject: string
  sender_team: string
  sender: string
  received_at: string
  body: string
  reply_to: string | null
}

export interface AgendaListResponse {
  items: Agenda[]
  total: number
}

export interface AgendaDetailResponse {
  agenda: Agenda
  mail: Mail
}

export interface ClassificationRevision {
  id: number
  agenda_id: string
  before: Record<string, unknown>
  after: Record<string, unknown>
  changed_at: string
  changed_by: string
}

export interface AliasRecord {
  id: number
  value: string
  target_paths: CategoryPath[]
  origin_agenda_id?: string | null
  context_domain?: DomainName | null
  context_tech?: string | null
}

export interface MappingRevision {
  id: number
  alias_id: number
  before: Record<string, unknown> | null
  after: Record<string, unknown>
  changed_at: string
  changed_by: string
}

export interface KnowledgeFacets {
  topics: string[]
  states: string[]
  sender_teams: string[]
  date_min: string | null
  date_max: string | null
}

export interface AgendaFilters {
  topic: string
  state: string
  senderTeam: string
  dateFrom: string
  dateTo: string
  reviewStatus: '' | 'pending' | 'confirmed' | 'on_hold'
}

export interface KnowledgeSession {
  user_id: string
  roles: string[]
  can_edit: boolean
}

export interface CategoryWikiPage {
  category_id: string
  page_kind: 'latest' | 'snapshot'
  level: 'domain' | 'tech' | 'lotcd'
  domain: DomainName
  tech: string | null
  lotcd: string | null
  title: string
  product: string | null
  fab_id: string | null
  as_of_week: string
  body_markdown: string
  agenda_count: number
  open_issue_ids: string[]
  resolved_issue_ids: string[]
  review_agenda_ids: string[]
  source_agenda_ids: string[]
  source_doc_ids: string[]
  source_hash: string
  taxonomy_version: number
  generated_at: string
}

export interface Selection {
  domain: DomainName | null
  tech: string | null
  lotcd: string | null
}

export type ClassificationItemKind =
  | 'lotcd_specific'
  | 'aggregate'
  | 'unknown'

export type DecisionStatus =
  | 'confirmed'
  | 'aggregate'
  | 'unclassified'
  | 'conflict'
  | 'review_required'
  | 'manually_corrected'
  | 'excluded'

export type ClassificationDiagnostic =
  | 'NO_LOTCD_MATCH'
  | 'MULTIPLE_LOTCD_CONFLICT'
  | 'UNKNOWN_LOTCD_CODE'
  | 'AGGREGATE_METRIC'
  | 'CONTEXT_MISSING'
  | 'ALIAS_COLLISION'

export interface CandidateMatch {
  phrase: string
  lotcd: string
  match_type: 'canonical' | 'alias'
  rule_id: string
  score: number
}

export interface ClassificationDecision {
  status: DecisionStatus
  target_path: CategoryPath | null
  matches: CandidateMatch[]
  diagnostics: ClassificationDiagnostic[]
  confidence: number
}

export interface ClassificationTrace {
  item_kind: ClassificationItemKind
  decision: ClassificationDecision
}

export interface ClassificationRun {
  id: string
  week: string
  status: 'processing' | 'completed' | 'failed'
  prompt_version: string
  classifier_version: string
  taxonomy_version: number
  alias_version: number
  prior_run_id: string | null
  started_at: string
  completed_at: string | null
  error: string | null
}

export interface ClassificationRunRequest {
  rerun: boolean
}

export interface ClassificationWeek {
  week: string
  workflow_state:
    | 'not_started'
    | 'processing'
    | 'review_in_progress'
    | 'ready_for_approval'
    | 'approved'
    | 'revalidation_required'
    | 'failed'
  active_run_id: string | null
  counts: Partial<Record<DecisionStatus, number>>
}

export interface ClassificationItem extends ClassificationTrace {
  agenda_id: string
  mail_id: string
  summary: string
  source_quote: string
  classification_context: string
  revision_count: number
}

export interface ClassificationItemList {
  items: ClassificationItem[]
  total: number
}

export interface ClassificationFilters {
  lotcd?: string
  status?: DecisionStatus
  q?: string
}

export interface RunItemChange {
  agenda_id: string
  before_status: DecisionStatus | null
  after_status: DecisionStatus | null
  before_lotcd: string | null
  after_lotcd: string | null
}

export interface ClassificationRunComparison {
  old_run_id: string
  new_run_id: string
  changed: RunItemChange[]
  unchanged_count: number
}

export interface ClassificationCorrectionRequest {
  lotcd: string
  reason: string
}

export interface ClassificationDispositionRequest {
  status: 'aggregate' | 'excluded'
  reason: string
}

export interface ClassificationSplitPart {
  source_quote: string
  summary: string
  lotcd: string
}

export interface ClassificationSplitRequest {
  parts: ClassificationSplitPart[]
  reason: string
}

export interface ClassificationAliasRequest {
  value: string
  lotcd: string
  origin_agenda_id: string
  context_domain?: DomainName | null
  context_tech?: string | null
}

export type ClassificationAliasResponse = AliasRecord
