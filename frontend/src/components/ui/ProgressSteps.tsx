import clsx from 'clsx'
import type { ReactElement, ReactNode } from 'react'
import { Link } from 'react-router-dom'

const DEFAULT_LABELS: [string, string, string, string] = [
  'Profile',
  'Requirements',
  'Preferences',
  'Dashboard',
]

export type ProgressStepLink = {
  label: string
  href?: string
  disabled?: boolean
}

export type ProgressStepsProps = {
  current: 1 | 2 | 3 | 'dashboard'
  labels?: [string, string, string, string]
  steps?: [ProgressStepLink, ProgressStepLink, ProgressStepLink, ProgressStepLink]
  className?: string
}

function isActive(current: ProgressStepsProps['current'], stepIndex: number): boolean {
  if (current === 'dashboard') return stepIndex === 3
  return current === (stepIndex + 1) as 1 | 2 | 3
}

export function ProgressSteps({
  current,
  labels = DEFAULT_LABELS,
  steps,
  className,
}: ProgressStepsProps): ReactElement {
  const parts: ReactNode[] = []
  for (let i = 0; i < 4; i += 1) {
    const num = String(i + 1).padStart(2, '0')
    const active = isActive(current, i)
    const step = steps?.[i]
    const label = step?.label ?? labels[i]
    const interactive = Boolean(step?.href) && !step?.disabled
    const textClassName = clsx(
      'font-sans text-[13px]',
      active ? 'font-semibold text-ink' : 'font-normal text-ink-muted',
      interactive ? 'transition-colors group-hover:text-ink' : null,
      step?.disabled ? 'opacity-50' : null,
    )
    if (i > 0) {
      parts.push(
        <span key={`sep-${i}`} className="text-ink-muted">
          {' '}
          /{' '}
        </span>,
      )
    }
    const content = (
      <>
        <span
          className={clsx(
            'font-mono text-[13px]',
            active ? 'font-semibold text-ink' : 'font-normal text-ink-muted',
            interactive ? 'transition-colors group-hover:text-ink' : null,
            step?.disabled ? 'opacity-50' : null,
          )}
        >
          {num}
        </span>
        <span className={textClassName}>{label}</span>
      </>
    )
    parts.push(
      interactive ? (
        <Link key={`step-${i}`} to={step?.href ?? '#'} className="group inline-flex items-baseline gap-1.5">
          {content}
        </Link>
      ) : (
        <span key={`step-${i}`} className="inline-flex items-baseline gap-1.5">
          {content}
        </span>
      ),
    )
  }
  return <p className={clsx('font-sans text-[13px] tracking-[0.08em]', className)}>{parts}</p>
}
