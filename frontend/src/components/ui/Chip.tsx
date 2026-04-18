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
        'inline-flex h-8 items-center rounded-full border px-3 text-[13px] transition-colors duration-150 ease-out',
        selected
          ? 'border-accent bg-accent-muted text-ink'
          : 'border-hairline bg-surface text-ink',
        className,
      )}
    >
      {children}
    </button>
  )
}
