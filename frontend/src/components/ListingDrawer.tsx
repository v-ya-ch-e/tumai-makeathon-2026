import { useEffect, useState } from 'react'
import { Button, Drawer, StatusPill, type StatusPillTone } from './ui'
import { getListingDetail } from '../lib/api'
import type { Listing, ListingDetail } from '../types'

export type ListingDrawerProps = {
  open: boolean
  listing: Listing | null
  onClose: () => void
}

function scoreTone(score: number | null): StatusPillTone {
  if (score === null) return 'idle'
  if (score >= 0.7) return 'good'
  if (score >= 0.4) return 'warn'
  return 'bad'
}

function formatScore(score: number | null): string {
  return score === null ? 'unscored' : score.toFixed(2)
}

export function ListingDrawer({ open, listing, onClose }: ListingDrawerProps) {
  const [detail, setDetail] = useState<ListingDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !listing) return
    setDetail(null)
    setError(null)
    setLoading(true)
    let cancelled = false
    void (async () => {
      try {
        const d = await getListingDetail(listing.id, listing.huntId)
        if (!cancelled) setDetail(d)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, listing?.id, listing?.huntId])

  const active: Listing | null = detail?.listing ?? listing

  return (
    <Drawer
      open={open}
      onClose={onClose}
      widthClass="w-[560px]"
      title={
        active ? (
          <div className="flex items-center gap-3">
            <span className="truncate">{active.title ?? `Listing ${active.id}`}</span>
            <StatusPill tone={scoreTone(active.score)}>{formatScore(active.score)}</StatusPill>
          </div>
        ) : (
          'Listing'
        )
      }
    >
      {active ? (
        <div className="space-y-8">
          {detail && detail.photos.length > 0 ? (
            <img
              src={detail.photos[0]}
              alt={active.title ?? active.id}
              className="w-full rounded-card border border-hairline object-cover"
              style={{ maxHeight: 360 }}
            />
          ) : null}

          <dl className="grid grid-cols-2 gap-y-3 text-[14px]">
            <dt className="text-ink-muted">Price</dt>
            <dd className="text-ink">{active.priceEur !== null ? `${active.priceEur} €` : '—'}</dd>
            <dt className="text-ink-muted">Size</dt>
            <dd className="text-ink">{active.sizeM2 !== null ? `${active.sizeM2} m²` : '—'}</dd>
            <dt className="text-ink-muted">WG size</dt>
            <dd className="text-ink">{active.wgSize !== null ? `${active.wgSize} people` : '—'}</dd>
            <dt className="text-ink-muted">District</dt>
            <dd className="text-ink">{active.district ?? '—'}</dd>
            <dt className="text-ink-muted">Available</dt>
            <dd className="text-ink">
              {active.availableFrom ?? '—'}
              {active.availableTo ? ` → ${active.availableTo}` : ''}
            </dd>
          </dl>

          {active.scoreReason ? (
            <section className="space-y-2">
              <h3 className="text-[15px] font-semibold text-ink">Why the agent flagged it</h3>
              <p className="text-[14px] text-ink">{active.scoreReason}</p>
              {active.matchReasons.length > 0 ? (
                <ul className="list-inside list-disc text-[13px] text-good">
                  {active.matchReasons.map((r, i) => (
                    <li key={`m-${i}`}>{r}</li>
                  ))}
                </ul>
              ) : null}
              {active.mismatchReasons.length > 0 ? (
                <ul className="list-inside list-disc text-[13px] text-bad">
                  {active.mismatchReasons.map((r, i) => (
                    <li key={`x-${i}`}>{r}</li>
                  ))}
                </ul>
              ) : null}
            </section>
          ) : null}

          {detail?.travelMinutesPerLocation &&
          Object.keys(detail.travelMinutesPerLocation).length > 0 ? (
            <section className="space-y-2">
              <h3 className="text-[15px] font-semibold text-ink">Commute</h3>
              <ul className="text-[13px] text-ink">
                {Object.entries(detail.travelMinutesPerLocation).map(([label, minutes]) => (
                  <li key={label}>
                    <span className="text-ink-muted">{label}</span>
                    <span> — {minutes} min</span>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {active.description ? (
            <section className="space-y-2">
              <h3 className="text-[15px] font-semibold text-ink">Description</h3>
              <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-ink">
                {active.description}
              </p>
            </section>
          ) : null}

          {detail && detail.photos.length > 1 ? (
            <section className="space-y-2">
              <h3 className="text-[15px] font-semibold text-ink">More photos</h3>
              <div className="grid grid-cols-2 gap-2">
                {detail.photos.slice(1).map((url, i) => (
                  <img
                    key={i}
                    src={url}
                    alt=""
                    className="h-32 w-full rounded border border-hairline object-cover"
                  />
                ))}
              </div>
            </section>
          ) : null}

          <div className="flex items-center gap-3 border-t border-hairline pt-4">
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                window.open(active.url, '_blank', 'noopener,noreferrer')
              }}
            >
              Open on wg-gesucht
            </Button>
            <Button variant="secondary" size="sm" disabled>
              Send message (needs login)
            </Button>
          </div>

          {loading ? <p className="text-[13px] text-ink-muted">Loading…</p> : null}
          {error ? <p className="text-[13px] text-bad">{error}</p> : null}
        </div>
      ) : (
        <p className="text-[13px] text-ink-muted">No listing selected.</p>
      )}
    </Drawer>
  )
}
