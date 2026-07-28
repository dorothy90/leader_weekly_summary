interface StepIndicatorProps {
  currentStep: 1 | 2
}

const steps = ['계정 연결', '폴더 선택'] as const

export function StepIndicator({ currentStep }: StepIndicatorProps) {
  return (
    <nav className="step-indicator" aria-label="설정 진행 단계">
      <ol>
        {steps.map((label, index) => {
          const step = (index + 1) as 1 | 2
          const isActive = step === currentStep
          const isComplete = step < currentStep

          return (
            <li
              className={isActive ? 'is-active' : isComplete ? 'is-complete' : ''}
              key={label}
              aria-current={isActive ? 'step' : undefined}
            >
              <span className="step-number" aria-hidden="true">
                {isComplete ? '✓' : step}
              </span>
              <span>{label}</span>
            </li>
          )
        })}
      </ol>
    </nav>
  )
}
