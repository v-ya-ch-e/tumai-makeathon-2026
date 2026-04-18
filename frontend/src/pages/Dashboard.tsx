import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ActionLog } from '../components/ActionLog'
<<<<<<< Updated upstream
import { AppTabs } from '../components/AppTabs'
=======
import { AppNav } from '../components/AppNav'
>>>>>>> Stashed changes
import { ConnectWGDialog } from '../components/ConnectWGDialog'
import { ListingDrawer } from '../components/ListingDrawer'
import { ListingList } from '../components/ListingList'
import { Button, Card, Chip, StatusPill, type StatusPillTone } from '../components/ui'
import {
  ApiError,
  createHunt,
  getCredentialsStatus,
  getHunt,
  getSearchProfile,
  stopHunt,
  streamHunt,
} from '../lib/api'
import { useSession } from '../lib/session'
import type { Action, CredentialsStatus, Hunt, Listing, SearchProfile } from '../types'

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
<<<<<<< Updated upstream
  if (status === 'starting') return 'Starting'
  if (status === 'stopping') return 'Stopping'
=======
  if (status === 'starting') return 'StartingвЂ¦'
  if (status === 'stopping') return 'StoppingвЂ¦'
>>>>>>> Stashed changes
  if (status === 'error') return 'Error'
  return 'Idle'
}

function huntToUiStatus(hunt: Hunt | null): UiStatus {
  if (hunt === null) return 'idle'
  if (hunt.status === 'running' || hunt.status === 'pending') return 'running'
  if (hunt.status === 'failed') return 'error'
  return 'idle'
}

function formatDate(value: string | null): string {
  if (!value) return 'Flexible'
  try {
    return new Date(value).toLocaleDateString([], {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  } catch {
    return value
  }
}

function formatSchedule(profile: SearchProfile): string {
  if (profile.schedule === 'one_shot') return 'One-off sweep'
  return `Every ${profile.rescanIntervalMinutes} min`
}

function formatMode(profile: SearchProfile): string {
  if (profile.mode === 'both') return 'WG + Flat'
  if (profile.mode === 'flat') return 'Flat'
  return 'WG room'
}

function formatBudget(profile: SearchProfile): string {
  return profile.priceMaxEur !== null ? `Up to ${profile.priceMaxEur}€` : 'Flexible budget cap'
}

function topScore(listings: Listing[]): string {
  const scored = listings.map((listing) => listing.score).filter((score): score is number => score !== null)
  if (scored.length === 0) return '—'
  return Math.max(...scored).toFixed(2)
}

function summaryCount(listings: Listing[]): string {
  const scored = listings.filter((listing) => listing.score !== null)
  return scored.length > 0 ? `${scored.length} scored` : 'No scores yet'
}

export default function Dashboard() {
  const navigate = useNavigate()
  const location = useLocation()
  const { username, isReady, setUsername } = useSession()

  const [profile, setProfile] = useState<SearchProfile | null>(null)
  const [hunt, setHunt] = useState<Hunt | null>(null)
  const [actions, setActions] = useState<Action[]>([])
  const [listings, setListings] = useState<Listing[]>([])
  const [uiStatus, setUiStatus] = useState<UiStatus>('idle')
  const [credStatus, setCredStatus] = useState<CredentialsStatus | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [openListing, setOpenListing] = useState<Listing | null>(null)
  const seenActionKeysRef = useRef<Set<string>>(new Set())
  const autoStartTriggeredRef = useRef(false)

  const refreshCredStatus = useCallback(
    async (name: string) => {
      try {
        setCredStatus(await getCredentialsStatus(name))
      } catch {
        setCredStatus({ connected: false, savedAt: null })
      }
    },
    [],
  )

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
      const searchProfile = await getSearchProfile(username)
      if (cancelled) return
      if (searchProfile === null) {
        navigate('/onboarding/requirements', { replace: true })
        return
      }
      setProfile(searchProfile)
      await refreshCredStatus(username)
      const storedId = localStorage.getItem(LS_HUNT_ID)
      const nextHunt = await refreshHunt(storedId)
      if (!cancelled) {
        applyHunt(nextHunt)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username, navigate, refreshCredStatus, refreshHunt, applyHunt])

  useEffect(() => {
    const huntId = hunt?.id
    if (!huntId) return
    if (hunt?.status === 'done' || hunt?.status === 'failed') return

    let closed = false
    let closeFn: (() => void) | null = null

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
      const key = actionKey(action)
      if (seenActionKeysRef.current.has(key)) return
      seenActionKeysRef.current.add(key)
      setActions((prev) => [...prev, action])
      if (action.kind === 'evaluate' || action.kind === 'new_listing') {
        void (async () => {
          const fresh = await refreshHunt(huntId)
          if (!fresh) return
          setListings(fresh.listings)
          setHunt(fresh)
          setUiStatus(huntToUiStatus(fresh))
        })()
      }
    })

    return () => {
      closed = true
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

  const onRestart = async () => {
    if (!username || !profile) return
    setErrorMessage(null)
    setUiStatus('starting')
    try {
      if (hunt && (hunt.status === 'running' || hunt.status === 'pending')) {
        await stopHunt(hunt.id)
      }
      const nextHunt = await createHunt(username, { schedule: profile.schedule })
      localStorage.setItem(LS_HUNT_ID, nextHunt.id)
      setOpenListing(null)
      applyHunt(nextHunt)
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : String(error))
      setUiStatus('error')
    }
  }

  const onStartAsNewUser = () => {
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

  const onCredentialsSaved = async () => {
    setDialogOpen(false)
    if (username) {
      await refreshCredStatus(username)
    }
  }

  const briefChips = useMemo(() => {
    if (!profile) return []
    return [
      formatBudget(profile),
      formatMode(profile),
      formatSchedule(profile),
      profile.hasBike ? 'Bike' : null,
      profile.hasCar ? 'Car' : null,
    ].filter((value): value is string => value !== null)
  }, [profile])

  useEffect(() => {
    if (!profile || !username) return
    if (!location.state || typeof location.state !== 'object' || !('autoStart' in location.state)) {
      return
    }
    if ((location.state as { autoStart?: boolean }).autoStart !== true) return
    if (autoStartTriggeredRef.current) return
    autoStartTriggeredRef.current = true
    navigate(location.pathname, { replace: true, state: null })
    if (hunt === null) {
      void onStart()
    }
  }, [location.pathname, location.state, navigate, profile, username, hunt])

  if (!isReady || profile === null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas font-sans text-[15px] text-ink-muted">
        LoadingвЂ¦
      </div>
    )
  }

  const connected = credStatus?.connected ?? false
  const isActive = uiStatus === 'running' || uiStatus === 'starting'
  const isStopping = uiStatus === 'stopping'
  const isStarting = uiStatus === 'starting'
  const hasListings = listings.length > 0
  const progression = [
    { label: 'Brief ready', done: profile !== null },
    { label: 'Hunt running', done: hunt !== null && hunt.status !== 'failed' },
    { label: 'Listings found', done: hasListings },
  ]

  return (
<<<<<<< Updated upstream
    <div className="relative min-h-screen overflow-hidden bg-canvas">
      <div className="relative mx-auto max-w-7xl px-5 py-5 sm:px-8 lg:px-10">
        <section className="overflow-hidden rounded-[34px] border border-hairline/80 bg-surface/95 shadow-[0_30px_80px_rgba(15,23,42,0.08)]">
          <div className="grid gap-6 border-b border-hairline/80 px-6 py-6 lg:grid-cols-[minmax(0,1.3fr)_360px] lg:px-8 xl:px-10">
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <p className="font-mono text-[12px] uppercase tracking-[0.28em] text-accent">
                  Live dashboard
                </p>
                <StatusPill tone={statusPillTone(uiStatus)}>{statusLabel(uiStatus)}</StatusPill>
              </div>
              <h1 className="mt-3 max-w-3xl text-[30px] font-semibold tracking-[-0.035em] text-ink sm:text-[38px]">
                {username}&apos;s live room hunt
              </h1>
              <p className="mt-3 max-w-2xl text-[15px] leading-7 text-ink-muted">
                Launch scans, track agent activity, and review the strongest listings in one place.
              </p>
              <div className="mt-4 flex flex-wrap gap-2">
                {briefChips.map((chip) => (
                  <Chip key={chip} selected onToggle={() => undefined} className="pointer-events-none">
                    {chip}
                  </Chip>
                ))}
              </div>
              <div className="mt-5 grid gap-3 sm:grid-cols-3">
                {progression.map((step, index) => (
                  <div
                    key={step.label}
                    className="rounded-[20px] border border-hairline/80 bg-surface-raised/85 px-4 py-3"
                  >
                    <p className="font-mono text-[11px] uppercase tracking-[0.16em] text-ink-muted">
                      0{index + 1}
                    </p>
                    <p className="mt-2 text-[14px] font-semibold text-ink">{step.label}</p>
                    <p className="mt-1 text-[13px] text-ink-muted">
                      {step.done ? 'Done' : 'Waiting'}
                    </p>
                  </div>
                ))}
              </div>
            </div>

            <Card className="rounded-[28px] bg-surface-raised p-6">
              <div className="flex justify-end">
                <AppTabs
                  current="/dashboard"
                  tabs={[
                    { label: 'Dashboard', href: '/dashboard' },
                    { label: 'Profile', href: '/profile' },
                  ]}
                />
              </div>
              <p className="mt-5 font-mono text-[12px] uppercase tracking-[0.24em] text-accent">
                Hunt controls
              </p>
              <div className="mt-5 space-y-3">
                <div className="flex items-center justify-between rounded-2xl border border-hairline/80 bg-surface-raised/85 px-4 py-3">
                  <div>
                    <p className="text-[13px] uppercase tracking-[0.14em] text-ink-muted">WG login</p>
                    <p className="mt-1 text-[15px] font-semibold text-ink">
                      {connected ? 'Connected' : 'Not connected'}
                    </p>
                  </div>
                  <Button variant="secondary" size="sm" onClick={() => setDialogOpen(true)}>
                    {connected ? 'Manage' : 'Connect'}
                  </Button>
                </div>

                <div className="flex items-center justify-between rounded-2xl border border-hairline/80 bg-surface-raised/85 px-4 py-3">
                  <div>
                    <p className="text-[13px] uppercase tracking-[0.14em] text-ink-muted">Run mode</p>
                    <p className="mt-1 text-[15px] font-semibold text-ink">{formatSchedule(profile)}</p>
                  </div>
                  <div className="flex flex-wrap justify-end gap-2">
                    {hunt ? (
                      <Button variant="secondary" size="sm" onClick={() => void onRestart()} disabled={isStarting || isStopping}>
                        Restart fresh
                      </Button>
                    ) : null}
                    {isActive ? (
                      <Button variant="destructive" onClick={() => void onStop()} disabled={isStopping}>
                        {isStopping ? 'Stopping…' : 'Stop agent'}
                      </Button>
                    ) : (
                      <Button variant="primary" onClick={() => void onStart()} disabled={isStarting}>
                        {isStarting ? 'Starting…' : hunt ? 'Start again' : 'Start agent'}
                      </Button>
                    )}
                  </div>
                </div>

                <div className="flex items-center justify-between rounded-2xl border border-hairline/80 bg-surface-raised/85 px-4 py-3">
                  <div>
                    <p className="text-[13px] uppercase tracking-[0.14em] text-ink-muted">New search</p>
                    <p className="mt-1 text-[15px] font-semibold text-ink">Start as a new user</p>
                  </div>
                  <Button variant="secondary" size="sm" onClick={onStartAsNewUser}>
                    New user
                  </Button>
                </div>
              </div>

              {errorMessage ? (
                <p className="mt-4 rounded-2xl border border-bad/30 bg-bad/5 px-4 py-3 text-[13px] text-bad">
                  {errorMessage}
                </p>
              ) : null}
            </Card>
=======
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-hairline bg-surface">
        <div className="mx-auto max-w-6xl px-12 py-6">
          <div className="flex items-center justify-between gap-6">
            <div className="flex items-center gap-4">
              <h1 className="font-sans text-[22px] font-semibold tracking-tight text-ink">
                {username}'s hunt
              </h1>
              <StatusPill tone={statusPillTone(uiStatus)}>{statusLabel(uiStatus)}</StatusPill>
            </div>
            <div className="flex items-center gap-3">
              <Button variant="secondary" size="sm" onClick={() => setDialogOpen(true)}>
                {connected ? 'wg-gesucht connected' : 'Connect wg-gesucht'}
              </Button>
              {isActive ? (
                <Button variant="destructive" onClick={() => void onStop()} disabled={isStopping}>
                  {isStopping ? 'StoppingвЂ¦' : 'Stop agent'}
                </Button>
              ) : (
                <Button variant="primary" onClick={() => void onStart()} disabled={isStarting}>
                  {isStarting ? 'StartingвЂ¦' : 'Start agent'}
                </Button>
              )}
            </div>
          </div>
          <div className="mt-4">
            <AppNav />
          </div>
        </div>
        {errorMessage ? (
          <div className="mx-auto max-w-6xl px-12 pb-4 text-[13px] text-bad">{errorMessage}</div>
        ) : null}
      </header>

      <main className="mx-auto max-w-6xl px-12 py-12">
        {hunt === null ? (
          <p className="text-[15px] text-ink-muted">
            Press Start agent to begin scanning wg-gesucht for listings that match your search profile.
          </p>
        ) : (
          <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
            <section className="space-y-4">
              <header className="flex items-baseline justify-between border-b border-hairline pb-2">
                <h2 className="text-[15px] font-semibold text-ink">Agent log</h2>
                <p className="font-mono text-[12px] text-ink-muted">Hunt {hunt.id}</p>
              </header>
              <ActionLog actions={actions} />
            </section>
            <section className="space-y-4">
              <header className="flex items-baseline justify-between border-b border-hairline pb-2">
                <h2 className="text-[15px] font-semibold text-ink">Listings</h2>
                <p className="font-mono text-[12px] text-ink-muted">{listings.length} total</p>
              </header>
              <ListingList listings={listings} onOpen={(l) => setOpenListing(l)} />
            </section>
>>>>>>> Stashed changes
          </div>

          <div className="grid gap-4 px-6 py-6 sm:grid-cols-2 xl:grid-cols-4 xl:px-10">
            <MetricCard label="Listings seen" value={String(listings.length)} note={summaryCount(listings)} />
            <MetricCard label="Top score" value={topScore(listings)} note="Best current candidate" />
            <MetricCard label="Action log" value={String(actions.length)} note="Events captured this run" />
            <MetricCard
              label="Move-in target"
              value={formatDate(profile.moveInFrom)}
              note={profile.moveInUntil ? `Until ${formatDate(profile.moveInUntil)}` : 'Open-ended'}
            />
          </div>
        </section>

        <main className="mt-6 grid gap-6 xl:grid-cols-[290px_minmax(0,1fr)]">
          <aside className="space-y-6">
            <Card className="rounded-[28px] bg-surface-raised p-6">
              <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">Search brief</p>
              <div className="mt-5 space-y-4">
                <BriefRow label="Budget" value={formatBudget(profile)} />
                <BriefRow
                  label="Places"
                  value={
                    profile.mainLocations.length > 0
                      ? `${profile.mainLocations.length} commute anchors`
                      : 'No places set'
                  }
                />
                <BriefRow label="Type" value={formatMode(profile)} />
                <BriefRow label="Preferences" value={`${profile.preferences.length} weighted`} />
              </div>
              <div className="mt-5 flex flex-wrap gap-2">
                {profile.mainLocations.slice(0, 4).map((location) => (
                  <Chip key={location.placeId} selected onToggle={() => undefined} className="pointer-events-none">
                    {location.label}
                  </Chip>
                ))}
              </div>
            </Card>

            <Card className="rounded-[28px] p-6">
              <p className="text-[18px] font-semibold tracking-[-0.02em] text-ink">Session status</p>
              <ul className="mt-4 space-y-3 text-[14px] leading-6 text-ink-muted">
                <li>{hunt ? `Current hunt: ${hunt.id}` : 'No hunt started yet.'}</li>
                <li>{hasListings ? `${listings.length} listings currently in view.` : 'Listings will appear here as soon as scoring starts.'}</li>
                <li>{actions.length > 0 ? `${actions.length} logged events so far.` : 'The action log will fill in automatically.'}</li>
              </ul>
            </Card>
          </aside>

          {hunt === null ? (
            <section className="grid gap-6 lg:grid-cols-[minmax(0,1.05fr)_minmax(0,0.95fr)]">
              <Card className="rounded-[30px] bg-surface-raised p-7">
                <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">Ready to launch</p>
                <h2 className="mt-4 text-[30px] font-semibold tracking-[-0.03em] text-ink">
                  Your brief is loaded and ready to scan.
                </h2>
                <p className="mt-4 max-w-xl text-[15px] leading-7 text-ink-muted">
                  Start the agent to fetch listings from wg-gesucht, score them against your profile, and stream the results here.
                </p>
                <div className="mt-6 flex flex-wrap gap-3">
                  <Button variant="primary" onClick={() => void onStart()}>
                    Start agent
                  </Button>
                  <Button variant="secondary" onClick={() => setDialogOpen(true)}>
                    {connected ? 'Manage wg login' : 'Connect wg-gesucht'}
                  </Button>
                </div>
              </Card>

              <Card className="rounded-[30px] bg-surface-raised p-7">
                <p className="text-[18px] font-semibold tracking-[-0.02em] text-ink">What you will see</p>
                <div className="mt-5 space-y-3">
                  {[
                    'A live log of crawls, scoring steps, vetoes, and rescans.',
                    'Ranked listings with score context and quick watchouts.',
                    'A detail drawer with score breakdown, commute times, photos, and the original post.',
                  ].map((item, index) => (
                    <div key={item} className="flex gap-4 rounded-2xl border border-hairline/80 bg-surface px-4 py-4">
                      <span className="font-mono text-[12px] text-accent">0{index + 1}</span>
                      <p className="text-[14px] leading-6 text-ink">{item}</p>
                    </div>
                  ))}
                </div>
              </Card>
            </section>
          ) : (
            <section className="grid gap-6 lg:grid-cols-[minmax(0,1.18fr)_minmax(0,0.82fr)]">
              <Card className="rounded-[30px] p-0">
                <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline/80 px-6 py-5">
                  <div>
                    <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">
                      Ranked results
                    </p>
                    <h2 className="mt-2 text-[22px] font-semibold tracking-[-0.02em] text-ink">
                      Listings
                    </h2>
                  </div>
                  <p className="text-[13px] text-ink-muted">
                    {listings.length} collected · {summaryCount(listings)}
                  </p>
                </div>
                <div className="max-h-[820px] overflow-y-auto px-6 py-5">
                  <ListingList listings={listings} onOpen={(listing) => setOpenListing(listing)} />
                </div>
              </Card>

              <Card className="rounded-[30px] p-0">
                <div className="flex items-center justify-between border-b border-hairline/80 px-6 py-5">
                  <div>
                    <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">
                      Agent activity
                    </p>
                    <h2 className="mt-2 text-[22px] font-semibold tracking-[-0.02em] text-ink">
                      Live log
                    </h2>
                  </div>
                  <span className="font-mono text-[12px] text-ink-muted">Hunt {hunt.id}</span>
                </div>
                <div className="max-h-[820px] overflow-y-auto px-6 py-5">
                  <ActionLog actions={actions} />
                </div>
              </Card>
            </section>
          )}
        </main>
      </div>

      {username ? (
        <ConnectWGDialog
          open={dialogOpen}
          username={username}
          onClose={() => setDialogOpen(false)}
          onSaved={() => void onCredentialsSaved()}
        />
      ) : null}

      <ListingDrawer
        open={openListing !== null}
        listing={openListing}
        onClose={() => setOpenListing(null)}
      />
    </div>
  )
}

function MetricCard({
  label,
  value,
  note,
}: {
  label: string
  value: string
  note: string
}) {
  return (
    <div className="rounded-[24px] border border-hairline/80 bg-surface-raised/90 px-5 py-5 shadow-[0_16px_32px_rgba(39,33,29,0.04)]">
      <p className="text-[12px] uppercase tracking-[0.16em] text-ink-muted">{label}</p>
      <p className="mt-3 text-[28px] font-semibold tracking-[-0.03em] text-ink">{value}</p>
      <p className="mt-1 text-[13px] text-ink-muted">{note}</p>
    </div>
  )
}

function BriefRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-hairline/60 pb-3 last:border-b-0 last:pb-0">
      <span className="text-[13px] uppercase tracking-[0.14em] text-ink-muted">{label}</span>
      <span className="text-right text-[14px] font-medium text-ink">{value}</span>
    </div>
  )
}
