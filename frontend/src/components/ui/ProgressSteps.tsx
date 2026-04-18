import clsx from 'clsx'
import type { ReactElement, ReactNode } from 'react'

const DEFAULT_LABELS: [string, string, string, string] = [
  'Profile',
  'Requirements',
  'Preferences',
  'Dashboard',
]

export type ProgressStepsProps = {
  current: 1 | 2 | 3 | 'dashboard'
  labels?: [string, string, string, string]
  className?: string
}

function isActive(current: ProgressStepsProps['current'], stepIndex: number): boolean {
  if (current === 'dashboard') return stepIndex === 3
  return current === (stepIndex + 1) as 1 | 2 | 3
}

export function ProgressSteps({
  current,
  labels = DEFAULT_LABELS,
  className,
}: ProgressStepsProps): ReactElement {
  const parts: ReactNode[] = []
  for (let i = 0; i < 4; i += 1) {
    const num = String(i + 1).padStart(2, '0')
    const active = isActive(current, i)
    if (i > 0) {
      parts.push(
        <span key={`sep-${i}`} className="text-ink-muted">
          {' '}
          /{' '}
        </span>,
      )
    }
    parts.push(
      <span key={`step-${i}`} className="inline-flex items-baseline gap-1.5">
        <span
          className={clsx(
            'font-mono text-[13px]',
            active ? 'font-semibold text-ink' : 'font-normal text-ink-muted',
          )}
        >
          {num}
        </span>
        <span
          className={clsx(
            'font-sans text-[13px]',
            active ? 'font-semibold text-ink' : 'font-normal text-ink-muted',
          )}
        >
          {labels[i]}
        </span>
      </span>,
    )
  }
  return <p className={clsx('font-sans text-[13px] tracking-[0.08em]', className)}>{parts}</p>
}
