import clsx from 'clsx'
import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  MissingLandlordInfoError,
  draftListingMessage,
  getListingDetail,
} from '../lib/api'
import { formatGermanDateRange } from '../lib/date'
import { useSession } from '../lib/session'
import type { Component, Listing, ListingDetail, User } from '../types'
import { Button, Drawer, StatusPill, Textarea, type StatusPillTone } from './ui'

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
  return score === null ? 'Pending' : `${Math.round(score * 100)}%`
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
        'grid grid-cols-[88px_minmax(0,1fr)_56px] items-start gap-3 text-[13px]',
        component.missingData && 'opacity-60',
      )}
    >
      <span className="leading-5 text-ink-muted">{componentLabel(component.key)}</span>
      <div className="space-y-1">
        <div className="h-1.5 w-full rounded-full bg-hairline/60">
          <div
            className={clsx('h-1.5 rounded-full', barClassName(component.score, component.missingData))}
            style={{ width: `${widthPct}%` }}
          />
        </div>
        {primaryEvidence ? <p className="break-words text-[12px] leading-5 text-ink-muted">{primaryEvidence}</p> : null}
      </div>
      <span className="text-right tabular-nums text-ink">
        {component.missingData ? '—' : `${Math.round(component.score * 100)}%`}
      </span>
    </li>
  )
}

function modeLabel(mode: string): string {
  const normalized = mode.toLowerCase()
  if (normalized === 'drive') return 'car'
  if (normalized === 'bicycle') return 'bike'
  if (normalized === 'transit') return 'public transit'
  return normalized
}

function fastestMode(
  modes: Record<string, number>,
): { mode: string; minutes: number } | null {
  let best: { mode: string; minutes: number } | null = null
  for (const [mode, minutes] of Object.entries(modes)) {
    if (typeof minutes !== 'number' || Number.isNaN(minutes)) continue
    if (best === null || minutes < best.minutes) {
      best = { mode, minutes }
    }
  }
  return best
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
    <section className={clsx('rounded-card border border-hairline bg-surface p-4 sm:p-5', className)}>
      <h3 className="text-[15px] font-semibold text-ink">{title}</h3>
      <div className="mt-3">{children}</div>
    </section>
  )
}

function hasLandlordInfo(user: User | null): boolean {
  if (!user) return false
  const firstName = (user.firstName ?? '').trim()
  const occupation = (user.occupation ?? '').trim()
  const bio = (user.bio ?? '').trim()
  const email = (user.email ?? '').trim()
  return Boolean(firstName && occupation && bio && email)
}

type DraftStatus = 'idle' | 'loading' | 'ready' | 'error' | 'missing_info'

type DraftState = {
  status: DraftStatus
  message: string
  error: string | null
}

const INITIAL_DRAFT_STATE: DraftState = { status: 'idle', message: '', error: null }

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
  const navigate = useNavigate()
  const { username, user } = useSession()
  const [detail, setDetail] = useState<ListingDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [draft, setDraft] = useState<DraftState>(INITIAL_DRAFT_STATE)
  const [copied, setCopied] = useState(false)

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

  // Reset the draft panel whenever the drawer switches to a different
  // listing or closes — drafts are scoped to the currently open row.
  useEffect(() => {
    setDraft(INITIAL_DRAFT_STATE)
    setCopied(false)
  }, [listing?.id, open])

  const activeListing: Listing | null = detail?.listing ?? listing

  const handleDraftClick = useCallback(async () => {
    if (!activeListing || !username) return
    if (!hasLandlordInfo(user)) {
      setDraft({ status: 'missing_info', message: '', error: null })
      return
    }
    setDraft((prev) => ({ status: 'loading', message: prev.message, error: null }))
    setCopied(false)
    try {
      const { message } = await draftListingMessage(username, activeListing.id)
      setDraft({ status: 'ready', message, error: null })
    } catch (err) {
      if (err instanceof MissingLandlordInfoError) {
        setDraft({ status: 'missing_info', message: '', error: null })
        return
      }
      setDraft({
        status: 'error',
        message: '',
        error: err instanceof Error ? err.message : String(err),
      })
    }
  }, [activeListing, username, user])

  const handleCopy = useCallback(async () => {
    if (!draft.message) return
    try {
      await navigator.clipboard.writeText(draft.message)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 2000)
    } catch {
      setCopied(false)
    }
  }, [draft.message])

  const goToProfileSettings = useCallback(() => {
    onClose()
    navigate('/profile#landlord-info')
  }, [navigate, onClose])

  const draftButtonLabel =
    draft.status === 'loading'
      ? 'Drafting…'
      : draft.status === 'ready'
        ? 'Regenerate message'
        : 'Draft message to landlord'

  return (
    <Drawer
      open={open}
      onClose={onClose}
      widthClass="w-full sm:w-[560px]"
      title={
        activeListing ? (
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
            <span className="min-w-0 basis-full truncate sm:basis-auto">
              {activeListing.title ?? `Listing ${activeListing.id}`}
            </span>
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
          <div className="flex flex-wrap items-center gap-3">
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                window.open(activeListing.url, '_blank', 'noopener,noreferrer')
              }}
            >
              Open on WG-Gesucht
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void handleDraftClick()}
              disabled={draft.status === 'loading'}
            >
              {draftButtonLabel}
            </Button>
          </div>

          {draft.status === 'missing_info' ? (
            <Section title="Draft a message to the landlord">
              <p className="text-[13px] leading-6 text-ink-muted">
                Add your "Information for landlord" in Profile settings first — a first
                name, occupation and a short bio is all we need to personalize the
                message.
              </p>
              <div className="mt-4">
                <Button variant="primary" size="sm" onClick={goToProfileSettings}>
                  Go to profile settings
                </Button>
              </div>
            </Section>
          ) : null}

          {draft.status === 'loading' && !draft.message ? (
            <Section title="Draft a message to the landlord">
              <p className="text-[13px] text-ink-muted">Drafting a personalized message…</p>
            </Section>
          ) : null}

          {(draft.status === 'ready' || (draft.status === 'loading' && draft.message)) ? (
            <Section title="Draft a message to the landlord">
              <p className="text-[12px] leading-5 text-ink-muted">
                Edit if you like, then click Copy and paste it into the WG-Gesucht
                contact dialog.
              </p>
              <Textarea
                className="mt-3"
                rows={10}
                value={draft.message}
                onChange={(event) =>
                  setDraft((prev) => ({ ...prev, message: event.target.value }))
                }
                disabled={draft.status === 'loading'}
              />
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => void handleCopy()}
                  disabled={!draft.message}
                >
                  {copied ? 'Copied' : 'Copy'}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => void handleDraftClick()}
                  disabled={draft.status === 'loading'}
                >
                  {draft.status === 'loading' ? 'Regenerating…' : 'Regenerate'}
                </Button>
              </div>
            </Section>
          ) : null}

          {draft.status === 'error' && draft.error ? (
            <Section title="Draft a message to the landlord" className="border-bad/35 bg-bad/5">
              <p className="text-[13px] leading-6 text-ink">
                Could not draft a message: {draft.error}
              </p>
              <div className="mt-3">
                <Button variant="secondary" size="sm" onClick={() => void handleDraftClick()}>
                  Try again
                </Button>
              </div>
            </Section>
          ) : null}

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
                value={formatGermanDateRange(activeListing.availableFrom, activeListing.availableTo)}
              />
            </dl>
          </Section>

          {activeListing.vetoReason ? (
            <Section title="Why it did not fit" className="border-bad/35 bg-bad/5">
              <p className="text-[14px] leading-6 text-ink">{activeListing.vetoReason}</p>
            </Section>
          ) : activeListing.components.length > 0 ? (
            <Section title="Why it ranks here">
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
                {Object.entries(detail.travelMinutesPerLocation).map(([label, modes]) => {
                  const best = fastestMode(modes)
                  return (
                    <li key={label} className="grid grid-cols-[minmax(0,1fr)_auto] items-start gap-4">
                      <span className="break-words text-ink-muted">{label}</span>
                      <span className="text-right">
                        {best ? (
                          <>
                            {best.minutes} min
                            <span className="block text-[12px] text-ink-muted">
                              via {modeLabel(best.mode)}
                            </span>
                          </>
                        ) : (
                          <span className="text-[12px] text-ink-muted">no data</span>
                        )}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </Section>
          ) : null}

          {detail && detail.nearbyPreferencePlaces.length > 0 ? (
            <Section title="Nearby highlights">
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
            <Section title="Listing description">
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

          {loading ? <p className="text-[13px] text-ink-muted">Loading…</p> : null}
          {error ? <p className="text-[13px] text-bad">{error}</p> : null}
        </div>
      ) : (
        <p className="text-[13px] text-ink-muted">No listing selected.</p>
      )}
    </Drawer>
  )
}
