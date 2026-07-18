import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { ClassificationWorkbenchPage } from './pages/ClassificationWorkbenchPage'
import './styles/tokens.css'
import './styles/classification.css'

const root = document.getElementById('root')

if (!root) {
  throw new Error('Root element not found')
}

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/classification" element={<ClassificationWorkbenchPage />} />
        <Route path="*" element={<Navigate to="/classification" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
