import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { CategoryWikiPage } from '../types'

interface CategoryWikiReaderProps {
  page: CategoryWikiPage | null
  loading: boolean
  error: string | null
  onSelectAgenda: (agendaId: string) => void
}

function readerMarkdown(markdown: string) {
  return markdown
    .replace(/^---\n[\s\S]*?\n---\n+/, '')
    .replace(/^# .*\n+/, '')
    .replace(/\[\[([^\]]+)\]\]/g, '[$1](#agenda:$1)')
}

export function CategoryWikiReader({
  page,
  loading,
  error,
  onSelectAgenda,
}: CategoryWikiReaderProps) {
  if (loading) {
    return <div className="reader-empty">Wiki 문서를 불러오는 중입니다.</div>
  }
  if (error || !page) {
    return (
      <div className="reader-empty">
        <strong>생성된 Wiki 문서가 없습니다.</strong>
        <span>`category_wiki_builder.py`를 실행한 후 다시 확인하세요.</span>
      </div>
    )
  }

  const path = [page.domain, page.tech, page.lotcd].filter(Boolean)
  return (
    <article className="category-note wiki-generated-page">
      <header>
        <span className="category-note__file">
          {path.join('/').toLowerCase()}.md · {page.as_of_week}
        </span>
        <div className="category-note__crumbs">
          {path.map((node) => <span key={node}>[[{node}]]</span>)}
        </div>
        <h1>{page.title}</h1>
        <p>
          {page.agenda_count}개 Agenda · 진행 {page.open_issue_ids.length}건 · 해결 {page.resolved_issue_ids.length}건
        </p>
      </header>
      <div className="reader-body">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            a({ href, children }) {
              if (href?.startsWith('#agenda:')) {
                const agendaId = href.slice('#agenda:'.length)
                return (
                  <button
                    className="wiki-agenda-link"
                    type="button"
                    onClick={() => onSelectAgenda(agendaId)}
                  >
                    {children}
                  </button>
                )
              }
              return <a href={href} target="_blank" rel="noreferrer">{children}</a>
            },
          }}
        >
          {readerMarkdown(page.body_markdown)}
        </ReactMarkdown>
      </div>
    </article>
  )
}

export default CategoryWikiReader
