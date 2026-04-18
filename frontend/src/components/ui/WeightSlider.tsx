import clsx from 'clsx'

export type WeightSliderProps = {
  value: number
  onChange: (next: number) => void
  id?: string
  ariaLabel?: string
  className?: string
}

const LEGEND: Array<{ position: number; label: string }> = [
  { position: 1, label: 'nice' },
  { position: 3, label: 'important' },
  { position: 5, label: 'must-have' },
]

export function WeightSlider({
  value,
  onChange,
  id,
  ariaLabel,
  className,
}: WeightSliderProps) {
  return (
    <div className={clsx('w-full', className)}>
      <input
        id={id}
        type="range"
        min={1}
        max={5}
        step={1}
        value={value}
        aria-label={ariaLabel}
        onChange={(e) => onChange(Number(e.target.value))}
        onClick={(e) => e.stopPropagation()}
        className="h-1 w-full cursor-pointer appearance-none rounded-full bg-hairline accent-accent focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-0"
      />
      <div className="mt-1 flex justify-between text-[11px] text-ink-muted">
        {LEGEND.map((l) => (
          <span
            key={l.position}
            className={clsx(value === l.position ? 'text-ink' : undefined)}
          >
            {l.label}
          </span>
        ))}
      </div>
    </div>
  )
}
