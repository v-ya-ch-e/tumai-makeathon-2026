import clsx from 'clsx'

export type WeightSliderProps = {
  value: number
  onChange: (next: number) => void
  id?: string
  ariaLabel?: string
  className?: string
}

const STEPS: Array<{ position: number; label: string }> = [
  { position: 1, label: 'nice' },
  { position: 3, label: 'important' },
  { position: 5, label: 'must-have' },
]

const ACTIVE_DISTANCE = 0.6

export function WeightSlider({ value, onChange, id, ariaLabel, className }: WeightSliderProps) {
  const fillPct = ((value - 1) / 4) * 100

  return (
    <div className={clsx('w-full', className)}>
      <input
        id={id}
        type="range"
        min={1}
        max={5}
        step={0.1}
        value={value}
        aria-label={ariaLabel}
        onChange={(e) => onChange(Number(e.target.value))}
        onClick={(e) => e.stopPropagation()}
        className="weight-slider w-full cursor-pointer"
        style={{ '--fill-pct': `${fillPct}%` } as React.CSSProperties}
      />
      <div className="mt-2 flex justify-between">
        {STEPS.map((s) => {
          const isActive = Math.abs(value - s.position) <= ACTIVE_DISTANCE
          return (
            <span
              key={s.position}
              className={clsx(
                'font-mono text-[10px] uppercase tracking-[0.18em] transition-colors duration-150',
                isActive ? 'font-semibold text-accent' : 'text-ink-muted',
              )}
            >
              {s.label}
            </span>
          )
        })}
      </div>
    </div>
  )
}
