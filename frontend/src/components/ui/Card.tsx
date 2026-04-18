import clsx from 'clsx'
import type { ReactNode } from 'react'

export type CardProps = {
  children: ReactNode
  className?: string
}

export function Card({ children, className }: CardProps) {
  return (
    <div className={clsx('rounded-card border border-hairline bg-surface p-4', className)}>
      {children}
    </div>
  )
}
