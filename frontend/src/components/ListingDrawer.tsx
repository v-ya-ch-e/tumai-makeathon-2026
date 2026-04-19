import clsx from 'clsx'
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  MissingLandlordInfoError,
  draftListingMessage,
  getListingDetail,
  getListingMessageDraft,
  saveListingMessageDraft,
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

/**
 * Drawer draft state machine:
 *
 *   - `hydrating` — checking the DB for an existing draft (GET on open)
 *   - `missing_info` — user hasn't filled "Information for landlord"
 *   - `empty` — landlord info present, no saved draft yet → show the
 *     prominent "Generate message" CTA
 *   - `generating` — POST in flight
 *   - `ready` — we have a draft (either just generated or loaded from DB)
 *   - `error` — last generate attempt failed; `message` may still hold
 *     the previous draft
 */
type DraftStatus =
  | 'hydrating'
  | 'missing_info'
  | 'empty'
  | 'generating'
  | 'ready'
  | 'error'

type DraftState = {
  status: DraftStatus
  message: string
  /** `true` once the user has edited since load — disables auto-save
   * until a change actually happens. */
  dirty: boolean
  error: string | null
}

const INITIAL_DRAFT_STATE: DraftState = {
  status: 'hydrating',
  message: '',
  dirty: false,
  error: null,
}

const AUTO_SAVE_DELAY_MS = 1200

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
  const [saveStatus, setSaveStatus] = useState<'idle' | 'saving' | 'saved' | 'error'>(
    'idle',
  )

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

  // Hydrate the draft section when the drawer opens (or switches rows):
  //   1. If the user has no landlord info → `missing_info`.
  //   2. Else GET the persisted draft. If one exists → `ready`; else `empty`.
  // Keeps the UI showing the same text between sessions without a second
  // LLM call.
  useEffect(() => {
    setDraft(INITIAL_DRAFT_STATE)
    setCopied(false)
    setSaveStatus('idle')
    if (!open || !listing || !username) return
    if (!hasLandlordInfo(user)) {
      setDraft({ status: 'missing_info', message: '', dirty: false, error: null })
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const existing = await getListingMessageDraft(username, listing.id)
        if (cancelled) return
        if (existing) {
          setDraft({
            status: 'ready',
            message: existing.message,
            dirty: false,
            error: null,
          })
        } else {
          setDraft({ status: 'empty', message: '', dirty: false, error: null })
        }
      } catch {
        if (cancelled) return
        // GET failures shouldn't block generation — fall through to empty.
        setDraft({ status: 'empty', message: '', dirty: false, error: null })
      }
    })()
    return () => {
      cancelled = true
    }
  }, [open, listing?.id, username, user])

  // Debounced auto-save for user edits. Fires only once the draft is
  // marked `dirty` by an onChange (so loading an existing draft doesn't
  // immediately trigger a PUT).
  useEffect(() => {
    if (!open || !listing || !username) return
    if (draft.status !== 'ready') return
    if (!draft.dirty) return
    const trimmed = draft.message.trim()
    if (!trimmed) {
      setSaveStatus('idle')
      return
    }
    setSaveStatus('saving')
    const handle = window.setTimeout(() => {
      void (async () => {
        try {
          await saveListingMessageDraft(username, listing.id, trimmed)
          setSaveStatus('saved')
          window.setTimeout(() => {
            setSaveStatus((prev) => (prev === 'saved' ? 'idle' : prev))
          }, 1500)
        } catch {
          setSaveStatus('error')
        }
      })()
    }, AUTO_SAVE_DELAY_MS)
    return () => {
      window.clearTimeout(handle)
    }
  }, [draft.status, draft.dirty, draft.message, open, listing?.id, username])

  const activeListing: Listing | null = detail?.listing ?? listing

  // Keep a stable reference to the listing id we dispatched a generate
  // for, so out-of-order responses (user clicks Regenerate twice) can't
  // clobber each other.
  const generationIdRef = useRef(0)

  const handleGenerateClick = useCallback(async () => {
    if (!activeListing || !username) return
    if (!hasLandlordInfo(user)) {
      setDraft({ status: 'missing_info', message: '', dirty: false, error: null })
      return
    }
    const ticket = ++generationIdRef.current
    setDraft((prev) => ({
      status: 'generating',
      message: prev.message,
      dirty: false,
      error: null,
    }))
    setCopied(false)
    setSaveStatus('idle')
    try {
      const result = await draftListingMessage(username, activeListing.id)
      if (ticket !== generationIdRef.current) return
      setDraft({ status: 'ready', message: result.message, dirty: false, error: null })
    } catch (err) {
      if (ticket !== generationIdRef.current) return
      if (err instanceof MissingLandlordInfoError) {
        setDraft({ status: 'missing_info', message: '', dirty: false, error: null })
        return
      }
      setDraft((prev) => ({
        status: 'error',
        message: prev.message,
        dirty: false,
        error: err instanceof Error ? err.message : String(err),
      }))
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

  return (
    <Drawer
      open={open}
      onClose={onClose}
      widthClass="w-full sm:w-[560px]"
      title={
        activeListing ? (
          <span className="block text-[17px] font-semibold leading-snug text-ink">
            {activeListing.title ?? `Listing ${activeListing.id}`}
          </span>
        ) : (
          'Listing'
        )
      }
    >
      {activeListing ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill tone={scoreTone(activeListing.score)}>
              Match {formatScore(activeListing.score)}
            </StatusPill>
            {activeListing.kind ? (
              <StatusPill tone="idle">
                {activeListing.kind === 'flat' ? 'Whole flat' : 'WG room'}
              </StatusPill>
            ) : null}
            {activeListing.district ? (
              <StatusPill tone="idle">{activeListing.district}</StatusPill>
            ) : null}
          </div>

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
          </div>

          <DraftMessagePanel
            draft={draft}
            copied={copied}
            saveStatus={saveStatus}
            onGenerate={() => void handleGenerateClick()}
            onCopy={() => void handleCopy()}
            onGoToProfileSettings={goToProfileSettings}
            onChangeMessage={(value) =>
              setDraft((prev) => ({
                ...prev,
                status: 'ready',
                message: value,
                dirty: true,
                error: null,
              }))
            }
          />

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

type DraftMessagePanelProps = {
  draft: DraftState
  copied: boolean
  saveStatus: 'idle' | 'saving' | 'saved' | 'error'
  onGenerate: () => void
  onCopy: () => void
  onGoToProfileSettings: () => void
  onChangeMessage: (value: string) => void
}

function DraftMessagePanel({
  draft,
  copied,
  saveStatus,
  onGenerate,
  onCopy,
  onGoToProfileSettings,
  onChangeMessage,
}: DraftMessagePanelProps) {
  const title = 'Draft a message to the landlord'

  if (draft.status === 'hydrating') {
    return (
      <Section title={title}>
        <p className="text-[13px] text-ink-muted">Checking for a saved draft…</p>
      </Section>
    )
  }

  if (draft.status === 'missing_info') {
    return (
      <Section
        title={title}
        className="border-accent/40 bg-accent-muted/30"
      >
        <p className="text-[14px] leading-6 text-ink">
          Let AI write a personalized first message to this landlord, ready to paste
          into WG-Gesucht.
        </p>
        <p className="mt-2 text-[13px] leading-6 text-ink-muted">
          First, add a short "Information for landlord" in Profile settings — name,
          occupation and a couple of sentences about you.
        </p>
        <div className="mt-4">
          <Button variant="primary" size="sm" onClick={onGoToProfileSettings}>
            Add info in profile settings
          </Button>
        </div>
      </Section>
    )
  }

  if (draft.status === 'empty') {
    return (
      <Section
        title={title}
        className="border-accent/40 bg-accent-muted/30"
      >
        <p className="text-[14px] leading-6 text-ink">
          Let AI write a personalized first message to this landlord, based on your
          profile and this listing. You can edit it before copying.
        </p>
        <div className="mt-4">
          <Button variant="primary" size="md" onClick={onGenerate}>
            Generate personalized message
          </Button>
        </div>
      </Section>
    )
  }

  if (draft.status === 'generating' && !draft.message) {
    return (
      <Section title={title}>
        <p className="text-[13px] text-ink-muted">Drafting a personalized message…</p>
      </Section>
    )
  }

  const textareaDisabled = draft.status === 'generating'
  const showError = draft.status === 'error' && draft.error

  return (
    <Section
      title={title}
      className={clsx(
        draft.status === 'ready' && 'border-accent/40 bg-accent-muted/20',
        showError && 'border-bad/35 bg-bad/5',
      )}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-[12px] leading-5 text-ink-muted">
          Edit if you like, then copy and paste into the WG-Gesucht contact dialog.
        </p>
        <SaveStatusBadge status={saveStatus} />
      </div>

      <Textarea
        className="mt-3"
        rows={10}
        value={draft.message}
        onChange={(event) => onChangeMessage(event.target.value)}
        disabled={textareaDisabled}
      />

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="primary" size="sm" onClick={onCopy} disabled={!draft.message}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onClick={onGenerate}
          disabled={draft.status === 'generating'}
        >
          {draft.status === 'generating' ? 'Regenerating…' : 'Regenerate'}
        </Button>
      </div>

      {showError ? (
        <p className="mt-3 text-[13px] leading-6 text-bad">
          Could not generate: {draft.error}
        </p>
      ) : null}
    </Section>
  )
}

function SaveStatusBadge({
  status,
}: {
  status: 'idle' | 'saving' | 'saved' | 'error'
}) {
  if (status === 'idle') return null
  const label =
    status === 'saving' ? 'Saving…' : status === 'saved' ? 'Saved' : 'Save failed'
  const tone =
    status === 'error'
      ? 'text-bad'
      : status === 'saved'
        ? 'text-good'
        : 'text-ink-muted'
  return <span className={clsx('text-[12px]', tone)}>{label}</span>
}
