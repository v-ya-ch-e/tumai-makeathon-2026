import clsx from 'clsx'
import type { Action } from '../types'

export type ActionLogProps = {
  actions: Action[]
  emptyLabel?: string
}

function kindToneClass(kind: string): string {
  if (kind === 'new_listing' || kind === 'evaluate' || kind === 'done') return 'text-good'
  if (kind === 'error') return 'text-bad'
  if (kind === 'rescan' || kind === 'rate_limit' || kind === 'dry_run_skip') return 'text-warn'
  return 'text-ink-muted'
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
    <ol className="space-y-3">
      {reversed.map((a, i) => (
        <li key={`${a.at}-${i}`} className="flex items-start gap-3 border-l-2 border-hairline pl-3">
          <span className="w-20 shrink-0 font-mono text-[12px] text-ink-muted">
            {formatTime(a.at)}
          </span>
          <span className={clsx('w-24 shrink-0 font-mono text-[12px]', kindToneClass(a.kind))}>
            {a.kind}
          </span>
          <span className="text-[14px] text-ink">{a.summary}</span>
        </li>
      ))}
    </ol>
  )
}
