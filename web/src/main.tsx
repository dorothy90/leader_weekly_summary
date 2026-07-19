import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import './styles/tokens.css'
import './styles/classification.css'
import './styles/wiki.css'

const ClassificationWorkbenchPage = lazy(() => import('./pages/ClassificationWorkbenchPage')
  .then((module) => ({ default: module.ClassificationWorkbenchPage })))
const WikiShell = lazy(() => import('./components/WikiShell')
  .then((module) => ({ default: module.WikiShell })))

function WikiPlaceholder({ mode }: { mode: string }) {
  return (
    <section className="wiki-placeholder" aria-labelledby="wiki-placeholder-title">
      <h1 id="wiki-placeholder-title">{mode}</h1>
      <dl>
        <dt>보기</dt><dd>{mode}</dd>
        <dt>상태</dt><dd>구성 예정</dd>
      </dl>
    </section>
  )
}

const root = document.getElementById('root')

if (!root) {
  throw new Error('Root element not found')
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Suspense fallback={<div role="status">화면을 불러오는 중입니다.</div>}>
        <Routes>
          <Route path="/classification" element={<ClassificationWorkbenchPage />} />
          <Route path="/wiki" element={<WikiShell />}>
            <Route index element={<Navigate to="topics" replace />} />
            <Route path="topics/*" element={<WikiPlaceholder mode="주제" />} />
            <Route path="lotcd/*" element={<WikiPlaceholder mode="LOTCD" />} />
            <Route path="teams/*" element={<WikiPlaceholder mode="팀" />} />
            <Route path="weeks/*" element={<WikiPlaceholder mode="주차" />} />
            <Route path="reviews/*" element={<WikiPlaceholder mode="분류 검토" />} />
          </Route>
          <Route path="*" element={<Navigate to="/classification" replace />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  </StrictMode>,
)
