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
        'inline-flex items-center justify-center rounded font-medium transition-colors duration-150 ease-out',
        'outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-canvas',
        size === 'md' && 'h-10 px-4 text-[15px]',
        size === 'sm' && 'h-8 px-3 text-[13px]',
        variant === 'primary' && 'bg-accent text-canvas hover:bg-[#79492d]',
        variant === 'secondary' && 'bg-transparent text-ink hover:bg-surface',
        variant === 'destructive' && 'bg-bad text-canvas hover:opacity-90',
        className,
      )}
      {...props}
    />
  )
})
