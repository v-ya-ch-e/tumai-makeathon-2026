import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ActionLog } from '../components/ActionLog'
import { AppTabs } from '../components/AppTabs'
import { ConnectWGDialog } from '../components/ConnectWGDialog'
import { ListingDrawer } from '../components/ListingDrawer'
import { ListingList } from '../components/ListingList'
import { Button, Card, StatusPill, type StatusPillTone } from '../components/ui'
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
  if (profile.schedule === 'one_shot') return 'One pass'
  return `Every ${profile.rescanIntervalMinutes} min`
}

function formatMode(profile: SearchProfile): string {
  if (profile.mode === 'both') return 'WG room or flat'
  if (profile.mode === 'flat') return 'Whole flat'
  return 'WG room'
}

function formatBudget(profile: SearchProfile): string {
  return profile.priceMaxEur !== null ? `Up to ${profile.priceMaxEur} EUR` : 'Flexible'
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

  const refreshCredStatus = useCallback(async (name: string) => {
    try {
      setCredStatus(await getCredentialsStatus(name))
    } catch {
      setCredStatus({ connected: false, savedAt: null })
    }
  }, [])

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
      if (!cancelled) applyHunt(nextHunt)
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

  const onCredentialsSaved = async () => {
    setDialogOpen(false)
    if (username) await refreshCredStatus(username)
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

  if (!isReady || profile === null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas font-sans text-[15px] text-ink-muted">
        Loading…
      </div>
    )
  }

  const connected = credStatus?.connected ?? false
  const isActive = uiStatus === 'running' || uiStatus === 'starting'
  const isStopping = uiStatus === 'stopping'
  const isStarting = uiStatus === 'starting'

  return (
    <div className="min-h-screen bg-canvas">
      <div className="app-shell space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-hairline pb-4">
          <div>
            <p className="section-kicker text-accent">WG Hunter</p>
            <p className="mt-1 text-[14px] text-ink-muted">Dashboard and profile</p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            <AppTabs
              current="/dashboard"
              tabs={[
                { label: 'Dashboard', href: '/dashboard' },
                { label: 'Profile', href: '/profile' },
              ]}
            />
            <Button variant="secondary" size="sm" onClick={onLogout}>
              Log out
            </Button>
          </div>
        </div>

        <header className="page-frame overflow-hidden">
          <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
            <div className="border-b border-hairline px-6 py-8 lg:border-b-0 lg:border-r lg:px-8">
              <div className="flex flex-wrap items-center gap-3">
                <p className="section-kicker text-accent">Dashboard</p>
                <StatusPill tone={statusPillTone(uiStatus)}>{statusLabel(uiStatus)}</StatusPill>
              </div>
              <h1 className="page-title mt-4">{username}&apos;s room hunt</h1>
              <p className="body-copy mt-4 max-w-3xl">
                WG Hunter pulls fresh WG-Gesucht listings, checks them against your commute and move-in constraints, and keeps a live log of every pass here.
              </p>

              <dl className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
                <HeaderFact label="Budget" value={formatBudget(profile)} />
                <HeaderFact label="Search" value={formatMode(profile)} />
                <HeaderFact
                  label="Anchors"
                  value={profile.mainLocations.length > 0 ? `${profile.mainLocations.length} places` : 'None yet'}
                />
                <HeaderFact label="Rescan" value={formatSchedule(profile)} />
              </dl>
            </div>

            <div className="px-6 py-6 lg:px-8">
              <div className="space-y-4">
                <ControlRow
                  label="WG login"
                  value={connected ? 'Connected' : 'Not connected'}
                  action={
                    <Button variant="secondary" size="sm" onClick={() => setDialogOpen(true)}>
                      {connected ? 'Manage' : 'Connect'}
                    </Button>
                  }
                />

                <ControlRow
                  label="Agent"
                  value={hunt ? `Hunt ${hunt.id}` : 'Ready to start'}
                  action={
                    <div className="flex flex-wrap justify-end gap-2">
                      {isActive ? (
                        <Button variant="destructive" size="sm" onClick={() => void onStop()} disabled={isStopping}>
                          {isStopping ? 'Stopping…' : 'Stop'}
                        </Button>
                      ) : (
                        <Button
                          variant="primary"
                          size="sm"
                          onClick={() => void (hunt ? onRestart() : onStart())}
                          disabled={isStarting}
                        >
                          {isStarting ? 'Starting…' : hunt ? 'Restart' : 'Start'}
                        </Button>
                      )}
                    </div>
                  }
                />
                {errorMessage ? (
                  <p className="rounded border border-bad/30 bg-bad/5 px-4 py-3 text-[13px] leading-6 text-bad">
                    {errorMessage}
                  </p>
                ) : null}
              </div>
            </div>
          </div>

          <div className="grid border-t border-hairline sm:grid-cols-4">
            <StatStrip label="Listings" value={String(listings.length)} note={summaryCount(listings)} />
            <StatStrip label="Top score" value={topScore(listings)} note="Best current fit" />
            <StatStrip label="Actions" value={String(actions.length)} note="Events captured" />
            <StatStrip
              label="Move-in"
              value={formatDate(profile.moveInFrom)}
              note={profile.moveInUntil ? `Until ${formatDate(profile.moveInUntil)}` : 'Open-ended'}
            />
          </div>
        </header>

        {hunt === null ? (
          <section className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
            <Card className="panel p-8">
              <p className="section-kicker text-accent">Before the first run</p>
              <h2 className="section-title mt-4">The brief is ready.</h2>
              <p className="body-copy mt-4 max-w-2xl">
                Start the agent when you want a new sweep. The dashboard will fill with live crawl events on the right and ranked listings on the left as soon as scoring starts.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <Button variant="primary" onClick={() => void onStart()}>
                  Start hunt
                </Button>
                <Button variant="secondary" onClick={() => setDialogOpen(true)}>
                  {connected ? 'Manage WG login' : 'Connect WG-Gesucht'}
                </Button>
              </div>

              <ol className="mt-8 divide-y divide-hairline border-t border-hairline">
                <LaunchStep
                  number="01"
                  title="Fetch listings"
                  detail="WG Hunter reads search pages and opens each new listing in detail."
                />
                <LaunchStep
                  number="02"
                  title="Score the fit"
                  detail="Budget, commute, move-in timing, preferences, and vibe each contribute to the final ranking."
                />
                <LaunchStep
                  number="03"
                  title="Review the output"
                  detail="Use the list for triage, then open a drawer for the full score breakdown and original post."
                />
              </ol>
            </Card>

            <Card className="panel-muted p-6">
              <p className="section-kicker">Search brief</p>
              <div className="mt-5 space-y-3">
                <BriefRow label="Budget" value={formatBudget(profile)} />
                <BriefRow label="Search" value={formatMode(profile)} />
                <BriefRow label="Rescan" value={formatSchedule(profile)} />
                <BriefRow label="Preferences" value={`${profile.preferences.length} weighted signals`} />
              </div>
              <div className="mt-6 border-t border-hairline pt-4">
                <p className="data-label">Places</p>
                {profile.mainLocations.length > 0 ? (
                  <ul className="mt-3 space-y-2 text-[14px] leading-6 text-ink">
                    {profile.mainLocations.map((location) => (
                      <li key={location.placeId}>
                        {location.label}
                        {location.maxCommuteMinutes !== null ? (
                          <span className="text-ink-muted"> · up to {location.maxCommuteMinutes} min</span>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-3 text-[14px] leading-6 text-ink-muted">No commute anchors saved yet.</p>
                )}
              </div>
            </Card>
          </section>
        ) : (
          <section className="grid gap-8 xl:grid-cols-[minmax(0,1.12fr)_400px]">
            <section className="page-frame overflow-hidden">
              <div className="flex flex-wrap items-end justify-between gap-4 border-b border-hairline px-6 py-5">
                <div>
                  <p className="section-kicker text-accent">Ranked results</p>
                  <h2 className="section-title mt-2">Listings</h2>
                </div>
                <p className="text-[13px] text-ink-muted">
                  {listings.length} collected · {summaryCount(listings)}
                </p>
              </div>
              <div className="max-h-[820px] overflow-y-auto">
                <ListingList listings={listings} onOpen={(listing) => setOpenListing(listing)} />
              </div>
            </section>

            <section className="page-frame overflow-hidden">
              <div className="flex items-end justify-between gap-4 border-b border-hairline px-6 py-5">
                <div>
                  <p className="section-kicker text-accent">Agent activity</p>
                  <h2 className="section-title mt-2">Live log</h2>
                </div>
                <span className="font-mono text-[12px] text-ink-muted">Hunt {hunt.id}</span>
              </div>
              <div className="max-h-[820px] overflow-y-auto px-6 py-2">
                <ActionLog actions={actions} />
              </div>
            </section>
          </section>
        )}

      </div>

      {username ? (
        <ConnectWGDialog
          open={dialogOpen}
          username={username}
          onClose={() => setDialogOpen(false)}
          onSaved={() => void onCredentialsSaved()}
        />
      ) : null}

      <ListingDrawer open={openListing !== null} listing={openListing} onClose={() => setOpenListing(null)} />
    </div>
  )
}

function HeaderFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="data-label">{label}</dt>
      <dd className="mt-1 text-[15px] text-ink">{value}</dd>
    </div>
  )
}

function ControlRow({
  label,
  value,
  action,
}: {
  label: string
  value: string
  action: ReactNode
}) {
  return (
    <div className="rounded border border-hairline bg-surface-raised px-4 py-4">
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="data-label">{label}</p>
          <p className="mt-1 text-[14px] leading-6 text-ink">{value}</p>
        </div>
        <div className="shrink-0">{action}</div>
      </div>
    </div>
  )
}

function StatStrip({
  label,
  value,
  note,
}: {
  label: string
  value: string
  note: string
}) {
  return (
    <div className="border-t border-hairline px-5 py-4 first:border-t-0 sm:border-t-0 sm:border-l first:sm:border-l-0">
      <p className="data-label">{label}</p>
      <p className="mt-2 text-[24px] font-semibold text-ink">{value}</p>
      <p className="mt-1 text-[13px] text-ink-muted">{note}</p>
    </div>
  )
}

function LaunchStep({
  number,
  title,
  detail,
}: {
  number: string
  title: string
  detail: string
}) {
  return (
    <li className="grid gap-3 py-4 md:grid-cols-[52px_minmax(0,1fr)]">
      <span className="font-mono text-[12px] text-ink-muted">{number}</span>
      <div>
        <p className="text-[15px] font-medium text-ink">{title}</p>
        <p className="mt-1 text-[14px] leading-6 text-ink-muted">{detail}</p>
      </div>
    </li>
  )
}

function BriefRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-hairline pt-3 first:border-t-0 first:pt-0">
      <span className="data-label">{label}</span>
      <span className="text-right text-[14px] text-ink">{value}</span>
    </div>
  )
}
