import clsx from 'clsx'
import type { ReactNode } from 'react'

export type CardProps = {
  children: ReactNode
  className?: string
}

export function Card({ children, className }: CardProps) {
  return (
    <div
      className={clsx(
        'rounded-card border border-hairline bg-surface p-4 shadow-[0_18px_45px_rgba(39,33,29,0.05)]',
        className,
      )}
    >
      {children}
    </div>
  )
}
