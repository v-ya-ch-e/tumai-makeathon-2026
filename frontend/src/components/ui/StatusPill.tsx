import clsx from 'clsx'
import type { ReactNode } from 'react'

export type StatusPillTone =
  | 'idle'
  | 'running'
  | 'rescanning'
  | 'good'
  | 'warn'
  | 'bad'

export type StatusPillProps = {
  tone: StatusPillTone
  children: ReactNode
  className?: string
}

function dotClassForTone(tone: StatusPillTone): string {
  if (tone === 'idle') return 'bg-ink-muted'
  if (tone === 'running' || tone === 'good') return 'bg-good'
  if (tone === 'rescanning' || tone === 'warn') return 'bg-warn'
  return 'bg-bad'
}

export function StatusPill({ tone, children, className }: StatusPillProps) {
  return (
    <span
      className={clsx(
        'inline-flex h-7 items-center gap-2 rounded-full border border-hairline bg-surface px-3 text-[13px] text-ink',
        className,
      )}
    >
      <span
        className={clsx(
          'inline-block h-1.5 w-1.5 shrink-0 rounded-full',
          dotClassForTone(tone),
        )}
        aria-hidden
      />
      {children}
    </span>
  )
}
