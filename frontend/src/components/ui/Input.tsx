import clsx from 'clsx'
import { forwardRef } from 'react'

const fieldClassName =
  'w-full rounded border border-hairline bg-surface text-ink placeholder:text-ink-muted transition-colors focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/30 focus:ring-offset-0'

export type InputProps = React.ComponentPropsWithoutRef<'input'>

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { className, ...props },
  ref,
) {
  return (
    <input
      ref={ref}
      className={clsx(fieldClassName, 'h-10 px-3', className)}
      {...props}
    />
  )
})

export type TextareaProps = React.ComponentPropsWithoutRef<'textarea'>

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  function Textarea({ className, ...props }, ref) {
    return (
      <textarea
        ref={ref}
        className={clsx(fieldClassName, 'min-h-[88px] resize-y px-3 py-2.5', className)}
        {...props}
      />
    )
  },
)

export type SelectProps = React.ComponentPropsWithoutRef<'select'>

export const Select = forwardRef<HTMLSelectElement, SelectProps>(function Select(
  { className, ...props },
  ref,
) {
  return (
    <select
      ref={ref}
      className={clsx(fieldClassName, 'h-10 px-3', className)}
      {...props}
    />
  )
})
