import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'

const ClassificationWorkbenchPage = lazy(() => import('./pages/ClassificationWorkbenchPage')
  .then((module) => ({ default: module.ClassificationWorkbenchPage })))
const WikiShell = lazy(() => import('./components/WikiShell')
  .then((module) => ({ default: module.WikiShell })))
const TopicIndexPage = lazy(() => import('./pages/TopicIndexPage')
  .then((module) => ({ default: module.TopicIndexPage })))
const TopicPage = lazy(() => import('./pages/TopicPage')
  .then((module) => ({ default: module.TopicPage })))
const LotcdWikiPage = lazy(() => import('./pages/LotcdWikiPage')
  .then((module) => ({ default: module.LotcdWikiPage })))
const TeamWikiPage = lazy(() => import('./pages/TeamWikiPage')
  .then((module) => ({ default: module.TeamWikiPage })))
const WeekWikiPage = lazy(() => import('./pages/WeekWikiPage')
  .then((module) => ({ default: module.WeekWikiPage })))
const WikiReviewPage = lazy(() => import('./pages/WikiReviewPage')
  .then((module) => ({ default: module.WikiReviewPage })))

export function AppRoutes() {
  return (
    <Suspense fallback={<div role="status">화면을 불러오는 중입니다.</div>}>
      <Routes>
        <Route path="/classification" element={<ClassificationWorkbenchPage />} />
        <Route path="/wiki" element={<WikiShell />}>
          <Route index element={<Navigate to="topics" replace />} />
          <Route path="topics" element={<TopicIndexPage />} />
          <Route path="topics/:topicId" element={<TopicPage />} />
          <Route path="lotcd/*" element={<LotcdWikiPage />} />
          <Route path="teams/*" element={<TeamWikiPage />} />
          <Route path="weeks/*" element={<WeekWikiPage />} />
          <Route path="reviews/*" element={<WikiReviewPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/classification" replace />} />
      </Routes>
    </Suspense>
  )
}
