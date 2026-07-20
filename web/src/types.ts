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

export type TopicKind =
  | 'issue'
  | 'observation'
  | 'change'
  | 'experiment'
  | 'action'
  | 'decision'
  | 'plan'
  | 'knowledge'

export type KnowledgeArea =
  | 'yield_defect'
  | 'process_equipment'
  | 'quality_analysis'
  | 'experiment_validation'
  | 'product_production'
  | 'schedule_delivery'
  | 'decision_action'
  | 'other'

export type TopicState =
  | 'new'
  | 'investigating'
  | 'action_in_progress'
  | 'monitoring'
  | 'resolved'
  | 'reopened'
  | 'closed'
  | 'review_required'

export interface WikiTopic {
  topic_id: string
  title: string
  topic_kind: TopicKind
  primary_area: KnowledgeArea
  secondary_areas: KnowledgeArea[]
  state: TopicState
  importance: 'low' | 'medium' | 'high' | 'critical'
  first_seen_week: string
  last_updated_week: string
  target_paths: CategoryPath[]
  teams: string[]
  source_agenda_ids: string[]
  related_topic_ids: string[]
  current_revision_id: string
}

export type RelationKind =
  | 'possible_cause'
  | 'affects'
  | 'measurement_effect'
  | 'comparison'
  | 'follow_up'
  | 'supports'
  | 'contradicts'
  | 'shares_condition'

export interface TopicRelation {
  relation_id: string
  source_topic_id: string
  target_topic_id: string
  kind: RelationKind
  agenda_ids: string[]
  confidence: number
  review_state: 'pending' | 'accepted' | 'rejected'
}

export interface WikiGraphView {
  topics: TopicListItem[]
  relations: TopicRelation[]
}

export interface WikiTopicDetail {
  topic: WikiTopic
  body_markdown: string
  sections: Array<{ key: string; title: string; body: string }>
  claims: Array<{ text: string; agenda_ids: string[] }>
  evidence: WikiEvidence[]
  relations: TopicRelation[]
}

export interface TopicListItem {
  topic_id: string
  title: string
  state: TopicState
  importance: 'low' | 'medium' | 'high' | 'critical'
  primary_area: KnowledgeArea
  target_paths: CategoryPath[]
  teams: string[]
  last_updated_week: string
  evidence_count: number
  rank_reasons: string[]
}

export interface WikiEvidence {
  agenda_id: string
  mail_id: string
  team: string
  week: string
  subject: string
  source_quote: string
  source_path?: string | null
  mail_html_available?: boolean
  original_mail_url?: string | null
}

export interface LotcdWikiView {
  domain: DomainName
  tech: string | null
  lotcd: string | null
  scope_level: 'domain' | 'tech' | 'lotcd'
  breadcrumb: string[]
  summary: string
  recent_changes: TopicListItem[]
  active_topics: TopicListItem[]
  knowledge_areas: Partial<Record<KnowledgeArea, TopicListItem[]>>
  actions_and_decisions: TopicListItem[]
  related_lotcds: string[]
  closed_topics: Record<string, TopicListItem[]>
  activity: WikiEvidence[]
  direct_activity: WikiEvidence[]
  rolled_up_activity: WikiEvidence[]
  topic_ids: string[]
}

export interface TeamWikiView {
  team: string
  topics: TopicListItem[]
  topic_ids: string[]
  recent_activity: WikiEvidence[]
  partner_teams: string[]
  target_paths: CategoryPath[]
  actions_and_decisions: TopicListItem[]
}

export interface WikiIndex { values: string[] }

export interface WeekWikiView {
  week: string
  revision_id: string
  published_at: string
  build_run_id: string
  new_topic_ids: string[]
  changed_topic_ids: string[]
  resolved_topic_ids: string[]
  reopened_topic_ids: string[]
  actions_and_decisions: TopicListItem[]
  new_relation_ids: string[]
  relation_review_events?: Array<{
    relation_id: string
    relation_kind?: RelationKind | null
    origin_week?: string
    action: 'accepted' | 'rejected'
    actor: string
    reviewed_at: string
  }>
  pending_assignment_count: number
  contradictions: string[]
  teams: string[]
}

export interface WikiReview {
  review_id: string
  kind: 'assignment' | 'relation'
  agenda_id: string | null
  candidates: Array<{
    topic_id: string
    score: number
    rank_reasons: string[]
  }>
  relation_id: string | null
  relation_kind?: RelationKind | null
  relation_agenda_ids?: string[]
  rationale: string
  status: 'pending' | 'resolved' | 'held'
}

export interface WikiBuildRun {
  run_id: string
  week: string
  classification_run_id: string
  taxonomy_version: number
  status:
    | 'linking'
    | 'review_required'
    | 'generating'
    | 'validating'
    | 'published'
    | 'partially_failed'
    | 'failed'
  input_hash: string
  model: string
  affected_topic_ids: string[]
  failed_topic_ids: string[]
  started_at: string
  completed_at: string | null
  error: string | null
}

export interface WikiReviewResolution {
  action: 'attach' | 'create' | 'hold' | 'accept' | 'reject'
  topic_id?: string
  title?: string
}
