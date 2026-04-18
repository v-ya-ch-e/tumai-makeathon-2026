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
        'inline-flex items-center justify-center rounded border font-medium tracking-[-0.01em] transition-all duration-150 ease-out',
        'outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-canvas',
        'disabled:cursor-not-allowed disabled:opacity-55 disabled:shadow-none',
        size === 'md' && 'h-11 px-4 text-[14px]',
        size === 'sm' && 'h-9 px-3.5 text-[12px] uppercase tracking-[0.14em]',
        variant === 'primary' &&
          'border-accent bg-accent text-canvas shadow-[0_10px_24px_rgba(140,85,52,0.18)] hover:-translate-y-px hover:bg-[#79492d] hover:shadow-[0_14px_30px_rgba(140,85,52,0.22)]',
        variant === 'secondary' &&
          'border-hairline bg-surface-raised text-ink hover:-translate-y-px hover:border-accent/30 hover:bg-surface',
        variant === 'destructive' &&
          'border-bad bg-bad text-canvas shadow-[0_10px_24px_rgba(164,90,69,0.16)] hover:-translate-y-px hover:opacity-95',
        className,
      )}
      {...props}
    />
  )
})
