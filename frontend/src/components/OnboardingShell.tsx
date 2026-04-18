import type { ReactNode } from 'react'
import { Button, ProgressSteps } from './ui'
import type { ProgressStepLink } from './ui/ProgressSteps'

export type OnboardingShellProps = {
  step: 1 | 2 | 3
  title: string
  description?: string
  eyebrow?: string
  children: ReactNode
  onBack?: () => void
  onNext: () => void
  nextLabel?: string
  nextDisabled?: boolean
  busy?: boolean
  footer?: ReactNode
  showProgress?: boolean
  hideIntro?: boolean
  aside?: ReactNode
  progressSteps?: [ProgressStepLink, ProgressStepLink, ProgressStepLink, ProgressStepLink]
}

export function OnboardingShell({
  step,
  title,
  description,
  eyebrow,
  children,
  onBack,
  onNext,
  nextLabel = 'Continue',
  nextDisabled = false,
  busy = false,
  footer,
  showProgress = true,
  hideIntro = false,
  aside,
  progressSteps,
}: OnboardingShellProps) {
  const disabled = busy || nextDisabled
  const primaryLabel = busy ? 'Continuing…' : nextLabel

  return (
    <div className="min-h-screen bg-canvas">
      <div className="app-shell">
        <div className="mb-8 flex flex-col gap-5 border-b border-hairline pb-5 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p className="brand-wordmark">Sherlock Homes</p>
            <p className="mt-1 max-w-xl text-[14px] text-ink-muted">
              A focused rental search experience built to help you compare options with confidence.
            </p>
          </div>
          {showProgress ? <ProgressSteps current={step} steps={progressSteps} /> : null}
        </div>

        <div className={aside ? 'grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]' : 'grid gap-6'}>
          <section className="page-frame overflow-hidden">
            {hideIntro ? null : (
              <div className="border-b border-hairline px-6 py-8 sm:px-8 lg:px-10">
                {eyebrow ? (
                  <p className="section-kicker mb-3 text-accent">
                    {eyebrow}
                  </p>
                ) : null}
                <h1 className="page-title max-w-2xl">
                  {title}
                </h1>
                {description ? (
                  <p className="body-copy mt-4 max-w-2xl">{description}</p>
                ) : null}
              </div>
            )}

            <div className="px-6 py-8 sm:px-8 lg:px-10">
              {children}
            </div>

            <div className="border-t border-hairline px-6 py-5 sm:px-8 lg:px-10">
              {footer ? <div className="mb-4">{footer}</div> : null}
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  {onBack ? (
                    <Button variant="secondary" type="button" onClick={onBack} disabled={busy}>
                      Back
                    </Button>
                  ) : (
                    <p className="text-[13px] text-ink-muted">You can revisit these details later.</p>
                  )}
                </div>
                <div className="flex items-center justify-end gap-3">
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
            </div>
          </section>

          {aside ? <aside className="hidden space-y-4 lg:sticky lg:top-6 lg:block lg:self-start">{aside}</aside> : null}
        </div>
      </div>
    </div>
  )
}
