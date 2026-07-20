import { Link } from 'react-router-dom'

interface WikiUtilityRailProps {
  view: 'docs' | 'graph'
  treeMode: 'knowledge' | 'evidence'
  reviewCount: number
  onViewChange: (view: 'docs' | 'graph') => void
  onTreeModeChange: (mode: 'knowledge' | 'evidence') => void
}

export function WikiUtilityRail({
  view, treeMode, reviewCount, onViewChange, onTreeModeChange,
}: WikiUtilityRailProps) {
  return (
    <nav className="wiki-utility-rail" aria-label="Wiki 도구">
      <Link className="wiki-utility-rail__mark" to="/wiki/topics" aria-label="Wiki 홈">WK</Link>
      <button type="button" className={view === 'docs' && treeMode === 'knowledge' ? 'is-active' : ''} onClick={() => {
        onTreeModeChange('knowledge'); onViewChange('docs')
      }} aria-label="Wiki Docs"><span>▤</span><small>Docs</small></button>
      <button type="button" className={treeMode === 'evidence' ? 'is-active' : ''} onClick={() => onTreeModeChange('evidence')} aria-label="Evidence"><span>⌁</span><small>근거</small></button>
      <button type="button" onClick={() => {
        onTreeModeChange('knowledge'); onViewChange('docs'); window.dispatchEvent(new Event('weekly-wiki:focus-search'))
      }} aria-label="Wiki 검색"><span>⌕</span><small>검색</small></button>
      <button type="button" className={view === 'graph' ? 'is-active' : ''} onClick={() => onViewChange('graph')} aria-label="Wiki Graph"><span>⌘</span><small>Graph</small></button>
      <Link className="wiki-utility-rail__link" to="/wiki/reviews" aria-label={`검토 ${reviewCount}건`}>
        <span>✓</span><small>검토</small>{reviewCount > 0 ? <b>{reviewCount}</b> : null}
      </Link>
      <span className="wiki-utility-rail__spacer" />
      <Link className="wiki-utility-rail__link" to="/classification" aria-label="분류 작업대"><span>↗</span><small>분류</small></Link>
    </nav>
  )
}
