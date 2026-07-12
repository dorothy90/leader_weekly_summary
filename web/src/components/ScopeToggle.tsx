import type { ScopeMode } from '../types'

interface ScopeToggleProps {
  value: ScopeMode
  onChange: (value: ScopeMode) => void
  disabled: boolean
}

export function ScopeToggle({ value, onChange, disabled }: ScopeToggleProps) {
  return (
    <div className="scope-toggle" aria-label="agenda 범위">
      <button
        type="button"
        className={value === 'direct' ? 'is-active' : ''}
        onClick={() => onChange('direct')}
        disabled={disabled}
      >
        직접 언급
      </button>
      <button
        type="button"
        className={value === 'descendants' ? 'is-active' : ''}
        onClick={() => onChange('descendants')}
      >
        하위 포함
      </button>
    </div>
  )
}
