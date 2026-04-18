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
    <ul className="space-y-4">
      {sorted.map((l, index) => (
        <li key={l.id}>
          <button
            type="button"
            onClick={() => onOpen(l)}
            className={clsx(
              'flex w-full items-start gap-4 rounded-[24px] border border-hairline/80 bg-surface-raised/90 p-5 text-left transition-all duration-150 ease-out',
              'hover:-translate-y-px hover:border-accent/35 hover:bg-surface hover:shadow-[0_18px_38px_rgba(39,33,29,0.06)]',
            )}
          >
            <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-[18px] border border-hairline/70 bg-[#f4e7d8] font-mono text-[12px] uppercase tracking-[0.16em] text-accent">
              {String(index + 1).padStart(2, '0')}
            </div>
            <div className="min-w-0 flex-1 space-y-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="truncate text-[17px] font-semibold tracking-[-0.02em] text-ink">
                    {l.title ?? `Listing ${l.id}`}
                  </h3>
                  <p className="mt-1 truncate text-[13px] text-ink-muted">
                    {metaLine(l) || 'Pricing and size still loading'}
                  </p>
                </div>
                <StatusPill tone={scoreTone(l.score)}>{scoreLabel(l.score)}</StatusPill>
              </div>

              {l.scoreReason ? (
                <p className="line-clamp-2 text-[14px] leading-6 text-ink">{l.scoreReason}</p>
              ) : null}

              <div className="flex flex-wrap gap-2">
                {l.matchReasons.slice(0, 2).map((reason) => (
                  <span
                    key={reason}
                    className="rounded-full border border-good/20 bg-good/10 px-3 py-1 text-[11px] uppercase tracking-[0.12em] text-good"
                  >
                    {reason}
                  </span>
                ))}
                {l.vetoReason ? (
                  <span className="rounded-full border border-bad/20 bg-bad/10 px-3 py-1 text-[11px] uppercase tracking-[0.12em] text-bad">
                    Rejected
                  </span>
                ) : null}
                {!l.vetoReason && l.matchReasons.length === 0 && l.mismatchReasons.length > 0 ? (
                  <span className="rounded-full border border-warn/20 bg-warn/10 px-3 py-1 text-[11px] uppercase tracking-[0.12em] text-warn">
                    Needs review
                  </span>
                ) : null}
              </div>

              {l.mismatchReasons.length > 0 ? (
                <p className="line-clamp-1 text-[13px] text-ink-muted">
                  Watchouts: {l.mismatchReasons.slice(0, 2).join(' · ')}
                </p>
              ) : null}
            </div>
            <div className="hidden self-center text-[18px] text-ink-muted sm:block" aria-hidden>
              →
            </div>
          </button>
        </li>
      ))}
    </ul>
  )
}
