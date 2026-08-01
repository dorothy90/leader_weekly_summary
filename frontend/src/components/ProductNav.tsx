import type { MouseEvent } from 'react'

interface ProductNavProps {
  currentPath: string
  onNavigate: (path: '/' | '/rag') => void
}

export function ProductNav({ currentPath, onNavigate }: ProductNavProps) {
  const navigate = (event: MouseEvent<HTMLAnchorElement>, path: '/' | '/rag') => {
    event.preventDefault()
    onNavigate(path)
  }

  return (
    <nav className="product-nav" aria-label="제품 화면">
      <a
        className={currentPath === '/' ? 'is-active' : ''}
        href="/"
        aria-current={currentPath === '/' ? 'page' : undefined}
        onClick={(event) => navigate(event, '/')}
      >
        Folder setup
      </a>
      <a
        className={currentPath === '/rag' ? 'is-active' : ''}
        href="/rag"
        aria-current={currentPath === '/rag' ? 'page' : undefined}
        onClick={(event) => navigate(event, '/rag')}
      >
        RAG Lab
      </a>
    </nav>
  )
}
