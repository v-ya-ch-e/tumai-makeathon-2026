import clsx from 'clsx'

export type ProgressBarProps = {
  /** Current progress value. Clamped to `[0, max]` before rendering. */
  value: number
  /** Total work. Values `<= 0` render a fully-muted track. */
  max: number
  /** Extra classes for the outer track element. */
  className?: string
  /** Accessible label applied to the underlying `role="progressbar"` element. */
  'aria-label'?: string
}

/**
 * Thin 6-px rail with an animated fill, styled with the Sherlock Homes ink
 * tokens so it feels native to the dashboard. Use for determinate progress
 * (e.g. the silent backfill's `done / total`), not indeterminate spinners.
 */
export function ProgressBar({
  value,
  max,
  className,
  'aria-label': ariaLabel,
}: ProgressBarProps) {
  const safeMax = max > 0 ? max : 0
  const safeValue = Math.min(Math.max(value, 0), safeMax)
  const pct = safeMax === 0 ? 0 : (safeValue / safeMax) * 100
  return (
    <div
      role="progressbar"
      aria-label={ariaLabel}
      aria-valuemin={0}
      aria-valuemax={safeMax}
      aria-valuenow={safeValue}
      className={clsx(
        'relative h-1.5 w-full overflow-hidden rounded-full bg-ink-muted/20',
        className,
      )}
    >
      <div
        className="absolute inset-y-0 left-0 rounded-full bg-ink transition-[width] duration-300 ease-out"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
