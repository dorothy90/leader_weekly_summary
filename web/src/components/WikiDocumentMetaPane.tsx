import type { CategoryWikiPage } from '../types'
import type { WikiHeading } from './CategoryWikiReader'

interface WikiDocumentMetaPaneProps {
  page: CategoryWikiPage
  headings: WikiHeading[]
  onNavigate: (canonicalId: string) => void
}

export function WikiDocumentMetaPane({
  page,
  headings,
  onNavigate,
}: WikiDocumentMetaPaneProps) {
  const parentSeparator = page.canonical_id.lastIndexOf('/')
  const parentId = parentSeparator > 0
    ? page.canonical_id.slice(0, parentSeparator)
    : null
  const hasReviewItems = page.contradictions.length > 0
    || page.generation_review_items.length > 0

  return (
    <aside className="wiki-meta-pane wiki-document-meta">
      <section className="wiki-meta-section">
        <h2>Metrics</h2>
        <div className="wiki-metrics">
          <div><span>진행 이슈</span><strong>{page.open_issue_count}</strong></div>
          <div><span>해결 이슈</span><strong>{page.resolved_issue_count}</strong></div>
          <div><span>출처</span><strong>{page.citation_map.length}</strong></div>
          <div><span>기준 주차</span><strong>{page.as_of_week}</strong></div>
        </div>
      </section>

      <section className="wiki-meta-section">
        <h2>Frontmatter</h2>
        <div className="wiki-field">
          <span>confidence</span><strong>{page.confidence}</strong>
        </div>
        <div className="wiki-field">
          <span>canonical</span><strong>{page.canonical_id}</strong>
        </div>
      </section>

      <section className="wiki-meta-section">
        <h2>Outline</h2>
        <nav aria-label="문서 목차">
          {headings.map((heading) => (
            <a href={`#${heading.id}`} key={heading.id}>{heading.label}</a>
          ))}
        </nav>
      </section>

      {hasReviewItems ? (
        <section className="wiki-meta-section">
          <h2>Review</h2>
          <ul>
            {page.contradictions.map((item) => <li key={`contradiction:${item}`}>{item}</li>)}
            {page.generation_review_items.map((item) => <li key={`review:${item}`}>{item}</li>)}
          </ul>
        </section>
      ) : null}

      {parentId || page.child_page_ids.length > 0 ? (
        <section className="wiki-meta-section">
          <h2>Related pages</h2>
          <div className="wiki-backlinks">
            {parentId ? (
              <button type="button" onClick={() => onNavigate(parentId)}>
                <span aria-hidden="true">↩</span><strong>{parentId}</strong>
              </button>
            ) : null}
            {page.child_page_ids.map((childId) => (
              <button type="button" onClick={() => onNavigate(childId)} key={childId}>
                <span aria-hidden="true">↩</span><strong>{childId}</strong>
              </button>
            ))}
          </div>
        </section>
      ) : null}
    </aside>
  )
}

export default WikiDocumentMetaPane
