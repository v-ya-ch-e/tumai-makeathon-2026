import clsx from 'clsx'
import { useEffect, useState, type ReactNode } from 'react'
import { getListingDetail } from '../lib/api'
import { useSession } from '../lib/session'
import type { Component, Listing, ListingDetail } from '../types'
import { Button, Drawer, StatusPill, type StatusPillTone } from './ui'

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
  return score === null ? 'Pending' : score.toFixed(2)
}

const COMPONENT_LABELS: Record<string, string> = {
  price: 'Price',
  size: 'Size',
  wg_size: 'WG size',
  availability: 'Availability',
  commute: 'Commute',
  preferences: 'Preferences',
  vibe: 'Vibe',
}

function componentLabel(key: string): string {
  return COMPONENT_LABELS[key] ?? key.replace(/_/g, ' ').replace(/\b\w/g, (char) => char.toUpperCase())
}

function barClassName(score: number, missing: boolean): string {
  if (missing) return 'bg-hairline'
  if (score >= 0.7) return 'bg-good'
  if (score >= 0.4) return 'bg-warn'
  return 'bg-bad'
}

function ComponentBar({ component }: { component: Component }) {
  const widthPct = component.missingData ? 100 : Math.max(2, Math.round(component.score * 100))
  const primaryEvidence = component.evidence.slice(0, 2).join(' · ')
  return (
    <li
      className={clsx(
        'grid grid-cols-[96px_1fr_48px] items-center gap-3 text-[13px]',
        component.missingData && 'opacity-60',
      )}
    >
      <span className="truncate text-ink-muted">{componentLabel(component.key)}</span>
      <div className="space-y-1">
        <div className="h-1.5 w-full rounded-full bg-hairline/60">
          <div
            className={clsx('h-1.5 rounded-full', barClassName(component.score, component.missingData))}
            style={{ width: `${widthPct}%` }}
          />
        </div>
        {primaryEvidence ? <p className="line-clamp-1 text-[12px] text-ink-muted">{primaryEvidence}</p> : null}
      </div>
      <span className="text-right tabular-nums text-ink">
        {component.missingData ? '—' : component.score.toFixed(2)}
      </span>
    </li>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="data-label">{label}</dt>
      <dd className="mt-1 text-[14px] text-ink">{value}</dd>
    </div>
  )
}

function Section({
  title,
  children,
  className,
}: {
  title: string
  children: ReactNode
  className?: string
}) {
  return (
    <section className={clsx('rounded-card border border-hairline bg-surface p-5', className)}>
      <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function nearbyCheckSummary(place: ListingDetail['nearbyPreferencePlaces'][number]): {
  status: string
  detail: string | null
} {
  if (!place.searched) {
    return { status: 'Lookup unavailable', detail: null }
  }
  if (place.distanceM !== null) {
    return {
      status: `${place.distanceM} m away`,
      detail: place.placeName ? place.placeName : null,
    }
  }
  return { status: 'None found nearby', detail: null }
}

export function ListingDrawer({ open, listing, onClose }: ListingDrawerProps) {
  const { username } = useSession()
  const [detail, setDetail] = useState<ListingDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !listing || !username) return
    setDetail(null)
    setError(null)
    setLoading(true)
    let cancelled = false
    void (async () => {
      try {
        const nextDetail = await getListingDetail(listing.id, username)
        if (!cancelled) setDetail(nextDetail)
      } catch (nextError) {
        if (!cancelled) setError(nextError instanceof Error ? nextError.message : String(nextError))
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, listing?.id, username])

  const activeListing: Listing | null = detail?.listing ?? listing

  return (
    <Drawer
      open={open}
      onClose={onClose}
      widthClass="w-[560px]"
      title={
        activeListing ? (
          <div className="flex items-center gap-3">
            <span className="truncate">{activeListing.title ?? `Listing ${activeListing.id}`}</span>
            {activeListing.kind ? (
              <StatusPill tone="idle">{activeListing.kind === 'flat' ? 'Whole flat' : 'WG room'}</StatusPill>
            ) : null}
            <StatusPill tone={scoreTone(activeListing.score)}>{formatScore(activeListing.score)}</StatusPill>
          </div>
        ) : (
          'Listing'
        )
      }
    >
      {activeListing ? (
        <div className="space-y-6">
          {detail && detail.photos.length > 0 ? (
            <div className="overflow-hidden rounded-card border border-hairline bg-surface">
              <img
                src={detail.photos[0]}
                alt={activeListing.title ?? activeListing.id}
                className="w-full object-cover"
                style={{ maxHeight: 360 }}
              />
            </div>
          ) : null}

          <Section title="Listing facts">
            <dl className="grid grid-cols-2 gap-x-4 gap-y-4 text-[14px]">
              <Stat label="Price" value={activeListing.priceEur !== null ? `${activeListing.priceEur} EUR` : '—'} />
              <Stat label="Size" value={activeListing.sizeM2 !== null ? `${activeListing.sizeM2} m²` : '—'} />
              <Stat label="WG size" value={activeListing.wgSize !== null ? `${activeListing.wgSize} people` : '—'} />
              <Stat label="District" value={activeListing.district ?? '—'} />
              <Stat
                label="Available"
                value={`${activeListing.availableFrom ?? '—'}${activeListing.availableTo ? ` → ${activeListing.availableTo}` : ''}`}
              />
            </dl>
          </Section>

          {activeListing.vetoReason ? (
            <Section title="Why it was rejected" className="border-bad/35 bg-bad/5">
              <p className="text-[14px] leading-6 text-ink">{activeListing.vetoReason}</p>
            </Section>
          ) : activeListing.components.length > 0 ? (
            <Section title="Score breakdown">
              {activeListing.scoreReason ? (
                <p className="text-[13px] leading-6 text-ink-muted">{activeListing.scoreReason}</p>
              ) : null}
              <ul className="mt-4 space-y-2">
                {activeListing.components.map((component) => (
                  <ComponentBar key={component.key} component={component} />
                ))}
              </ul>
            </Section>
          ) : activeListing.scoreReason ? (
            <Section title="Why it stands out">
              <p className="text-[14px] leading-6 text-ink">{activeListing.scoreReason}</p>
              {activeListing.matchReasons.length > 0 ? (
                <ul className="mt-3 list-inside list-disc text-[13px] leading-6 text-good">
                  {activeListing.matchReasons.map((reason, index) => (
                    <li key={`match-${index}`}>{reason}</li>
                  ))}
                </ul>
              ) : null}
              {activeListing.mismatchReasons.length > 0 ? (
                <ul className="mt-3 list-inside list-disc text-[13px] leading-6 text-bad">
                  {activeListing.mismatchReasons.map((reason, index) => (
                    <li key={`mismatch-${index}`}>{reason}</li>
                  ))}
                </ul>
              ) : null}
            </Section>
          ) : null}

          {detail?.travelMinutesPerLocation && Object.keys(detail.travelMinutesPerLocation).length > 0 ? (
            <Section title="Commute">
              <ul className="space-y-2 text-[13px] text-ink">
                {Object.entries(detail.travelMinutesPerLocation).map(([label, minutes]) => (
                  <li key={label} className="flex items-start justify-between gap-4">
                    <span className="text-ink-muted">{label}</span>
                    <span>{minutes} min</span>
                  </li>
                ))}
              </ul>
            </Section>
          ) : null}

          {detail && detail.nearbyPreferencePlaces.length > 0 ? (
            <Section title="Nearby preference checks">
              <ul className="space-y-2">
                {detail.nearbyPreferencePlaces.map((place) => {
                  const summary = nearbyCheckSummary(place)
                  return (
                    <li
                      key={place.key}
                      className="grid gap-1 rounded border border-hairline bg-surface-raised px-3 py-3 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-start sm:gap-3"
                    >
                      <div className="min-w-0">
                        <p className="text-[13px] font-medium text-ink">{place.label}</p>
                        {summary.detail ? (
                          <p className="mt-1 break-words text-[12px] leading-5 text-ink-muted">{summary.detail}</p>
                        ) : null}
                      </div>
                      <p className="text-[12px] leading-5 text-ink-muted sm:text-right">{summary.status}</p>
                    </li>
                  )
                })}
              </ul>
            </Section>
          ) : null}

          {activeListing.description ? (
            <Section title="Original description">
              <p className="whitespace-pre-wrap text-[14px] leading-7 text-ink">{activeListing.description}</p>
            </Section>
          ) : null}

          {detail && detail.photos.length > 1 ? (
            <Section title="More photos">
              <div className="grid grid-cols-2 gap-2">
                {detail.photos.slice(1).map((url, index) => (
                  <img
                    key={index}
                    src={url}
                    alt=""
                    className="h-32 w-full rounded border border-hairline object-cover"
                  />
                ))}
              </div>
            </Section>
          ) : null}

          <div className="flex flex-wrap items-center gap-3 border-t border-hairline pt-4">
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                window.open(activeListing.url, '_blank', 'noopener,noreferrer')
              }}
            >
              Open on WG-Gesucht
            </Button>
            <Button variant="secondary" size="sm" disabled>
              Messaging comes later
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
