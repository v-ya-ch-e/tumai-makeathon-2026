import clsx from 'clsx'
import type { Listing } from '../types'
import { StatusPill, type StatusPillTone } from './ui'

export type ListingListProps = {
  listings: Listing[]
  onOpen: (listing: Listing) => void
  emptyLabel?: string
}

function scoreTone(score: number | null): StatusPillTone {
  if (score === null) return 'idle'
  if (score >= 0.7) return 'good'
  if (score >= 0.4) return 'warn'
  return 'bad'
}

function scoreLabel(score: number | null): string {
  if (score === null) return 'unscored'
  return score.toFixed(2)
}

function metaLine(l: Listing): string {
  const parts: string[] = []
  if (l.priceEur !== null) parts.push(`${l.priceEur} €`)
  if (l.sizeM2 !== null) parts.push(`${l.sizeM2} m²`)
  if (l.wgSize !== null) parts.push(`${l.wgSize}er WG`)
  if (l.district) parts.push(l.district)
  return parts.join(' · ')
}

export function ListingList({ listings, onOpen, emptyLabel }: ListingListProps) {
  if (listings.length === 0) {
    return (
      <p className="text-[13px] text-ink-muted">
        {emptyLabel ?? 'Matching listings will appear here once the agent has scored its first pass.'}
      </p>
    )
  }

  const sorted = [...listings].sort((a, b) => (b.score ?? -1) - (a.score ?? -1))

  return (
    <ul className="space-y-3">
      {sorted.map((l) => (
        <li key={l.id}>
          <button
            type="button"
            onClick={() => onOpen(l)}
            className={clsx(
              'flex w-full items-start gap-4 rounded-card border border-hairline bg-surface p-4 text-left transition-colors duration-150 ease-out',
              'hover:bg-surface-raised',
            )}
          >
            <div className="min-w-0 flex-1 space-y-1">
              <h3 className="truncate text-[15px] font-semibold text-ink">
                {l.title ?? `Listing ${l.id}`}
              </h3>
              <p className="truncate text-[13px] text-ink-muted">{metaLine(l) || '—'}</p>
              {l.scoreReason ? (
                <p className="line-clamp-2 text-[13px] text-ink-muted">{l.scoreReason}</p>
              ) : null}
            </div>
            <StatusPill tone={scoreTone(l.score)}>{scoreLabel(l.score)}</StatusPill>
          </button>
        </li>
      ))}
    </ul>
  )
}
