import { useEffect, useMemo } from 'react'
import ReactMarkdown, { type Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'

import type { CategoryWikiPage } from '../types'

export interface WikiHeading {
  id: string
  label: string
}

interface CategoryWikiReaderProps {
  page: CategoryWikiPage | null
  loading: boolean
  error: string | null
  onSelectCitation: (mailId: string) => void
  onOutlineChange: (headings: WikiHeading[]) => void
}

const SECTION_LABELS = [
  '개요',
  '현재 상태와 주요 변화',
  '원인과 영향 관계',
  '조치와 효과',
  '펜딩 이슈와 의사결정',
  '누적 지식',
]

function readerMarkdown(markdown: string) {
  return markdown
    .replace(/^---\n[\s\S]*?\n---\n+/, '')
    .replace(/^# .*\n+/, '')
}

function citationMarkdown(markdown: string) {
  return markdown.replace(/\[mail:([^\]]+)\]/g, '[mail:$1](#mail:$1)')
}

function headingId(label: string) {
  return label.trim().toLowerCase().replace(/\s+/g, '-').replace(/[^\p{L}\p{N}-]/gu, '')
}

export function CategoryWikiReader({
  page,
  loading,
  error,
  onSelectCitation,
  onOutlineChange,
}: CategoryWikiReaderProps) {
  const headings = useMemo(() => [
    ...SECTION_LABELS.map((label) => ({ id: headingId(label), label })),
    ...(page?.weekly_history.length
      ? [{ id: 'weekly-history-title', label: '주차별 업데이트 이력' }]
      : []),
  ], [page?.weekly_history.length])

  useEffect(() => {
    onOutlineChange(headings)
  }, [headings, onOutlineChange])

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
  const markdownComponents: Components = {
    h2({ children }) {
      return <h2 id={headingId(String(children))}>{children}</h2>
    },
    a({ href, children }) {
      if (href?.startsWith('#mail:')) {
        return (
          <button
            className="wiki-mail-citation"
            type="button"
            onClick={() => onSelectCitation(href.slice('#mail:'.length))}
          >
            {children}
          </button>
        )
      }
      return <a href={href} target="_blank" rel="noreferrer">{children}</a>
    },
  }

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
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
          {citationMarkdown(page.current_body_markdown || readerMarkdown(page.body_markdown))}
        </ReactMarkdown>
        {page.weekly_history.length ? (
          <section className="wiki-weekly-history" aria-labelledby="weekly-history-title">
            <h2 id="weekly-history-title">주차별 업데이트 이력</h2>
            {page.weekly_history.map((entry, index) => (
              <details open={index === 0} key={entry.week}>
                <summary>{entry.week}</summary>
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                  {citationMarkdown(entry.body_markdown)}
                </ReactMarkdown>
              </details>
            ))}
          </section>
        ) : null}
      </div>
    </article>
  )
}

export default CategoryWikiReader
