import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

const ClassificationWorkbenchPage = lazy(() => import('./pages/ClassificationWorkbenchPage')
  .then((module) => ({ default: module.ClassificationWorkbenchPage })))
const WikiShell = lazy(() => import('./components/WikiShell')
  .then((module) => ({ default: module.WikiShell })))
const WikiWorkspacePage = lazy(() => import('./pages/WikiWorkspacePage')
  .then((module) => ({ default: module.WikiWorkspacePage })))
const WikiReviewPage = lazy(() => import('./pages/WikiReviewPage')
  .then((module) => ({ default: module.WikiReviewPage })))

export function AppRoutes() {
  return (
    <Suspense fallback={<div role="status">화면을 불러오는 중입니다.</div>}>
      <Routes>
        <Route path="/classification" element={<ClassificationWorkbenchPage />} />
        <Route path="/wiki" element={<WikiShell />}>
          <Route index element={<Navigate to="topics" replace />} />
          <Route path="topics" element={<WikiWorkspacePage />} />
          <Route path="topics/:topicId" element={<WikiWorkspacePage />} />
          <Route path="lotcd/*" element={<WikiWorkspacePage />} />
          <Route path="teams/*" element={<WikiWorkspacePage />} />
          <Route path="weeks/*" element={<WikiWorkspacePage />} />
          <Route path="reviews/*" element={<WikiReviewPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/classification" replace />} />
      </Routes>
    </Suspense>
  )
}
