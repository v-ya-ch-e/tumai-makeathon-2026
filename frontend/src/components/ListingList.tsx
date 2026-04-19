import { formatGermanDate } from '../lib/date'
import type { Listing } from '../types'
import { StatusPill, type StatusPillTone } from './ui'

export type ListingListProps = {
  listings: Listing[]
  onOpen: (listing: Listing) => void
  emptyLabel?: string
  /** Optional predicate used to highlight freshly-scraped listings with a
   * "NEW" badge. Defaults to flagging listings whose `firstSeenAt` is
   * within the last 24 hours. */
  isNew?: (listing: Listing) => boolean
  /** Predicate + handler for the per-row hide toggle. When both are
   * provided, a small eye button appears in the top-right of each card. */
  isHidden?: (listing: Listing) => boolean
  onToggleHide?: (listing: Listing) => void
}

const NEW_BADGE_TTL_MS = 24 * 60 * 60 * 1000

function defaultIsNew(listing: Listing): boolean {
  if (!listing.firstSeenAt) return false
  const seenMs = Date.parse(listing.firstSeenAt)
  if (Number.isNaN(seenMs)) return false
  return Date.now() - seenMs < NEW_BADGE_TTL_MS
}

function scoreTone(score: number | null): StatusPillTone {
  if (score === null) return 'idle'
  if (score >= 0.7) return 'good'
  if (score >= 0.4) return 'warn'
  return 'bad'
}

function scoreLabel(score: number | null): string {
  if (score === null) return 'Pending'
  return `${Math.round(score * 100)}%`
}

function priceLabel(listing: Listing): string {
  return listing.priceEur !== null ? `${listing.priceEur} EUR` : 'Price pending'
}

function sizeLabel(listing: Listing): string {
  if (listing.sizeM2 !== null) return `${listing.sizeM2} m²`
  if (listing.wgSize !== null) return `${listing.wgSize} people`
  return 'Size pending'
}

function distanceLabel(listing: Listing): string {
  if (listing.bestCommuteMinutes !== null) {
    const target = listing.bestCommuteLabel ? `to ${listing.bestCommuteLabel}` : 'to your best anchor'
    return `${listing.bestCommuteMinutes} min ${target}`
  }
  return 'Route pending'
}

function availabilityLabel(listing: Listing): string | null {
  if (listing.availableFrom && listing.availableTo) {
    return `${formatGermanDate(listing.availableFrom)} - ${formatGermanDate(listing.availableTo)}`
  }
  if (listing.availableFrom) return `From ${formatGermanDate(listing.availableFrom)}`
  if (listing.availableTo) return `Until ${formatGermanDate(listing.availableTo)}`
  return null
}

function subline(listing: Listing): string {
  const parts: string[] = []
  if (listing.district) parts.push(listing.district)
  const availability = availabilityLabel(listing)
  if (availability) parts.push(availability)
  return parts.join(' · ')
}

export function ListingList({
  listings,
  onOpen,
  emptyLabel,
  isNew,
  isHidden,
  onToggleHide,
}: ListingListProps) {
  if (listings.length === 0) {
    return (
      <p className="px-6 py-8 text-[13px] text-ink-muted sm:px-8 lg:px-10">
        {emptyLabel ?? 'Matches will appear here as soon as the first results are ready.'}
      </p>
    )
  }

  const flagAsNew = isNew ?? defaultIsNew
  const canHide = typeof onToggleHide === 'function'
  return (
    <ul className="divide-y divide-hairline">
      {listings.map((listing) => {
        const fresh = flagAsNew(listing)
        const hidden = isHidden ? isHidden(listing) : false
        return (
          <li key={listing.id} className="relative">
            <button
              type="button"
              onClick={() => onOpen(listing)}
              className={`group grid w-full gap-4 px-5 py-5 text-left transition-colors duration-150 ease-out hover:bg-surface-raised sm:grid-cols-[176px_minmax(0,1fr)] ${hidden ? 'opacity-60' : ''}`}
            >
              <div className="relative overflow-hidden rounded border border-hairline bg-surface-raised">
                {fresh ? (
                  <span
                    className="absolute left-2 top-2 z-10 inline-flex items-center gap-1.5 rounded-full bg-accent px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.18em] text-white ring-2 ring-white/95 shadow-[0_2px_6px_rgba(0,0,0,0.35)]"
                    aria-label="New listing"
                  >
                    <span aria-hidden className="relative inline-flex h-1.5 w-1.5">
                      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white opacity-75" />
                      <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-white" />
                    </span>
                    New
                  </span>
                ) : null}
                {listing.coverPhotoUrl ? (
                  <img
                    src={listing.coverPhotoUrl}
                    alt={listing.title ?? `Listing ${listing.id}`}
                    className="aspect-[4/3] h-full w-full object-cover"
                    loading="lazy"
                  />
                ) : (
                  <div className="flex aspect-[4/3] items-end justify-between p-4">
                    <span className="data-label">No photo</span>
                    <span className="text-[13px] text-ink-muted">{listing.district ?? 'WG-Gesucht'}</span>
                  </div>
                )}
              </div>

              <div className="min-w-0">
                <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start">
                  <div className="min-w-0">
                    <h3 className="truncate text-[20px] font-semibold text-ink">
                      {listing.title ?? `Listing ${listing.id}`}
                    </h3>
                    <p className="mt-1 text-[13px] text-ink-muted">
                      {subline(listing) || 'Location and availability still loading'}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {listing.kind ? (
                      <StatusPill tone="idle">{listing.kind === 'flat' ? 'Whole flat' : 'WG room'}</StatusPill>
                    ) : null}
                    <div className="flex items-start justify-start sm:justify-end">
                      <StatusPill tone={scoreTone(listing.score)}>{scoreLabel(listing.score)}</StatusPill>
                    </div>
                  </div>
                </div>

                <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
                  <Fact label="Price" value={priceLabel(listing)} />
                  <Fact label="Size" value={sizeLabel(listing)} />
                  <Fact label="Commute" value={distanceLabel(listing)} />
                </dl>

                <div className="mt-4 flex items-center justify-between border-t border-hairline pt-3 text-[13px] text-ink-muted">
                  <span>See details and original listing</span>
                  <span aria-hidden className="text-ink transition-transform duration-150 group-hover:translate-x-0.5">
                    Open →
                  </span>
                </div>
              </div>
            </button>
            {canHide ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation()
                  onToggleHide?.(listing)
                }}
                aria-pressed={hidden}
                aria-label={hidden ? 'Unhide listing' : 'Hide listing'}
                title={hidden ? 'Unhide listing' : 'Hide listing'}
                className="absolute right-7 top-7 z-20 inline-flex h-8 w-8 items-center justify-center rounded-full border border-hairline bg-surface/90 text-ink-muted shadow-sm backdrop-blur transition-colors hover:border-ink hover:text-ink focus:border-ink focus:outline-none sm:right-auto sm:left-[156px]"
              >
                {hidden ? <EyeOffIcon /> : <EyeIcon />}
              </button>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

function EyeIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}

function EyeOffIcon() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 3l18 18" />
      <path d="M10.58 10.58A3 3 0 0 0 12 15a3 3 0 0 0 2.83-2.01" />
      <path d="M9.88 5.09A10.94 10.94 0 0 1 12 5c6.5 0 10 7 10 7a17.5 17.5 0 0 1-3.22 4.19" />
      <path d="M6.1 6.1A17.5 17.5 0 0 0 2 12s3.5 7 10 7a10.94 10.94 0 0 0 5.17-1.32" />
    </svg>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="data-label">{label}</dt>
      <dd className="mt-1 text-[14px] text-ink">{value}</dd>
    </div>
  )
}
