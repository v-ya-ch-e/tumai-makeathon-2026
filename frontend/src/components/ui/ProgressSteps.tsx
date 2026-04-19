import clsx from 'clsx'
import type { ReactElement, ReactNode } from 'react'
import { Link } from 'react-router-dom'

const DEFAULT_LABELS: [string, string, string] = [
  'Profile',
  'Requirements',
  'Preferences',
]

export type ProgressStepLink = {
  label: string
  href?: string
  disabled?: boolean
}

export type ProgressStepsProps = {
  current: 1 | 2 | 3 | 'dashboard'
  labels?: [string, string, string]
  steps?: [ProgressStepLink, ProgressStepLink, ProgressStepLink, ProgressStepLink?]
  className?: string
}

function isActive(current: ProgressStepsProps['current'], stepIndex: number): boolean {
  if (current === 'dashboard') return false
  return current === (stepIndex + 1) as 1 | 2 | 3
}

export function ProgressSteps({
  current,
  labels = DEFAULT_LABELS,
  steps,
  className,
}: ProgressStepsProps): ReactElement {
  const parts: ReactNode[] = []
  for (let i = 0; i < 3; i += 1) {
    const active = isActive(current, i)
    const step = steps?.[i]
    const label = step?.label ?? labels[i]
    const interactive = Boolean(step?.href) && !step?.disabled
    const labelClassName = clsx(
      'font-mono text-[11px] uppercase tracking-[0.22em]',
      active ? 'text-accent' : 'text-ink-muted',
      interactive ? 'transition-colors group-hover:text-ink' : null,
      step?.disabled ? 'opacity-50' : null,
    )
    if (i > 0) {
      parts.push(
        <span key={`sep-${i}`} className="text-ink-muted/60" aria-hidden>
          —
        </span>,
      )
    }
    parts.push(
      interactive ? (
        <Link key={`step-${i}`} to={step?.href ?? '#'} className={clsx('group inline-flex items-baseline', labelClassName)}>
          {label}
        </Link>
      ) : (
        <span key={`step-${i}`} className={labelClassName}>
          {label}
        </span>
      ),
    )
  }
  return (
    <p
      className={clsx(
        'flex flex-nowrap items-center gap-3 overflow-x-auto whitespace-nowrap',
        className,
      )}
    >
      {parts}
    </p>
  )
}
