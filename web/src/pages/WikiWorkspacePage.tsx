import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import { fetchWikiReviews } from '../api/knowledge'
import { KnowledgeTree } from '../components/wiki/KnowledgeTree'
import { ResizablePane } from '../components/wiki/ResizablePane'
import { WikiDocumentPane } from '../components/wiki/WikiDocumentPane'
import { WikiUtilityRail } from '../components/wiki/WikiUtilityRail'
import { useWikiCollection } from '../components/wiki/useWikiCollection'
import { parseWikiLocation } from '../components/wiki/wikiLocation'

const LAYOUT_KEY = 'weekly-wiki:layout:v1'
const WikiGraph = lazy(() => import('../components/wiki/graph/WikiGraph'))

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
  const graphScopeIds = useMemo(() => new Set(collection.topics.map((topic) => topic.topic_id)), [collection.topics])

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

  function selectTopic(topicId: string) {
    const params = new URLSearchParams()
    params.set('from', wikiLocation.collectionPath)
    if (wikiLocation.view === 'graph') params.set('view', 'graph')
    navigate(`/wiki/topics/${encodeURIComponent(topicId)}?${params}`)
  }

  return (
    <div className="wiki-workspace">
      <WikiUtilityRail view={wikiLocation.view} treeMode={treeMode} reviewCount={reviewCount} onViewChange={changeView} onTreeModeChange={setTreeMode} />
      <ResizablePane side="left" width={layout.treeWidth} min={190} max={360} collapsed={layout.treeCollapsed} onWidthChange={(treeWidth) => setLayout((current) => ({ ...current, treeWidth }))}>
        <KnowledgeTree activePath={wikiLocation.collectionPath} kind={wikiLocation.kind} collapsed={layout.treeCollapsed} onCollapse={() => setLayout((current) => ({ ...current, treeCollapsed: !current.treeCollapsed }))} />
      </ResizablePane>
      <section className="wiki-explorer" aria-label="Wiki 탐색">
        <header className="wiki-explorer__toolbar">
          <div><small>{collection.kind.toUpperCase()} COLLECTION</small><strong>{collection.title}</strong></div>
          <div className="wiki-view-switch" aria-label="Wiki 보기">
            <button type="button" aria-pressed={wikiLocation.view === 'docs'} onClick={() => changeView('docs')}>Docs</button>
            <button type="button" aria-pressed={wikiLocation.view === 'graph'} onClick={() => changeView('graph')}>Graph</button>
          </div>
        </header>
        {wikiLocation.view === 'docs' ? <div className="wiki-explorer__body wiki-explorer__body--document">
          <WikiDocumentPane topicId={wikiLocation.topicId} collection={collection} onSelectTopic={selectTopic} />
        </div> : <Suspense fallback={<div className="wiki-graph-placeholder" role="status">관계 그래프를 불러오는 중입니다.</div>}>
          <WikiGraph scopeIds={graphScopeIds} selectedTopicId={wikiLocation.topicId} onSelectTopic={selectTopic} />
        </Suspense>}
      </section>
      {wikiLocation.view === 'graph' && wikiLocation.topicId ? <ResizablePane side="right" width={layout.documentWidth} min={320} max={720} collapsed={layout.documentCollapsed} onWidthChange={(documentWidth) => setLayout((current) => ({ ...current, documentWidth }))}>
        <div className="wiki-document-pane">
          <header><button type="button" onClick={() => setLayout((current) => ({ ...current, documentCollapsed: !current.documentCollapsed }))} aria-label={layout.documentCollapsed ? 'Wiki 문서 열기' : 'Wiki 문서 접기'}>{layout.documentCollapsed ? '‹' : '›'}</button></header>
          {layout.documentCollapsed ? null : <WikiDocumentPane topicId={wikiLocation.topicId} collection={collection} onSelectTopic={selectTopic} />}
        </div>
      </ResizablePane> : null}
    </div>
  )
}
