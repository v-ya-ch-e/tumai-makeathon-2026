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
        'inline-flex min-h-9 items-center rounded border px-3 py-1.5 text-[12px] transition-colors duration-150 ease-out',
        selected
          ? 'border-accent bg-accent-muted text-ink'
          : 'border-hairline bg-surface text-ink-muted hover:border-ink hover:text-ink',
        className,
      )}
    >
      {children}
    </button>
  )
}
