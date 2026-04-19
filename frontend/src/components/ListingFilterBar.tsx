import clsx from 'clsx'
import { useId } from 'react'
import type { Listing } from '../types'

export type ListingSort =
  | 'score'
  | 'newest'
  | 'price_asc'
  | 'price_desc'
  | 'commute'

export type ListingKindFilter = 'all' | 'wg' | 'flat'

export type ListingFilters = {
  sort: ListingSort
  kind: ListingKindFilter
  onlyNew: boolean
  showHidden: boolean
}

export const DEFAULT_LISTING_FILTERS: ListingFilters = {
  sort: 'score',
  kind: 'all',
  onlyNew: false,
  showHidden: false,
}

const SORT_OPTIONS: Array<{ value: ListingSort; label: string }> = [
  { value: 'score', label: 'Best match' },
  { value: 'newest', label: 'Newest first' },
  { value: 'price_asc', label: 'Price: low to high' },
  { value: 'price_desc', label: 'Price: high to low' },
  { value: 'commute', label: 'Shortest commute' },
]

const KIND_OPTIONS: Array<{ value: ListingKindFilter; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'wg', label: 'WG room' },
  { value: 'flat', label: 'Whole flat' },
]

export type ListingFilterBarProps = {
  value: ListingFilters
  onChange: (next: ListingFilters) => void
  newCount: number
  totalCount: number
  visibleCount: number
  hiddenCount: number
}

export function ListingFilterBar({
  value,
  onChange,
  newCount,
  totalCount,
  visibleCount,
  hiddenCount,
}: ListingFilterBarProps) {
  const sortId = useId()
  const filtered = visibleCount !== totalCount
  return (
    <div className="flex flex-col gap-3 border-b border-hairline px-6 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-8 lg:px-10">
      <div className="flex flex-wrap items-center gap-2">
        <label
          htmlFor={sortId}
          className="text-[11px] uppercase tracking-[0.14em] text-ink-muted"
        >
          Sort
        </label>
        <select
          id={sortId}
          value={value.sort}
          onChange={(event) =>
            onChange({ ...value, sort: event.target.value as ListingSort })
          }
          className="min-h-9 rounded border border-hairline bg-surface px-3 py-1.5 text-[13px] text-ink transition-colors hover:border-ink focus:border-ink focus:outline-none"
        >
          {SORT_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>

        <span aria-hidden className="hidden h-5 w-px bg-hairline sm:inline-block" />

        <div
          role="radiogroup"
          aria-label="Listing type"
          className="inline-flex rounded-full bg-surface-raised p-1"
        >
          {KIND_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              role="radio"
              aria-checked={value.kind === option.value}
              onClick={() => onChange({ ...value, kind: option.value })}
              className={clsx(
                'rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors',
                value.kind === option.value
                  ? 'bg-surface text-ink shadow-sm'
                  : 'text-ink-muted hover:text-ink',
              )}
            >
              {option.label}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={() => onChange({ ...value, onlyNew: !value.onlyNew })}
          aria-pressed={value.onlyNew}
          disabled={newCount === 0 && !value.onlyNew}
          className={clsx(
            'inline-flex min-h-9 items-center gap-2 rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors',
            value.onlyNew
              ? 'border-accent bg-accent-muted text-ink'
              : 'border-hairline bg-surface text-ink-muted hover:border-ink hover:text-ink',
            newCount === 0 && !value.onlyNew ? 'cursor-not-allowed opacity-50' : '',
          )}
        >
          <span
            aria-hidden
            className={clsx(
              'inline-block h-1.5 w-1.5 rounded-full',
              newCount > 0 ? 'bg-accent' : 'bg-ink-muted',
            )}
          />
          Only new
          <span className="text-ink-muted">({newCount})</span>
        </button>

        <button
          type="button"
          onClick={() => onChange({ ...value, showHidden: !value.showHidden })}
          aria-pressed={value.showHidden}
          disabled={hiddenCount === 0 && !value.showHidden}
          className={clsx(
            'inline-flex min-h-9 items-center gap-2 rounded-full border px-3 py-1.5 text-[12px] font-medium transition-colors',
            value.showHidden
              ? 'border-ink bg-surface-raised text-ink'
              : 'border-hairline bg-surface text-ink-muted hover:border-ink hover:text-ink',
            hiddenCount === 0 && !value.showHidden ? 'cursor-not-allowed opacity-50' : '',
          )}
        >
          Show hidden
          <span className="text-ink-muted">({hiddenCount})</span>
        </button>
      </div>

      <div className="text-[12px] text-ink-muted">
        {filtered
          ? `${visibleCount} of ${totalCount} shown`
          : `${totalCount} listing${totalCount === 1 ? '' : 's'}`}
      </div>
    </div>
  )
}

const NEW_WINDOW_MS = 24 * 60 * 60 * 1000

/** A listing is "new" when it was first discovered by the scraper *after* the
 * user created their account (gate that already governs notifications on the
 * backend) AND it is still inside the 24-hour display window. */
export function isListingNew(
  listing: Listing,
  userCreatedAt: string | null,
): boolean {
  if (!listing.firstSeenAt) return false
  const firstSeenMs = Date.parse(listing.firstSeenAt)
  if (Number.isNaN(firstSeenMs)) return false
  if (Date.now() - firstSeenMs > NEW_WINDOW_MS) return false
  if (userCreatedAt) {
    const userCreatedMs = Date.parse(userCreatedAt)
    if (!Number.isNaN(userCreatedMs) && firstSeenMs <= userCreatedMs) return false
  }
  return true
}

export function applyListingFilters(
  listings: Listing[],
  filters: ListingFilters,
  userCreatedAt: string | null,
  isHidden: (listing: Listing) => boolean = () => false,
): Listing[] {
  const filtered = listings.filter((listing) => {
    if (filters.kind !== 'all' && listing.kind && listing.kind !== filters.kind) {
      return false
    }
    if (filters.onlyNew && !isListingNew(listing, userCreatedAt)) {
      return false
    }
    if (!filters.showHidden && isHidden(listing)) {
      return false
    }
    return true
  })

  const byScoreDesc = (a: Listing, b: Listing) =>
    (b.score ?? -Infinity) - (a.score ?? -Infinity)

  switch (filters.sort) {
    case 'newest': {
      return [...filtered].sort((a, b) => {
        const aNew = isListingNew(a, userCreatedAt)
        const bNew = isListingNew(b, userCreatedAt)
        if (aNew !== bNew) return aNew ? -1 : 1
        return byScoreDesc(a, b)
      })
    }
    case 'price_asc':
      return [...filtered].sort(
        (a, b) => (a.priceEur ?? Infinity) - (b.priceEur ?? Infinity),
      )
    case 'price_desc':
      return [...filtered].sort(
        (a, b) => (b.priceEur ?? -Infinity) - (a.priceEur ?? -Infinity),
      )
    case 'commute':
      return [...filtered].sort(
        (a, b) =>
          (a.bestCommuteMinutes ?? Infinity) - (b.bestCommuteMinutes ?? Infinity),
      )
    case 'score':
    default:
      return [...filtered].sort(byScoreDesc)
  }
}
