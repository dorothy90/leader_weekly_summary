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

export interface WeeklyHistoryRecord {
  week: string
  body_markdown: string
  source_mail_ids: string[]
  agenda_ids?: string[]
  source_doc_ids?: string[]
}

export interface WikiCitationRecord {
  mail_id: string
  agenda_ids: string[]
  source_doc_ids?: string[]
  used_in_sections: string[]
  category_paths: string[]
}

export interface CategoryWikiPage {
  category_id: string
  page_kind: 'latest' | 'snapshot'
  doc_type: 'canonical' | 'snapshot'
  canonical_id: string
  level: 'domain' | 'tech' | 'lotcd'
  domain: DomainName
  tech: string | null
  lotcd: string | null
  title: string
  product: string | null
  fab_id: string | null
  aliases: string[]
  as_of_week: string
  current_body_markdown: string
  weekly_history: WeeklyHistoryRecord[]
  body_markdown: string
  citation_map: WikiCitationRecord[]
  child_page_ids: string[]
  confidence: 'low' | 'medium' | 'high'
  agenda_count: number
  issue_ids?: string[]
  open_issue_ids: string[]
  resolved_issue_ids: string[]
  open_issue_count: number
  resolved_issue_count: number
  contradictions: string[]
  generation_review_items: string[]
  review_agenda_ids: string[]
  source_agenda_ids: string[]
  source_doc_ids: string[]
  source_hash: string
  taxonomy_version: number
  schema_version?: number
  generation_strategy?: 'legacy' | 'fixed_sections' | 'incremental_merge'
  generated_at: string
  updated_at: string | null
}

export interface WikiPageSummary {
  category_id: string
  canonical_id: string
  level: 'domain' | 'tech' | 'lotcd'
  domain: DomainName
  tech: string | null
  lotcd: string | null
  title: string
  as_of_week: string
  open_issue_count: number
  resolved_issue_count: number
  confidence: 'low' | 'medium' | 'high'
  review_item_count: number
}

export interface WikiPageSummaryResponse {
  items: WikiPageSummary[]
}

export interface WikiCitationDetail {
  mail: Mail
  agendas: Agenda[]
  used_in_sections: string[]
}

export interface Selection {
  domain: DomainName | null
  tech: string | null
  lotcd: string | null
}
