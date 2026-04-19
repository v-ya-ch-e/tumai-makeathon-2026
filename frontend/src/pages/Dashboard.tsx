import clsx from 'clsx'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { AppTabs } from '../components/AppTabs'
import { ListingDrawer } from '../components/ListingDrawer'
import {
  DEFAULT_LISTING_FILTERS,
  ListingFilterBar,
  applyListingFilters,
  isListingNew,
  isLowScore,
  type ListingFilters,
} from '../components/ListingFilterBar'
import { ListingList } from '../components/ListingList'
import { ListingMap } from '../components/ListingMap'
import { Button, ProgressBar, StatusPill, type StatusPillTone } from '../components/ui'
import { formatGermanDate } from '../lib/date'
import {
  ApiError,
  createHunt,
  getHunt,
  getSearchProfile,
  stopHunt,
  streamHunt,
} from '../lib/api'
import { useHiddenListings } from '../lib/hiddenListings'
import { useSession } from '../lib/session'
import type { Action, Hunt, Listing, SearchProfile } from '../types'

const LS_HUNT_ID = 'wg-hunter.hunt-id'

type UiStatus = 'idle' | 'starting' | 'running' | 'stopping' | 'error'

function statusPillTone(status: UiStatus): StatusPillTone {
  if (status === 'running') return 'running'
  if (status === 'starting' || status === 'stopping') return 'rescanning'
  if (status === 'error') return 'bad'
  return 'idle'
}

function statusLabel(status: UiStatus): string {
  if (status === 'running') return 'Running'
  if (status === 'starting') return 'Starting'
  if (status === 'stopping') return 'Stopping'
  if (status === 'error') return 'Error'
  return 'Idle'
}

function huntToUiStatus(hunt: Hunt | null): UiStatus {
  if (hunt === null) return 'idle'
  if (hunt.status === 'running' || hunt.status === 'pending') return 'running'
  if (hunt.status === 'failed') return 'error'
  return 'idle'
}

function huntIdForUsername(username: string): string {
  return `user:${encodeURIComponent(username)}`
}

function hasActiveBackfill(hunt: Hunt | null): boolean {
  if (hunt === null) return false
  if (hunt.backfillTotal === null || hunt.backfillTotal <= 0) return false
  return (hunt.backfillDone ?? 0) < hunt.backfillTotal
}

function topScorePct(listings: Listing[]): string {
  const scored = listings.map((listing) => listing.score).filter((score): score is number => score !== null)
  if (scored.length === 0) return '—'
  return `${Math.round(Math.max(...scored) * 100)}%`
}

function moveInLabel(profile: SearchProfile): string {
  if (profile.moveInFrom) return formatGermanDate(profile.moveInFrom)
  return 'Flexible'
}

function moveInNote(profile: SearchProfile): string {
  if (profile.moveInUntil) return `Until ${formatGermanDate(profile.moveInUntil)}`
  if (profile.moveInFrom) return 'Open-ended'
  return 'No earliest date'
}

export default function Dashboard() {
  const navigate = useNavigate()
  const location = useLocation()
  const { username, isReady, setUsername, user } = useSession()

  const [profile, setProfile] = useState<SearchProfile | null>(null)
  const [hunt, setHunt] = useState<Hunt | null>(null)
  const [, setActions] = useState<Action[]>([])
  const [listings, setListings] = useState<Listing[]>([])
  const [uiStatus, setUiStatus] = useState<UiStatus>('idle')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [openListing, setOpenListing] = useState<Listing | null>(null)
  const [viewMode, setViewMode] = useState<'list' | 'map'>('list')
  const [filters, setFilters] = useState<ListingFilters>(DEFAULT_LISTING_FILTERS)
  const [initialLoading, setInitialLoading] = useState(true)
  // Captures the `autoStart` intent coming from the onboarding flow at mount
  // time. React Router clears `location.state` once the autoStart effect
  // fires, so we snapshot it here. Two places use the snapshot: the
  // bootstrap effect polls `/agent` briefly so the skeleton can transition
  // straight into the live backfill progress bar, and the skeleton itself
  // renders the progress-shaped variant so that transition isn't jarring.
  const [hasAutoStartIntent] = useState(() => {
    const s = location.state
    return Boolean(
      s &&
        typeof s === 'object' &&
        'autoStart' in s &&
        (s as { autoStart?: boolean }).autoStart === true,
    )
  })

  // Prefer the backfill baseline (bumped on material profile edits) over
  // raw account-creation time: listings scored by the silent re-backfill
  // should not light up the "new" badge, while listings scraped afterwards
  // still correctly flag as new.
  const baselineAt = user?.backfillBaselineAt ?? user?.createdAt ?? null
  const { hiddenIds, isHidden, toggle: toggleHidden } = useHiddenListings(username)
  const isNewListing = useCallback(
    (listing: Listing) => isListingNew(listing, baselineAt),
    [baselineAt],
  )
  const isListingHidden = useCallback(
    (listing: Listing) => isHidden(listing.id),
    [isHidden],
  )
  const newCount = useMemo(
    () => listings.filter((listing) => isNewListing(listing)).length,
    [listings, isNewListing],
  )
  const hiddenCount = useMemo(
    () => listings.reduce((acc, listing) => (hiddenIds.has(listing.id) ? acc + 1 : acc), 0),
    [listings, hiddenIds],
  )
  const lowScoreCount = useMemo(
    () => listings.reduce((acc, listing) => (isLowScore(listing) ? acc + 1 : acc), 0),
    [listings],
  )
  const visibleListings = useMemo(
    () => applyListingFilters(listings, filters, baselineAt, isListingHidden),
    [listings, filters, baselineAt, isListingHidden],
  )
  const isBackfilling = hasActiveBackfill(hunt)
  const seenActionKeysRef = useRef<Set<string>>(new Set())
  const autoStartTriggeredRef = useRef(false)
  const refreshTimerRef = useRef<number | null>(null)
  const refreshInFlightRef = useRef(false)

  const actionKey = (action: Action): string => `${action.at}|${action.kind}|${action.summary}`

  const applyHunt = useCallback((nextHunt: Hunt | null) => {
    if (nextHunt === null) {
      setHunt(null)
      setActions([])
      setListings([])
      setUiStatus('idle')
      seenActionKeysRef.current = new Set()
      return
    }
    setHunt(nextHunt)
    setListings(nextHunt.listings)
    setActions(nextHunt.actions)
    seenActionKeysRef.current = new Set(nextHunt.actions.map(actionKey))
    setUiStatus(huntToUiStatus(nextHunt))
  }, [])

  const refreshHunt = useCallback(async (id: string | null): Promise<Hunt | null> => {
    if (!id) return null
    const nextHunt = await getHunt(id)
    if (nextHunt === null) {
      localStorage.removeItem(LS_HUNT_ID)
      return null
    }
    return nextHunt
  }, [])

  useEffect(() => {
    if (!isReady) return
    if (!username) {
      navigate('/onboarding/profile', { replace: true })
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const searchProfile = await getSearchProfile(username)
        if (cancelled) return
        if (searchProfile === null) {
          navigate('/onboarding/requirements', { replace: true })
          return
        }
        const storedId = localStorage.getItem(LS_HUNT_ID)
        const expectedHuntId = huntIdForUsername(username)
        const huntId = storedId === expectedHuntId ? storedId : expectedHuntId
        if (storedId !== huntId) {
          localStorage.setItem(LS_HUNT_ID, huntId)
        }
        let nextHunt = await refreshHunt(huntId)
        // Fresh-from-onboarding flow: the backend spawned the matcher in
        // `PUT /search-profile`, but the one-shot backfill may not have
        // published its `{done, total}` snapshot yet when we query
        // `/agent` for the first time. Poll briefly so the skeleton
        // transitions straight into the live progress bar instead of
        // flashing the idle "0 listings" stats row. We also poll when we
        // already have a hunt mid-backfill so the first render reflects
        // that state up front.
        if (!cancelled && (hasAutoStartIntent || hasActiveBackfill(nextHunt))) {
          const deadline = Date.now() + 3000
          while (
            !cancelled &&
            Date.now() < deadline &&
            !hasActiveBackfill(nextHunt)
          ) {
            await new Promise((resolve) => setTimeout(resolve, 250))
            if (cancelled) return
            nextHunt = await refreshHunt(huntId)
          }
        }
        if (cancelled) return
        setProfile(searchProfile)
        applyHunt(nextHunt)
      } finally {
        if (!cancelled) setInitialLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username, navigate, refreshHunt, applyHunt, hasAutoStartIntent])

  useEffect(() => {
    const huntId = hunt?.id
    if (!huntId) return
    if (hunt?.status === 'done' || hunt?.status === 'failed') return

    let closed = false
    let closeFn: (() => void) | null = null

    const scheduleRefresh = () => {
      if (closed) return
      if (refreshTimerRef.current !== null) return
      refreshTimerRef.current = window.setTimeout(() => {
        refreshTimerRef.current = null
        if (closed) return
        if (refreshInFlightRef.current) {
          scheduleRefresh()
          return
        }
        refreshInFlightRef.current = true
        void (async () => {
          try {
            const fresh = await refreshHunt(huntId)
            if (closed || !fresh) return
            setListings(fresh.listings)
            setHunt(fresh)
            setUiStatus(huntToUiStatus(fresh))
          } finally {
            refreshInFlightRef.current = false
          }
        })()
      }, 750)
    }

    closeFn = streamHunt(huntId, (event) => {
      if ('kind' in event && event.kind === 'stream-end') {
        if (!closed) {
          closed = true
          closeFn?.()
        }
        void (async () => {
          const fresh = await refreshHunt(huntId)
          if (fresh) applyHunt(fresh)
        })()
        return
      }
      const action = event as Action
      // Backfill progress events fire ~one per scored listing; we refresh the
      // progress bar directly from the action's `detail` JSON instead of
      // round-tripping /agent every time, and intentionally skip
      // `scheduleRefresh` here because the per-listing `evaluate` /
      // `new_listing` actions already drive the debounced listings refetch.
      if (action.kind === 'backfill_progress') {
        try {
          const payload = action.detail ? JSON.parse(action.detail) : null
          if (
            payload &&
            typeof payload === 'object' &&
            typeof (payload as { done?: unknown }).done === 'number' &&
            typeof (payload as { total?: unknown }).total === 'number'
          ) {
            const done = (payload as { done: number }).done
            const total = (payload as { total: number }).total
            setHunt((prev) =>
              prev === null
                ? prev
                : {
                    ...prev,
                    backfillDone: done,
                    backfillTotal: total,
                  },
            )
          }
        } catch {
          // Malformed detail — ignore silently; the /agent status poll after
          // the next listing refresh will reconcile.
        }
        return
      }
      const key = actionKey(action)
      if (seenActionKeysRef.current.has(key)) return
      seenActionKeysRef.current.add(key)
      setActions((prev) => [...prev, action])
      if (action.kind === 'evaluate' || action.kind === 'new_listing') {
        scheduleRefresh()
      }
    })

    return () => {
      closed = true
      if (refreshTimerRef.current !== null) {
        window.clearTimeout(refreshTimerRef.current)
        refreshTimerRef.current = null
      }
      closeFn?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hunt?.id])

  const onStart = async () => {
    if (!username || !profile) return
    setErrorMessage(null)
    setUiStatus('starting')
    try {
      const nextHunt = await createHunt(username, { schedule: profile.schedule })
      localStorage.setItem(LS_HUNT_ID, nextHunt.id)
      applyHunt(nextHunt)
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : String(error))
      setUiStatus('error')
    }
  }

  const onLogout = () => {
    localStorage.removeItem(LS_HUNT_ID)
    setOpenListing(null)
    setUsername(null)
    navigate('/onboarding/profile', { replace: true })
  }

  const onStop = async () => {
    if (!hunt) return
    setErrorMessage(null)
    setUiStatus('stopping')
    try {
      const nextHunt = await stopHunt(hunt.id)
      applyHunt(nextHunt)
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : String(error))
      setUiStatus('error')
    }
  }

  useEffect(() => {
    if (!profile || !username) return
    if (!location.state || typeof location.state !== 'object' || !('autoStart' in location.state)) return
    if ((location.state as { autoStart?: boolean }).autoStart !== true) return
    if (autoStartTriggeredRef.current) return
    autoStartTriggeredRef.current = true
    navigate(location.pathname, { replace: true, state: null })
    if (hunt === null) void onStart()
  }, [location.pathname, location.state, navigate, profile, username, hunt])

  const isBootstrapping = !isReady || profile === null || initialLoading
  const isActive = uiStatus === 'running' || uiStatus === 'starting'
  const isStopping = uiStatus === 'stopping'
  const isStarting = uiStatus === 'starting'

  return (
    <div className="min-h-screen bg-canvas">
      <div className="app-shell space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="brand-wordmark">Sherlock Homes</p>
            <p className="mt-1 max-w-xl text-[14px] text-ink-muted">
              A smarter search for places in Munich that fit your lifestyle.
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <AppTabs
              current="/dashboard"
              tabs={[
                { label: 'Dashboard', href: '/dashboard' },
                { label: 'Profile', href: '/profile' },
              ]}
            />
            <button
              type="button"
              onClick={onLogout}
              className="rounded-full border border-hairline bg-surface px-4 py-2 text-[13px] font-medium text-ink transition-colors hover:border-ink"
            >
              Log out
            </button>
          </div>
        </header>

        {isBootstrapping || profile === null ? (
          <DashboardSkeleton variant={hasAutoStartIntent ? 'progress' : 'stats'} />
        ) : (
        <>
        <section className="page-frame overflow-hidden">
          <div className="flex flex-col gap-6 px-6 py-8 sm:px-8 lg:flex-row lg:items-start lg:justify-between lg:px-10">
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-3">
                <h1 className="page-title">Your search</h1>
                <StatusPill tone={statusPillTone(uiStatus)}>{statusLabel(uiStatus)}</StatusPill>
              </div>
              <p className="body-copy mt-3 max-w-2xl">
                Fresh matches and trade-offs to keep your shortlist moving.
              </p>
            </div>
            <div className="shrink-0">
              {isActive ? (
                <Button
                  variant="secondary"
                  shape="pill"
                  onClick={() => void onStop()}
                  disabled={isStopping}
                  iconLeft={<StopIcon />}
                >
                  {isStopping ? 'Stopping…' : 'Stop'}
                </Button>
              ) : (
                <Button
                  variant="primary"
                  onClick={() => void onStart()}
                  disabled={isStarting}
                  iconLeft={<PlayIcon />}
                >
                  {isStarting ? 'Starting…' : hunt ? 'Resume' : 'Start search'}
                </Button>
              )}
            </div>
          </div>

          {isBackfilling && hunt !== null && hunt.backfillTotal !== null ? (
            <div className="border-t border-hairline px-6 py-5 sm:px-8 lg:px-10">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-[11px] uppercase tracking-[0.14em] text-ink-muted">
                  Setting up your shortlist
                </span>
                <span className="text-[13px] tabular-nums text-ink">
                  {hunt.backfillDone ?? 0} / {hunt.backfillTotal}
                </span>
              </div>
              <ProgressBar
                value={hunt.backfillDone ?? 0}
                max={hunt.backfillTotal}
                className="mt-3"
                aria-label="Evaluating existing listings"
              />
            </div>
          ) : (
            <div className="grid border-t border-hairline sm:grid-cols-3">
              <Stat label="Listings" value={String(listings.length)} />
              <Stat label="Best fit" value={topScorePct(listings)} />
              <Stat label="Move-in" value={moveInLabel(profile)} note={moveInNote(profile)} />
            </div>
          )}

          {errorMessage ? (
            <div className="border-t border-hairline px-6 py-4 sm:px-8 lg:px-10">
              <p className="rounded-card border border-bad/30 bg-bad/5 px-4 py-3 text-[13px] leading-6 text-bad">
                {errorMessage}
              </p>
            </div>
          ) : null}
        </section>

        <section className="page-frame overflow-hidden">
          <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline px-6 py-5 sm:px-8 lg:px-10">
            <h2 className="text-[30px] font-semibold text-ink">Best matches</h2>
            <ViewToggle value={viewMode} onChange={setViewMode} />
          </div>
          <ListingFilterBar
            value={filters}
            onChange={setFilters}
            newCount={newCount}
            totalCount={listings.length}
            visibleCount={visibleListings.length}
            hiddenCount={hiddenCount}
            lowScoreCount={lowScoreCount}
          />
          {viewMode === 'list' ? (
            <div className="max-h-[820px] overflow-y-auto">
              <ListingList
                listings={visibleListings}
                onOpen={(listing) => setOpenListing(listing)}
                isNew={isNewListing}
                isHidden={isListingHidden}
                onToggleHide={(listing) => toggleHidden(listing.id)}
                emptyLabel={
                  listings.length === 0
                    ? undefined
                    : 'No listings match the current filters. Try loosening them.'
                }
              />
            </div>
          ) : (
            <div className="p-4">
              <ListingMap listings={visibleListings} onOpen={(listing) => setOpenListing(listing)} />
            </div>
          )}
        </section>
        </>
        )}
      </div>
      <ListingDrawer open={openListing !== null} listing={openListing} onClose={() => setOpenListing(null)} />
    </div>
  )
}

function DashboardSkeleton({ variant = 'stats' }: { variant?: 'stats' | 'progress' }) {
  return (
    <>
      <section className="page-frame overflow-hidden animate-pulse" aria-hidden aria-busy>
        <div className="flex flex-col gap-6 px-6 py-8 sm:px-8 lg:flex-row lg:items-start lg:justify-between lg:px-10">
          <div className="min-w-0 flex-1 space-y-4">
            <div className="flex flex-wrap items-center gap-3">
              <div className="h-10 w-64 rounded bg-surface-raised sm:h-14 sm:w-80" />
              <div className="h-6 w-20 rounded-full bg-surface-raised" />
            </div>
            <div className="h-4 w-80 max-w-full rounded bg-surface-raised" />
          </div>
          <div className="shrink-0">
            <div className="h-10 w-36 rounded-full bg-surface-raised" />
          </div>
        </div>
        {variant === 'progress' ? (
          <div className="border-t border-hairline px-6 py-5 sm:px-8 lg:px-10">
            <div className="flex items-baseline justify-between gap-3">
              <div className="h-3 w-40 rounded bg-surface-raised" />
              <div className="h-4 w-14 rounded bg-surface-raised" />
            </div>
            <div className="mt-3 h-1.5 w-full rounded-full bg-surface-raised" />
          </div>
        ) : (
          <div className="grid border-t border-hairline sm:grid-cols-3">
            <StatSkeleton />
            <StatSkeleton />
            <StatSkeleton withNote />
          </div>
        )}
      </section>

      <section className="page-frame overflow-hidden animate-pulse" aria-hidden aria-busy>
        <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline px-6 py-5 sm:px-8 lg:px-10">
          <div className="h-8 w-44 rounded bg-surface-raised" />
          <div className="h-8 w-28 rounded-full bg-surface-raised" />
        </div>
        <div className="flex flex-wrap items-center gap-3 border-b border-hairline px-6 py-4 sm:px-8 lg:px-10">
          <div className="h-8 w-24 rounded-full bg-surface-raised" />
          <div className="h-8 w-40 rounded-full bg-surface-raised" />
          <div className="ml-auto h-4 w-20 rounded bg-surface-raised" />
        </div>
        <ul className="divide-y divide-hairline">
          <ListingRowSkeleton />
          <ListingRowSkeleton />
          <ListingRowSkeleton />
        </ul>
      </section>
    </>
  )
}

function StatSkeleton({ withNote = false }: { withNote?: boolean }) {
  return (
    <div className="border-t border-hairline px-6 py-5 first:border-t-0 sm:border-t-0 sm:border-l first:sm:border-l-0 sm:px-8 lg:px-10">
      <div className="h-3 w-16 rounded bg-surface-raised" />
      <div className="mt-3 h-8 w-24 rounded bg-surface-raised" />
      {withNote ? <div className="mt-3 h-3 w-32 rounded bg-surface-raised" /> : null}
    </div>
  )
}

function ListingRowSkeleton() {
  return (
    <li className="grid w-full gap-4 px-5 py-5 sm:grid-cols-[176px_minmax(0,1fr)]">
      <div className="aspect-[4/3] w-full rounded border border-hairline bg-surface-raised" />
      <div className="min-w-0 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="h-6 w-2/3 rounded bg-surface-raised" />
          <div className="h-6 w-16 shrink-0 rounded-full bg-surface-raised" />
        </div>
        <div className="h-3 w-1/2 rounded bg-surface-raised" />
        <div className="flex gap-6 pt-2">
          <div className="space-y-2">
            <div className="h-3 w-10 rounded bg-surface-raised" />
            <div className="h-4 w-16 rounded bg-surface-raised" />
          </div>
          <div className="space-y-2">
            <div className="h-3 w-10 rounded bg-surface-raised" />
            <div className="h-4 w-14 rounded bg-surface-raised" />
          </div>
          <div className="space-y-2">
            <div className="h-3 w-14 rounded bg-surface-raised" />
            <div className="h-4 w-24 rounded bg-surface-raised" />
          </div>
        </div>
      </div>
    </li>
  )
}

function Stat({
  label,
  value,
  note,
}: {
  label: string
  value: string
  note?: string
}) {
  return (
    <div className="border-t border-hairline px-6 py-5 first:border-t-0 sm:border-t-0 sm:border-l first:sm:border-l-0 sm:px-8 lg:px-10">
      <p className="data-label">{label}</p>
      <p className="mt-2 text-[34px] font-semibold leading-none text-ink">{value}</p>
      {note ? <p className="mt-2 text-[13px] text-ink-muted">{note}</p> : null}
    </div>
  )
}

function ViewToggle({
  value,
  onChange,
}: {
  value: 'list' | 'map'
  onChange: (next: 'list' | 'map') => void
}) {
  return (
    <div role="tablist" aria-label="View mode" className="inline-flex rounded-full bg-surface-raised p-1">
      <ViewToggleButton active={value === 'list'} onClick={() => onChange('list')}>
        List
      </ViewToggleButton>
      <ViewToggleButton active={value === 'map'} onClick={() => onChange('map')}>
        Map
      </ViewToggleButton>
    </div>
  )
}

function ViewToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: string
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      className={clsx(
        'rounded-full px-4 py-1.5 text-[13px] font-medium transition-colors',
        active ? 'bg-surface text-ink shadow-sm' : 'text-ink-muted hover:text-ink',
      )}
    >
      {children}
    </button>
  )
}

function StopIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round">
      <rect x="3.5" y="3.5" width="9" height="9" rx="1.5" />
    </svg>
  )
}

function PlayIcon() {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" fill="currentColor">
      <path d="M5 3.5l8 4.5-8 4.5z" />
    </svg>
  )
}
