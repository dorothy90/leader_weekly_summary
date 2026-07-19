import { Link } from 'react-router-dom'

import type { CategoryPath, KnowledgeArea, TopicListItem, TopicState } from '../types'

export const topicStateLabels: Record<TopicState, string> = {
  new: '신규',
  investigating: '조사 중',
  action_in_progress: '조치 진행',
  monitoring: '모니터링',
  resolved: '해결',
  reopened: '재개',
  closed: '종료',
  review_required: '검토 필요',
}

export const knowledgeAreaLabels: Record<KnowledgeArea, string> = {
  yield_defect: '수율·불량',
  process_equipment: '공정·설비',
  quality_analysis: '품질·분석',
  experiment_validation: '실험·검증',
  product_production: '제품·생산',
  schedule_delivery: '일정·납기',
  decision_action: '의사결정·조치',
  other: '기타',
}

export function formatTargetPath(path: CategoryPath) {
  return [path.domain, path.tech, path.lotcd].filter(Boolean).join(' / ')
}

interface TopicListProps {
  topics: TopicListItem[]
  from: string
  showRankReasons?: boolean
}

export function TopicList({ topics, from, showRankReasons = false }: TopicListProps) {
  if (topics.length === 0) {
    return <p className="topic-list__empty">조건에 맞는 주제가 없습니다.</p>
  }

  return (
    <ol className="topic-list">
      {topics.map((topic) => (
        <li key={topic.topic_id} className="topic-list__item">
          <div className="topic-list__identity">
            <span className="topic-list__id">{topic.topic_id}</span>
            <span className={`topic-badge topic-badge--${topic.state}`}>
              {topicStateLabels[topic.state]}
            </span>
            <span className={`topic-importance topic-importance--${topic.importance}`}>
              {topic.importance.toUpperCase()}
            </span>
          </div>
          <Link
            className="topic-list__title"
            to={`/wiki/topics/${encodeURIComponent(topic.topic_id)}?from=${encodeURIComponent(from)}`}
          >
            {topic.title}
          </Link>
          <dl className="topic-list__metadata">
            <div><dt>영역</dt><dd>{knowledgeAreaLabels[topic.primary_area]}</dd></div>
            <div>
              <dt>LOTCD</dt>
              <dd>{topic.target_paths.length > 0 ? topic.target_paths.map(formatTargetPath).join(', ') : '공통'}</dd>
            </div>
            <div><dt>기여 팀</dt><dd>{topic.teams.length > 0 ? topic.teams.join(' · ') : '미지정'}</dd></div>
            <div><dt>최근 갱신</dt><dd>{topic.last_updated_week}</dd></div>
            <div><dt>인용</dt><dd>근거 {topic.evidence_count}건</dd></div>
          </dl>
          {showRankReasons && topic.rank_reasons.length > 0 ? (
            <ul className="topic-list__rank-reasons" aria-label={`${topic.title} 정렬 근거`}>
              {topic.rank_reasons.map((reason) => <li key={reason}>{reason}</li>)}
            </ul>
          ) : null}
        </li>
      ))}
    </ol>
  )
}
