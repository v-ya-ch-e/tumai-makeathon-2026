import clsx from 'clsx'
import type { Action } from '../types'

export type ActionLogProps = {
  actions: Action[]
  emptyLabel?: string
}

function kindToneClass(kind: string): string {
  if (kind === 'new_listing' || kind === 'evaluate' || kind === 'done') return 'border-good/30 bg-good/10 text-good'
  if (kind === 'error') return 'border-bad/30 bg-bad/10 text-bad'
  if (kind === 'rescan' || kind === 'rate_limit' || kind === 'dry_run_skip') {
    return 'border-warn/30 bg-warn/10 text-warn'
  }
  return 'border-hairline bg-surface-raised text-ink-muted'
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  } catch {
    return iso
  }
}

export function ActionLog({ actions, emptyLabel }: ActionLogProps) {
  if (actions.length === 0) {
    return (
      <p className="text-[13px] text-ink-muted">
        {emptyLabel ?? 'Agent actions will stream here as the hunt runs.'}
      </p>
    )
  }

  const reversed = [...actions].reverse()

  return (
    <ol className="space-y-4">
      {reversed.map((a, i) => (
        <li
          key={`${a.at}-${i}`}
          className="rounded-[22px] border border-hairline/80 bg-surface-raised/80 px-4 py-4 shadow-[0_14px_30px_rgba(39,33,29,0.04)]"
        >
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-mono text-[12px] text-ink-muted">{formatTime(a.at)}</span>
            <span
              className={clsx(
                'inline-flex rounded-full border px-2.5 py-1 font-mono text-[11px] uppercase tracking-[0.14em]',
                kindToneClass(a.kind),
              )}
            >
              {a.kind}
            </span>
          </div>
          <p className="mt-3 text-[14px] leading-6 text-ink">{a.summary}</p>
          {a.detail ? <p className="mt-2 text-[13px] leading-6 text-ink-muted">{a.detail}</p> : null}
        </li>
      ))}
    </ol>
  )
}
