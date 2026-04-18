import { formatGermanDate } from '../lib/date'
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

export function ListingList({ listings, onOpen, emptyLabel }: ListingListProps) {
  if (listings.length === 0) {
    return (
      <p className="text-[13px] text-ink-muted">
        {emptyLabel ?? 'Matches will appear here as soon as the first results are ready.'}
      </p>
    )
  }

  const sorted = [...listings].sort((a, b) => (b.score ?? -1) - (a.score ?? -1))
  return (
    <ul className="divide-y divide-hairline">
      {sorted.map((listing) => {
        return (
          <li key={listing.id}>
            <button
              type="button"
              onClick={() => onOpen(listing)}
              className="group grid w-full gap-4 px-5 py-5 text-left transition-colors duration-150 ease-out hover:bg-surface-raised sm:grid-cols-[176px_minmax(0,1fr)]"
            >
              <div className="overflow-hidden rounded border border-hairline bg-surface-raised">
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
          </li>
        )
      })}
    </ul>
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
