import type { ReactNode } from 'react'
import { Button, ProgressSteps } from './ui'

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
  aside?: ReactNode
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
  aside,
}: OnboardingShellProps) {
  const disabled = busy || nextDisabled
  const primaryLabel = busy ? 'Continuing…' : nextLabel

  return (
    <div className="relative min-h-screen overflow-hidden bg-canvas">
      <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_top_left,_rgba(138,90,59,0.18),_transparent_38%),radial-gradient(circle_at_bottom_right,_rgba(107,142,90,0.12),_transparent_28%)]" />
      <div className="relative mx-auto max-w-7xl px-6 py-6 sm:px-8 lg:px-10">
        {showProgress ? (
          <div className="mb-8 rounded-full border border-hairline/80 bg-surface/80 px-5 py-3 backdrop-blur-sm">
            <ProgressSteps current={step} />
          </div>
        ) : null}

        <div className="grid gap-8 lg:grid-cols-[minmax(0,1.25fr)_320px] xl:grid-cols-[minmax(0,1.3fr)_360px]">
          <section className="overflow-hidden rounded-[32px] border border-hairline/80 bg-surface/92 shadow-[0_24px_80px_rgba(43,38,35,0.08)] backdrop-blur-sm">
            <div className="border-b border-hairline/80 px-6 py-8 sm:px-8 lg:px-10">
              {eyebrow ? (
                <p className="mb-3 font-mono text-[12px] uppercase tracking-[0.28em] text-accent">
                  {eyebrow}
                </p>
              ) : null}
              <h1 className="max-w-2xl text-[32px] font-semibold tracking-[-0.03em] text-ink sm:text-[40px]">
                {title}
              </h1>
              {description ? (
                <p className="mt-4 max-w-2xl text-[16px] leading-7 text-ink-muted">{description}</p>
              ) : null}
            </div>

            <div className="px-6 py-8 sm:px-8 lg:px-10">{children}</div>

            <div className="border-t border-hairline/80 px-6 py-5 sm:px-8 lg:px-10">
              {footer ? <div className="mb-4">{footer}</div> : null}
              <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  {onBack ? (
                    <Button variant="secondary" type="button" onClick={onBack} disabled={busy}>
                      Back
                    </Button>
                  ) : (
                    <p className="text-[13px] text-ink-muted">We save only what the hunt needs.</p>
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

          <aside className="space-y-4 lg:sticky lg:top-6 lg:self-start">
            <div className="rounded-[28px] border border-hairline/80 bg-[#f8f2e7]/94 p-6 shadow-[0_24px_70px_rgba(138,90,59,0.12)] backdrop-blur-sm">
              <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">Step {step}</p>
              <p className="mt-3 text-[22px] font-semibold tracking-[-0.02em] text-ink">
                Build your hunt profile.
              </p>
              <p className="mt-3 text-[14px] leading-6 text-ink-muted">
                The better this brief is, the better the agent can rank rooms for you.
              </p>
            </div>
            {aside ? aside : null}
          </aside>
        </div>
      </div>
    </div>
  )
}
