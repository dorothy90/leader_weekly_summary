import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { fetchWikiReviews } from '../api/knowledge'
import { KnowledgeTree } from '../components/wiki/KnowledgeTree'
import { ResizablePane } from '../components/wiki/ResizablePane'
import { WikiUtilityRail } from '../components/wiki/WikiUtilityRail'
import { useWikiCollection } from '../components/wiki/useWikiCollection'
import { parseWikiLocation } from '../components/wiki/wikiLocation'
import { TopicList } from '../components/TopicList'

const LAYOUT_KEY = 'weekly-wiki:layout:v1'

interface LayoutState { treeWidth: number; documentWidth: number; treeCollapsed: boolean; documentCollapsed: boolean }

function initialLayout(): LayoutState {
  try {
    const value = typeof localStorage?.getItem === 'function' ? localStorage.getItem(LAYOUT_KEY) : null
    const saved = JSON.parse(value || '{}') as Partial<LayoutState>
    return {
      treeWidth: saved.treeWidth ?? 254,
      documentWidth: saved.documentWidth ?? 460,
      treeCollapsed: saved.treeCollapsed ?? false,
      documentCollapsed: saved.documentCollapsed ?? false,
    }
  } catch {
    return { treeWidth: 254, documentWidth: 460, treeCollapsed: false, documentCollapsed: false }
  }
}

export function WikiWorkspacePage() {
  const location = useLocation()
  const navigate = useNavigate()
  const wikiLocation = parseWikiLocation(location.pathname, location.search)
  const collection = useWikiCollection(wikiLocation.collectionPath)
  const [treeMode, setTreeMode] = useState<'knowledge' | 'evidence'>('knowledge')
  const [layout, setLayout] = useState<LayoutState>(initialLayout)
  const [reviewCount, setReviewCount] = useState(0)

  useEffect(() => {
    if (typeof localStorage?.setItem === 'function') {
      localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout))
    }
  }, [layout])

  useEffect(() => {
    const controller = new AbortController()
    fetchWikiReviews(controller.signal).then((reviews) => setReviewCount(reviews.length)).catch(() => setReviewCount(0))
    return () => controller.abort()
  }, [])

  function changeView(view: 'docs' | 'graph') {
    const params = new URLSearchParams(location.search)
    if (view === 'graph') params.set('view', 'graph')
    else params.delete('view')
    navigate(`${location.pathname}${params.size ? `?${params}` : ''}`)
  }

  return (
    <div className="wiki-workspace">
      <WikiUtilityRail view={wikiLocation.view} treeMode={treeMode} reviewCount={reviewCount} onViewChange={changeView} onTreeModeChange={setTreeMode} />
      <ResizablePane side="left" width={layout.treeWidth} min={190} max={360} collapsed={layout.treeCollapsed} onWidthChange={(treeWidth) => setLayout((current) => ({ ...current, treeWidth }))}>
        <KnowledgeTree activePath={wikiLocation.collectionPath} mode={treeMode} onModeChange={setTreeMode} collapsed={layout.treeCollapsed} onCollapse={() => setLayout((current) => ({ ...current, treeCollapsed: !current.treeCollapsed }))} />
      </ResizablePane>
      <section className="wiki-explorer" aria-label="Wiki 탐색">
        <header className="wiki-explorer__toolbar">
          <div><small>{collection.kind.toUpperCase()} COLLECTION</small><strong>{collection.title}</strong></div>
          <div className="wiki-view-switch" aria-label="Wiki 보기">
            <button type="button" aria-pressed={wikiLocation.view === 'docs'} onClick={() => changeView('docs')}>Docs</button>
            <button type="button" aria-pressed={wikiLocation.view === 'graph'} onClick={() => changeView('graph')}>Graph</button>
          </div>
        </header>
        {wikiLocation.view === 'docs' ? <div className="wiki-explorer__body">
          {collection.status === 'loading' ? <p className="topic-page-status" role="status">Wiki 컬렉션을 불러오는 중입니다.</p> : null}
          {collection.status === 'error' ? <p className="topic-page-status topic-page-status--error" role="alert">Wiki 컬렉션을 불러오지 못했습니다.</p> : null}
          {collection.status === 'ready' ? <>
            <div className="wiki-collection-summary"><span>{collection.summary}</span><b>{collection.topics.length} TOPICS</b></div>
            <TopicList topics={collection.topics} from={wikiLocation.collectionPath} />
          </> : null}
        </div> : <div className="wiki-graph-placeholder" role="status">관계 그래프를 준비하는 중입니다.</div>}
      </section>
      <ResizablePane side="right" width={layout.documentWidth} min={320} max={720} collapsed={layout.documentCollapsed} onWidthChange={(documentWidth) => setLayout((current) => ({ ...current, documentWidth }))}>
        <article className="wiki-document-pane" aria-label="Wiki 문서">
          <header><button type="button" onClick={() => setLayout((current) => ({ ...current, documentCollapsed: !current.documentCollapsed }))} aria-label={layout.documentCollapsed ? 'Wiki 문서 열기' : 'Wiki 문서 접기'}>{layout.documentCollapsed ? '‹' : '›'}</button></header>
          {layout.documentCollapsed ? null : <div className="wiki-document-pane__overview">
            <small>COLLECTION OVERVIEW</small>
            <h1>{collection.title}</h1>
            <p>{collection.summary || '좌측 트리에서 분류 축을 선택하거나 중앙에서 Topic을 선택하세요.'}</p>
            <dl>
              <div><dt>표시 Topic</dt><dd>{collection.topics.length}</dd></div>
              <div><dt>선택 모드</dt><dd>{wikiLocation.view === 'graph' ? 'Wiki Graph' : 'Wiki Docs'}</dd></div>
            </dl>
          </div>}
        </article>
      </ResizablePane>
    </div>
  )
}
