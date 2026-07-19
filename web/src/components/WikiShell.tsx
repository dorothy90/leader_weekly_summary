import { Link, NavLink, Outlet } from 'react-router-dom'

const wikiModes = [
  { label: '주제', to: '/wiki/topics' },
  { label: 'LOTCD', to: '/wiki/lotcd' },
  { label: '팀', to: '/wiki/teams' },
  { label: '주차', to: '/wiki/weeks' },
]

export function WikiShell() {
  return (
    <div className="wiki-shell">
      <header className="wiki-shell__header">
        <Link to="/wiki/topics" className="wiki-shell__brand">
          <strong>Weekly Knowledge Wiki</strong>
          <span>주간 기술 로그북</span>
        </Link>
        <nav className="wiki-shell__modes" aria-label="Wiki 탐색 모드">
          {wikiModes.map((mode) => (
            <NavLink key={mode.to} to={mode.to}>{mode.label}</NavLink>
          ))}
        </nav>
        <Link className="wiki-shell__classification-link" to="/classification">분류 작업대</Link>
      </header>
      <main className="wiki-shell__content">
        <Outlet />
      </main>
    </div>
  )
}
