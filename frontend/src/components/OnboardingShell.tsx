import type { ReactNode } from 'react'
import { Button, ProgressSteps } from './ui'

export type OnboardingShellProps = {
  step: 1 | 2 | 3
  title: string
  description?: string
  children: ReactNode
  onBack?: () => void
  onNext: () => void
  nextLabel?: string
  nextDisabled?: boolean
  busy?: boolean
  footer?: ReactNode
}

export function OnboardingShell({
  step,
  title,
  description,
  children,
  onBack,
  onNext,
  nextLabel = 'Continue',
  nextDisabled = false,
  busy = false,
  footer,
}: OnboardingShellProps) {
  const disabled = busy || nextDisabled
  const primaryLabel = busy ? 'Continuing…' : nextLabel

  return (
    <div className="min-h-screen bg-canvas">
      <div className="w-full px-6 py-6">
        <ProgressSteps current={step} />
      </div>
      <div className="mx-auto max-w-xl space-y-8 px-6 py-12">
        <h1 className="font-sans text-[28px] font-semibold tracking-tight text-ink">{title}</h1>
        {description ? (
          <p className="text-[15px] text-ink-muted">{description}</p>
        ) : null}
        {children}
        <div className="flex items-center justify-between border-t border-hairline pt-4">
          <div>
            {onBack ? (
              <Button variant="secondary" type="button" onClick={onBack} disabled={busy}>
                Back
              </Button>
            ) : null}
          </div>
          <div className="flex items-center gap-3">
            {busy ? (
              <span
                className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-hairline border-t-accent"
                aria-hidden
              />
            ) : null}
            <Button variant="primary" type="button" onClick={onNext} disabled={disabled}>
              {primaryLabel}
            </Button>
          </div>
        </div>
        {footer}
      </div>
    </div>
  )
}
