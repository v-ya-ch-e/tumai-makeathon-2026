import clsx from 'clsx'
import { forwardRef, type ReactNode } from 'react'

export type ButtonVariant = 'primary' | 'secondary' | 'destructive'
export type ButtonSize = 'md' | 'sm'
export type ButtonShape = 'default' | 'pill'

export type ButtonProps = Omit<
  React.ButtonHTMLAttributes<HTMLButtonElement>,
  'children'
> & {
  variant?: ButtonVariant
  size?: ButtonSize
  shape?: ButtonShape
  iconLeft?: ReactNode
  iconRight?: ReactNode
  children?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    shape,
    iconLeft,
    iconRight,
    className,
    type = 'button',
    children,
    ...props
  },
  ref,
) {
  // Primary buttons default to the pill shape used by the reference designs.
  const resolvedShape: ButtonShape = shape ?? (variant === 'primary' ? 'pill' : 'default')
  return (
    <button
      ref={ref}
      type={type}
      className={clsx(
        'inline-flex items-center justify-center gap-2 border font-medium tracking-[-0.01em] transition-colors duration-150 ease-out',
        'outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-canvas',
        'disabled:cursor-not-allowed disabled:opacity-55',
        resolvedShape === 'pill' ? 'rounded-full' : 'rounded',
        size === 'md' && 'h-11 px-5 text-[14px]',
        size === 'sm' && 'h-9 px-4 text-[12px]',
        variant === 'primary' &&
          'border-accent bg-accent text-white hover:border-[#185f3c] hover:bg-[#185f3c]',
        variant === 'secondary' &&
          'border-hairline bg-transparent text-ink hover:border-ink hover:bg-surface-raised',
        variant === 'destructive' &&
          'border-bad bg-transparent text-bad hover:bg-bad/5',
        className,
      )}
      {...props}
    >
      {iconLeft ? <span aria-hidden className="inline-flex shrink-0">{iconLeft}</span> : null}
      {children}
      {iconRight ? <span aria-hidden className="inline-flex shrink-0">{iconRight}</span> : null}
    </button>
  )
})
