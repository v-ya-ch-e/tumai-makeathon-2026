import clsx from 'clsx'
import { forwardRef } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'destructive'
export type ButtonSize = 'md' | 'sm'

export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', className, type = 'button', ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={clsx(
        'inline-flex items-center justify-center rounded border font-medium tracking-[-0.01em] transition-colors duration-150 ease-out',
        'outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-canvas',
        'disabled:cursor-not-allowed disabled:opacity-55',
        size === 'md' && 'h-11 px-4 text-[14px]',
        size === 'sm' && 'h-9 px-3 text-[12px]',
        variant === 'primary' &&
          'border-accent bg-accent text-surface hover:border-[#6b472f] hover:bg-[#6b472f]',
        variant === 'secondary' &&
          'border-hairline bg-transparent text-ink hover:border-ink hover:bg-surface-raised',
        variant === 'destructive' &&
          'border-bad bg-transparent text-bad hover:bg-bad/5',
        className,
      )}
      {...props}
    />
  )
})
