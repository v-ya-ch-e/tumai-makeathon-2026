import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { ActionLog } from '../components/ActionLog'
import { AppTabs } from '../components/AppTabs'
import { ConnectWGDialog } from '../components/ConnectWGDialog'
import { ListingDrawer } from '../components/ListingDrawer'
import { ListingList } from '../components/ListingList'
import { Button, Card, StatusPill, type StatusPillTone } from '../components/ui'
import {
  ApiError,
  getAgentStatus,
  getCredentialsStatus,
  getSearchProfile,
  getUserActions,
  getUserListings,
  pauseAgent,
  startAgent,
  streamUser,
} from '../lib/api'
import { useSession } from '../lib/session'
import type { Action, CredentialsStatus, Listing, SearchProfile } from '../types'

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
  if (status === 'stopping') return 'Pausing'
  if (status === 'error') return 'Error'
  return 'Idle'
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
  const { username, isReady, setUsername } = useSession()

  const [profile, setProfile] = useState<SearchProfile | null>(null)
  const [agentRunning, setAgentRunning] = useState(false)
  const [actions, setActions] = useState<Action[]>([])
  const [listings, setListings] = useState<Listing[]>([])
  const [uiStatus, setUiStatus] = useState<UiStatus>('idle')
  const [credStatus, setCredStatus] = useState<CredentialsStatus | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [openListing, setOpenListing] = useState<Listing | null>(null)
  const seenActionKeysRef = useRef<Set<string>>(new Set())

  const refreshCredStatus = useCallback(async (name: string) => {
    try {
      setCredStatus(await getCredentialsStatus(name))
    } catch {
      setCredStatus({ connected: false, savedAt: null })
    }
  }, [])

  const actionKey = (action: Action): string => `${action.at}|${action.kind}|${action.summary}`

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
      try {
        const [userListings, userActions, agentStatus] = await Promise.all([
          getUserListings(username),
          getUserActions(username),
          getAgentStatus(username),
        ])
        if (cancelled) return
        setListings(userListings)
        setActions(userActions)
        seenActionKeysRef.current = new Set(userActions.map(actionKey))
        setAgentRunning(agentStatus.running)
        setUiStatus(agentStatus.running ? 'running' : 'idle')
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error instanceof ApiError ? error.message : String(error))
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username, navigate, refreshCredStatus])

  useEffect(() => {
    if (!username || !profile) return

    let closed = false
    const close = streamUser(username, (action) => {
      if (closed) return
      const key = actionKey(action)
      if (seenActionKeysRef.current.has(key)) return
      seenActionKeysRef.current.add(key)
      setActions((prev) => [...prev, action])
      if (action.kind === 'evaluate' || action.kind === 'new_listing') {
        void (async () => {
          try {
            const fresh = await getUserListings(username)
            if (!closed) setListings(fresh)
          } catch {
            // Ignore transient refresh errors; next event will retry.
          }
        })()
      }
    })

    return () => {
      closed = true
      close()
    }
  }, [username, profile])

  const onStart = async () => {
    if (!username) return
    setErrorMessage(null)
    setUiStatus('starting')
    try {
      await startAgent(username)
      setAgentRunning(true)
      setUiStatus('running')
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : String(error))
      setUiStatus('error')
    }
  }

  const onPause = async () => {
    if (!username) return
    setErrorMessage(null)
    setUiStatus('stopping')
    try {
      await pauseAgent(username)
      setAgentRunning(false)
      setUiStatus('idle')
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : String(error))
      setUiStatus('error')
    }
  }

  const onLogout = () => {
    setOpenListing(null)
    setUsername(null)
    navigate('/onboarding/profile', { replace: true })
  }

  const onCredentialsSaved = async () => {
    setDialogOpen(false)
    if (username) await refreshCredStatus(username)
  }

  if (!isReady || profile === null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas font-sans text-[15px] text-ink-muted">
        Loading…
      </div>
    )
  }

  const connected = credStatus?.connected ?? false
  const isStarting = uiStatus === 'starting'
  const isStopping = uiStatus === 'stopping'
  const isEmpty = listings.length === 0 && actions.length === 0

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
                { label: 'Timeline', href: '/timeline' },
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
                  label="Background agent"
                  value={agentRunning ? 'Running' : 'Paused'}
                  action={
                    <div className="flex flex-wrap justify-end gap-2">
                      {agentRunning ? (
                        <Button variant="destructive" size="sm" onClick={() => void onPause()} disabled={isStopping}>
                          {isStopping ? 'Pausing…' : 'Pause'}
                        </Button>
                      ) : (
                        <Button variant="primary" size="sm" onClick={() => void onStart()} disabled={isStarting}>
                          {isStarting ? 'Starting…' : 'Resume'}
                        </Button>
                      )}
                    </div>
                  }
                />

                <ControlRow
                  label="Reset"
                  value="Clear the current local profile and start as a new user."
                  action={
                    <Button variant="secondary" size="sm" onClick={onLogout}>
                      New user
                    </Button>
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

        {isEmpty ? (
          <section className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
            <Card className="panel p-8">
              <p className="section-kicker text-accent">Before the first run</p>
              <h2 className="section-title mt-4">The brief is ready.</h2>
              <p className="body-copy mt-4 max-w-2xl">
                Start the background agent when you want a new sweep. The dashboard will fill with live crawl events on the right and ranked listings on the left as soon as scoring starts.
              </p>
              <div className="mt-6 flex flex-wrap gap-3">
                <Button variant="primary" onClick={() => void onStart()} disabled={isStarting || agentRunning}>
                  {agentRunning ? 'Agent running' : isStarting ? 'Starting…' : 'Start background agent'}
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
                <span className="font-mono text-[12px] text-ink-muted">Background agent</span>
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
