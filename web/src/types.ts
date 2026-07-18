export type DomainName = 'DRAM' | 'NAND'

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

export interface Taxonomy {
  version: number
  is_dummy: boolean
  notice: string
  domains: Array<{ id: string; name: DomainName; techs: Tech[] }>
  group_aliases: Array<{ alias: string; target_lotcds: string[] }>
}

export interface CategoryPath {
  domain: DomainName
  tech: string | null
  lotcd: string | null
}

export interface KnowledgeSession {
  user_id: string
  roles: string[]
  can_edit: boolean
}

export type ClassificationItemKind = 'lotcd_specific' | 'aggregate' | 'unknown'
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

export interface ClassificationItem {
  agenda_id: string
  mail_id: string
  summary: string
  source_quote: string
  classification_context: string
  item_kind: ClassificationItemKind
  decision: ClassificationDecision
  revision_count: number
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

export interface ClassificationItemList {
  items: ClassificationItem[]
  total: number
}

export interface ClassificationFilters {
  lotcd?: string
  status?: DecisionStatus
  q?: string
}

export interface ClassificationSplitPart {
  source_quote: string
  summary: string
  lotcd: string
}

export interface ClassificationAliasRequest {
  value: string
  lotcd: string
  origin_agenda_id: string
  context_domain?: DomainName | null
  context_tech?: string | null
}

export interface AliasRecord {
  id: number
  value: string
  target_paths: CategoryPath[]
  origin_agenda_id: string | null
  context_domain: DomainName | null
  context_tech: string | null
}

export type ClassificationAliasResponse = AliasRecord

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
