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
  if (score === null) return 'Pending'
  return score.toFixed(2)
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
  if (listing.bestCommuteMinutes !== null) return `${listing.bestCommuteMinutes} min commute`
  return 'Route pending'
}

function subline(listing: Listing): string {
  const parts: string[] = []
  if (listing.district) parts.push(listing.district)
  if (listing.availableFrom) parts.push(`From ${listing.availableFrom}`)
  return parts.join(' · ')
}

function listingState(listing: Listing): { tone: StatusPillTone; label: string } | null {
  if (listing.vetoReason) return { tone: 'bad', label: 'Rejected' }
  if (listing.matchReasons.length > 0) return { tone: 'good', label: 'Strong match' }
  if (listing.mismatchReasons.length > 0) return { tone: 'warn', label: 'Needs review' }
  return null
}

function listingNote(listing: Listing): string | null {
  if (listing.vetoReason) return listing.vetoReason
  if (listing.mismatchReasons.length > 0) return listing.mismatchReasons[0]
  if (listing.matchReasons.length > 0) return listing.matchReasons[0]
  return null
}

export function ListingList({ listings, onOpen, emptyLabel }: ListingListProps) {
  if (listings.length === 0) {
    return (
      <p className="text-[13px] text-ink-muted">
        {emptyLabel ?? 'Matching listings will appear here once the agent has finished its first scoring pass.'}
      </p>
    )
  }

  const sorted = [...listings].sort((a, b) => (b.score ?? -1) - (a.score ?? -1))

  return (
    <ul className="divide-y divide-hairline">
      {sorted.map((listing) => {
        const state = listingState(listing)
        const note = listingNote(listing)
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
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-[20px] font-semibold text-ink">
                      {listing.title ?? `Listing ${listing.id}`}
                    </h3>
                    <p className="mt-1 text-[13px] text-ink-muted">
                      {subline(listing) || 'Location and availability still loading'}
                    </p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <StatusPill tone={scoreTone(listing.score)}>{scoreLabel(listing.score)}</StatusPill>
                    {state ? <StatusPill tone={state.tone}>{state.label}</StatusPill> : null}
                  </div>
                </div>

                <dl className="mt-4 flex flex-wrap gap-x-6 gap-y-2">
                  <Fact label="Price" value={priceLabel(listing)} />
                  <Fact label="Size" value={sizeLabel(listing)} />
                  <Fact label="Commute" value={distanceLabel(listing)} />
                </dl>

                {note ? (
                  <p className={clsx('mt-4 text-[14px] leading-6', listing.vetoReason ? 'text-bad' : 'text-ink-muted')}>
                    {note}
                  </p>
                ) : null}

                <div className="mt-4 flex items-center justify-between border-t border-hairline pt-3 text-[13px] text-ink-muted">
                  <span>{listing.coverPhotoUrl ? 'Photo available' : 'Text-only listing'}</span>
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
