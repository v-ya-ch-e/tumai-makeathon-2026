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

function priceLabel(l: Listing): string {
  return l.priceEur !== null ? `${l.priceEur} €` : 'Price pending'
}

function sizeLabel(l: Listing): string {
  if (l.sizeM2 !== null) return `${l.sizeM2} m²`
  if (l.wgSize !== null) return `${l.wgSize}er WG`
  return 'Size pending'
}

function distanceLabel(l: Listing): string {
  if (l.bestCommuteMinutes !== null) return `${l.bestCommuteMinutes} min`
  return 'No route yet'
}

function subline(l: Listing): string {
  const parts: string[] = []
  if (l.district) parts.push(l.district)
  if (l.availableFrom) parts.push(`From ${l.availableFrom}`)
  return parts.join(' · ')
}

function coverFallback(index: number): string {
  return `linear-gradient(135deg, rgba(255,56,92,0.16), rgba(255,181,167,0.4) ${45 + index * 7}%, rgba(255,255,255,0.96))`
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
    <ul className="grid gap-5 md:grid-cols-2 xl:grid-cols-2">
      {sorted.map((l, index) => (
        <li key={l.id}>
          <button
            type="button"
            onClick={() => onOpen(l)}
            className={clsx(
              'group w-full overflow-hidden rounded-[28px] border border-hairline/70 bg-surface text-left transition-all duration-200 ease-out',
              'hover:-translate-y-0.5 hover:border-accent/35 hover:shadow-[0_22px_48px_rgba(15,23,42,0.09)]',
            )}
          >
            <div className="relative aspect-[4/3] w-full overflow-hidden bg-[#fff1ee]">
              {l.coverPhotoUrl ? (
                <img
                  src={l.coverPhotoUrl}
                  alt={l.title ?? `Listing ${l.id}`}
                  className="h-full w-full object-cover transition-transform duration-300 ease-out group-hover:scale-[1.03]"
                  loading="lazy"
                />
              ) : (
                <div
                  className="flex h-full w-full items-end justify-between px-5 py-4"
                  style={{ backgroundImage: coverFallback(index) }}
                >
                  <span className="rounded-full bg-white/82 px-3 py-1 text-[11px] font-medium uppercase tracking-[0.14em] text-accent shadow-sm">
                    No photo yet
                  </span>
                  <span className="text-[34px]">🏠</span>
                </div>
              )}
              <StatusPill
                tone={scoreTone(l.score)}
                className="absolute right-4 top-4 border-white/80 bg-white/92 shadow-[0_10px_24px_rgba(15,23,42,0.08)]"
              >
                {scoreLabel(l.score)}
              </StatusPill>
            </div>
            <div className="space-y-3 p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <h3 className="truncate text-[18px] font-semibold tracking-[-0.02em] text-ink">
                    {l.title ?? `Listing ${l.id}`}
                  </h3>
                  <p className="mt-1 truncate text-[13px] text-ink-muted">
                    {subline(l) || 'Location and availability still loading'}
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-2 rounded-[20px] bg-surface-raised p-3">
                <FactPill label="Price" value={priceLabel(l)} />
                <FactPill label="Size" value={sizeLabel(l)} />
                <FactPill label="Distance" value={distanceLabel(l)} />
              </div>

              <div className="flex flex-wrap gap-2">
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
                {!l.vetoReason && l.matchReasons.length > 0 ? (
                  <span className="rounded-full border border-good/20 bg-good/10 px-3 py-1 text-[11px] uppercase tracking-[0.12em] text-good">
                    Strong match
                  </span>
                ) : null}
              </div>

              {l.vetoReason ? (
                <p className="line-clamp-2 text-[13px] text-ink-muted">
                  {l.vetoReason}
                </p>
              ) : l.mismatchReasons.length > 0 ? (
                <p className="line-clamp-1 text-[13px] text-ink-muted">
                  Watchout: {l.mismatchReasons[0]}
                </p>
              ) : l.matchReasons.length > 0 ? (
                <p className="line-clamp-1 text-[13px] text-ink-muted">
                  Highlight: {l.matchReasons[0]}
                </p>
              ) : null}

              <div className="flex items-center justify-between border-t border-hairline/60 pt-3 text-[13px] text-ink-muted">
                <span>{l.coverPhotoUrl ? 'Photo available' : 'Text-only listing'}</span>
                <span aria-hidden className="text-[18px] transition-transform duration-150 group-hover:translate-x-0.5">
                  →
                </span>
              </div>
            </div>
          </button>
        </li>
      ))}
    </ul>
  )
}

function FactPill({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[16px] border border-hairline/70 bg-white px-3 py-2">
      <p className="text-[10px] uppercase tracking-[0.14em] text-ink-muted">{label}</p>
      <p className="mt-1 text-[14px] font-semibold text-ink">{value}</p>
    </div>
  )
}
