import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { ExplorerPage } from './pages/ExplorerPage'
import { MappingPage } from './pages/MappingPage'
import { ClassificationWorkbenchPage } from './pages/ClassificationWorkbenchPage'
import './styles/tokens.css'
import './styles/app.css'
import './styles/wiki.css'

const WikiGraphPage = lazy(() => import('./pages/WikiGraphPage'))

const root = document.getElementById('root')

if (!root) {
  throw new Error('Root element not found')
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/explorer/:domain?/:tech?/:lotcd?" element={<ExplorerPage classic />} />
        <Route path="/wiki" element={<Navigate to="/wiki/docs" replace />} />
        <Route path="/wiki/docs/:domain?/:tech?/:lotcd?" element={<ExplorerPage />} />
        <Route
          path="/wiki/graph"
          element={
            <Suspense fallback={<div className="wiki-route-loading">Graph loading…</div>}>
              <WikiGraphPage />
            </Suspense>
          }
        />
        <Route path="/review" element={<Navigate to="/wiki/docs?review=pending" replace />} />
        <Route path="/mappings" element={<MappingPage />} />
        <Route path="/classification" element={<ClassificationWorkbenchPage />} />
        <Route path="*" element={<Navigate to="/wiki/docs" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
