import clsx from 'clsx'
import type { ReactNode } from 'react'

export type ChipProps = {
  selected: boolean
  onToggle: () => void
  children: ReactNode
  className?: string
}

export function Chip({ selected, onToggle, children, className }: ChipProps) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={clsx(
        'inline-flex min-h-9 items-center rounded-full border px-3.5 py-1.5 text-[12px] uppercase tracking-[0.14em] transition-all duration-150 ease-out',
        selected
          ? 'border-accent bg-accent-muted text-ink shadow-[0_8px_18px_rgba(140,85,52,0.12)]'
          : 'border-hairline bg-surface text-ink-muted hover:border-accent/30 hover:text-ink',
        className,
      )}
    >
      {children}
    </button>
  )
}
