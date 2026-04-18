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
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-[-8rem] top-16 h-56 w-56 rounded-full border border-accent/10" />
        <div className="absolute right-10 top-24 h-32 w-32 rounded-full border border-good/15" />
        <div className="absolute bottom-10 left-1/3 h-24 w-24 rounded-full border border-accent/10" />
      </div>
      <div className="relative mx-auto max-w-7xl px-5 py-5 sm:px-8 lg:px-10">
        {showProgress ? (
          <div className="mb-8 flex items-center justify-between gap-4 rounded-[24px] border border-hairline/80 bg-surface/90 px-5 py-4 shadow-[0_18px_38px_rgba(39,33,29,0.05)]">
            <ProgressSteps current={step} />
            <span className="hidden font-mono text-[11px] uppercase tracking-[0.24em] text-ink-muted sm:inline">
              WG Hunter
            </span>
          </div>
        ) : null}

        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.3fr)_340px] xl:grid-cols-[minmax(0,1.35fr)_380px]">
          <section className="overflow-hidden rounded-[32px] border border-hairline/80 bg-surface/95 shadow-[0_30px_80px_rgba(39,33,29,0.08)]">
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

            <div className="bg-[linear-gradient(180deg,rgba(255,250,242,0.24),rgba(255,250,242,0))] px-6 py-8 sm:px-8 lg:px-10">
              {children}
            </div>

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
            <div className="overflow-hidden rounded-[28px] border border-hairline/80 bg-[#f6ecdd] p-0 shadow-[0_26px_60px_rgba(140,85,52,0.12)]">
              <div className="border-b border-hairline/80 px-6 py-5">
                <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">Step {step}</p>
                <p className="mt-3 text-[22px] font-semibold tracking-[-0.02em] text-ink">
                  Build your hunt profile.
                </p>
              </div>
              <div className="space-y-5 px-6 py-5">
                <p className="text-[14px] leading-6 text-ink-muted">
                  The sharper this brief is, the more confidently the agent can reject weak rooms and surface real contenders.
                </p>
                <div className="grid gap-3">
                  {[
                    'Search basics first',
                    'Tune preferences second',
                    'Watch ranked results live',
                  ].map((item, index) => (
                    <div
                      key={item}
                      className="flex items-start gap-3 rounded-2xl border border-hairline/70 bg-surface-raised/80 px-4 py-3"
                    >
                      <span className="font-mono text-[12px] text-accent">
                        0{index + 1}
                      </span>
                      <span className="text-[13px] leading-5 text-ink">{item}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            {aside ? aside : null}
          </aside>
        </div>
      </div>
    </div>
  )
}
