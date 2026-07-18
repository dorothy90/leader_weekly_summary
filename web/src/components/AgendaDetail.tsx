import { useEffect, useState } from 'react'

import type {
  AgendaDetailResponse,
  CategoryPath,
  ClassificationRevision,
  Taxonomy,
} from '../types'
import { ClassificationEditor } from './ClassificationEditor'
import { EvidenceText } from './EvidenceText'

interface AgendaDetailProps {
  detail: AgendaDetailResponse | null
  loading: boolean
  taxonomy: Taxonomy | null
  revisions: ClassificationRevision[]
  canEdit: boolean
  onConfirmClassification: (paths: CategoryPath[]) => Promise<void>
  onHoldClassification: () => Promise<void>
  onClose: () => void
}

function pathLabel(path: AgendaDetailResponse['agenda']['target_paths'][number]) {
  return [path.domain, path.tech, path.lotcd].filter(Boolean).join(' / ')
}

export function AgendaDetail({
  detail,
  loading,
  taxonomy,
  revisions,
  canEdit,
  onConfirmClassification,
  onHoldClassification,
  onClose,
}: AgendaDetailProps) {
  const [editingClassification, setEditingClassification] = useState(false)

  useEffect(() => {
    setEditingClassification(false)
  }, [detail?.agenda.id, detail?.agenda.review_required])

  if (loading) {
    return <article className="detail-panel detail-panel--state">상세 불러오는 중</article>
  }
  if (!detail) {
    return (
      <article className="detail-panel detail-panel--state">
        <strong>agenda를 선택하세요</strong>
        <span>분류 경로와 메일 원문 근거가 여기에 표시됩니다.</span>
      </article>
    )
  }

  const { agenda, mail } = detail

  return (
    <article className="detail-panel">
      <div className="detail-panel__header">
        <span>Agenda detail</span>
        <button type="button" onClick={onClose} aria-label="상세 닫기">
          ×
        </button>
      </div>

      <div className="detail-panel__content">
        <div className="detail-title">
          <span className="detail-id">{agenda.id}</span>
          <h2>{agenda.summary}</h2>
          <div className="confidence-meter">
            <span style={{ width: `${agenda.confidence * 100}%` }} />
            <small>분류 신뢰도 {Math.round(agenda.confidence * 100)}%</small>
          </div>
        </div>

        <section className="detail-section">
          <h3>분류 경로</h3>
          <div className="detail-paths">
            {agenda.target_paths.length > 0 ? (
              agenda.target_paths.map((path) => (
                <span className={`path-chip path-chip--${path.domain.toLowerCase()}`} key={pathLabel(path)}>
                  {pathLabel(path)}
                </span>
              ))
            ) : (
              <span className="path-chip path-chip--unknown">미분류</span>
            )}
          </div>
          <dl className="detail-facts">
            <div>
              <dt>Scope</dt>
              <dd>{agenda.scope}</dd>
            </div>
            <div>
              <dt>Topic</dt>
              <dd>{agenda.topic}</dd>
            </div>
            <div>
              <dt>상태</dt>
              <dd>{agenda.state}</dd>
            </div>
          </dl>
        </section>

        {taxonomy && canEdit && (agenda.review_required || editingClassification) ? (
          <ClassificationEditor
            taxonomy={taxonomy}
            candidate={agenda.candidate_paths[0] ?? null}
            initialPaths={agenda.target_paths}
            allowHold={agenda.review_required}
            onConfirm={async (paths) => {
              await onConfirmClassification(paths)
              setEditingClassification(false)
            }}
            onHold={onHoldClassification}
            onCancel={() => setEditingClassification(false)}
          />
        ) : canEdit ? (
          <button
            className="detail-edit-classification"
            type="button"
            onClick={() => setEditingClassification(true)}
          >
            분류 수정
          </button>
        ) : null}

        <section className="detail-section evidence-section">
          <div className="section-heading">
            <h3>메일 원문 근거</h3>
            <span>{mail.sender_team}</span>
          </div>
          <strong className="mail-subject">{mail.subject}</strong>
          <EvidenceText body={mail.body} quote={agenda.source_quote} />
        </section>

        {revisions.length > 0 ? (
          <section className="detail-section revision-section">
            <h3>분류 변경 이력</h3>
            {revisions.map((revision) => (
              <div className="revision-row" key={revision.id}>
                <strong>{revision.changed_by}</strong>
                <span>
                  {new Intl.DateTimeFormat('ko-KR', {
                    dateStyle: 'short',
                    timeStyle: 'short',
                  }).format(new Date(revision.changed_at))}
                </span>
              </div>
            ))}
          </section>
        ) : null}
      </div>
    </article>
  )
}
