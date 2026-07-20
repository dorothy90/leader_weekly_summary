import type { MouseEvent as ReactMouseEvent, ReactNode } from 'react'

interface ResizablePaneProps {
  side: 'left' | 'right'
  width: number
  min: number
  max: number
  collapsed: boolean
  onWidthChange: (width: number) => void
  children: ReactNode
}

export function ResizablePane({ side, width, min, max, collapsed, onWidthChange, children }: ResizablePaneProps) {
  function startResize(event: ReactMouseEvent<HTMLDivElement>) {
    event.preventDefault()
    const initialX = event.clientX
    const initialWidth = width
    const move = (moveEvent: MouseEvent) => {
      const delta = moveEvent.clientX - initialX
      const next = side === 'left' ? initialWidth + delta : initialWidth - delta
      onWidthChange(Math.max(min, Math.min(max, next)))
    }
    const stop = () => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', stop)
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', stop)
  }

  return (
    <div className={`wiki-resizable wiki-resizable--${side} ${collapsed ? 'is-collapsed' : ''}`} style={{ width: collapsed ? 38 : width }}>
      {side === 'right' ? <div className="wiki-resizable__handle" role="separator" aria-orientation="vertical" onMouseDown={startResize} /> : null}
      <div className="wiki-resizable__content">{children}</div>
      {side === 'left' ? <div className="wiki-resizable__handle" role="separator" aria-orientation="vertical" onMouseDown={startResize} /> : null}
    </div>
  )
}
