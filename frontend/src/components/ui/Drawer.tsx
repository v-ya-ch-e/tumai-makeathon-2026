import clsx from 'clsx'
import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'

export type DrawerProps = {
  open: boolean
  onClose: () => void
  title?: ReactNode
  children: ReactNode
  widthClass?: string
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  widthClass = 'w-full sm:w-[480px]',
}: DrawerProps) {
  const [render, setRender] = useState(open)
  const [entered, setEntered] = useState(false)

  useEffect(() => {
    if (open) {
      setRender(true)
      const id = requestAnimationFrame(() => {
        requestAnimationFrame(() => setEntered(true))
      })
      return () => cancelAnimationFrame(id)
    }
    setEntered(false)
    const t = window.setTimeout(() => setRender(false), 220)
    return () => window.clearTimeout(t)
  }, [open])

  useEffect(() => {
    if (!render) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [render, onClose])

  if (!render) return null

  return createPortal(
    <div className="pointer-events-none fixed inset-0 z-40">
      <div
        aria-modal
        role="dialog"
        className={clsx(
          'pointer-events-auto fixed top-0 right-0 z-50 flex h-full max-w-full flex-col rounded-l-drawer border-l border-hairline bg-surface shadow-drawer transition-transform duration-[220ms] ease-in-out',
          widthClass,
          entered ? 'translate-x-0' : 'translate-x-full',
        )}
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-hairline px-4 py-4 sm:gap-4 sm:px-6 sm:py-5">
          {title != null ? (
            <div className="min-w-0 flex-1 text-[15px] font-semibold text-ink">{title}</div>
          ) : (
            <div className="flex-1" />
          )}
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded text-ink-muted transition-colors duration-150 ease-out hover:bg-surface-raised hover:text-ink"
            aria-label="Close"
          >
            <span className="text-lg leading-none">✕</span>
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
