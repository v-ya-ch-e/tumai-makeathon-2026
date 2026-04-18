import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ActionLog } from '../components/ActionLog'
import { ConnectWGDialog } from '../components/ConnectWGDialog'
import { ListingDrawer } from '../components/ListingDrawer'
import { ListingList } from '../components/ListingList'
import { Button, StatusPill, type StatusPillTone } from '../components/ui'
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
  if (status === 'starting') return 'Starting…'
  if (status === 'stopping') return 'Stopping…'
  if (status === 'error') return 'Error'
  return 'Idle'
}

function huntToUiStatus(hunt: Hunt | null): UiStatus {
  if (hunt === null) return 'idle'
  if (hunt.status === 'running' || hunt.status === 'pending') return 'running'
  if (hunt.status === 'failed') return 'error'
  return 'idle'
}

export default function Dashboard() {
  const navigate = useNavigate()
  const { username, isReady } = useSession()

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

  const actionKey = (a: Action): string => `${a.at}|${a.kind}|${a.summary}`

  const applyHunt = useCallback((h: Hunt | null) => {
    if (h === null) {
      setHunt(null)
      setActions([])
      setListings([])
      setUiStatus('idle')
      seenActionKeysRef.current = new Set()
      return
    }
    setHunt(h)
    setListings(h.listings)
    setActions(h.actions)
    seenActionKeysRef.current = new Set(h.actions.map(actionKey))
    setUiStatus(huntToUiStatus(h))
  }, [])

  const refreshHunt = useCallback(async (id: string | null): Promise<Hunt | null> => {
    if (!id) return null
    const h = await getHunt(id)
    if (h === null) {
      localStorage.removeItem(LS_HUNT_ID)
      return null
    }
    return h
  }, [])

  useEffect(() => {
    if (!isReady) return
    if (!username) {
      navigate('/onboarding/profile', { replace: true })
      return
    }
    let cancelled = false
    void (async () => {
      const sp = await getSearchProfile(username)
      if (cancelled) return
      if (sp === null) {
        navigate('/onboarding/requirements', { replace: true })
        return
      }
      setProfile(sp)
      await refreshCredStatus(username)
      const storedId = localStorage.getItem(LS_HUNT_ID)
      const h = await refreshHunt(storedId)
      if (!cancelled) {
        applyHunt(h)
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

    closeFn = streamHunt(
      huntId,
      (ev) => {
        if ('kind' in ev && ev.kind === 'stream-end') {
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
        const a = ev as Action
        const key = actionKey(a)
        if (seenActionKeysRef.current.has(key)) return
        seenActionKeysRef.current.add(key)
        setActions((prev) => [...prev, a])
        if (a.kind === 'evaluate' || a.kind === 'new_listing') {
          void (async () => {
            const fresh = await refreshHunt(huntId)
            if (!fresh) return
            setListings(fresh.listings)
            setHunt(fresh)
            setUiStatus(huntToUiStatus(fresh))
          })()
        }
      },
    )

    return () => {
      closed = true
      closeFn?.()
    }
    // Intentionally only depends on hunt?.id so the EventSource stays alive
    // across hunt.status transitions (pending -> running -> done).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hunt?.id])

  const onStart = async () => {
    if (!username || !profile) return
    setErrorMessage(null)
    setUiStatus('starting')
    try {
      const h = await createHunt(username, { schedule: profile.schedule })
      localStorage.setItem(LS_HUNT_ID, h.id)
      applyHunt(h)
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : String(err))
      setUiStatus('error')
    }
  }

  const onStop = async () => {
    if (!hunt) return
    setErrorMessage(null)
    setUiStatus('stopping')
    try {
      const h = await stopHunt(hunt.id)
      applyHunt(h)
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : String(err))
      setUiStatus('error')
    }
  }

  const onCredentialsSaved = async () => {
    setDialogOpen(false)
    if (username) {
      await refreshCredStatus(username)
    }
  }

  if (!isReady || profile === null) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas font-sans text-[15px] text-ink-muted">
        Loading…
      </div>
    )
  }

  const connected = credStatus?.connected ?? false
  const isActive: boolean = uiStatus === 'running' || uiStatus === 'starting'
  const isStopping: boolean = uiStatus === 'stopping'
  const isStarting: boolean = uiStatus === 'starting'

  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-hairline bg-surface">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-6 px-12 py-6">
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
                {isStopping ? 'Stopping…' : 'Stop agent'}
              </Button>
            ) : (
              <Button variant="primary" onClick={() => void onStart()} disabled={isStarting}>
                {isStarting ? 'Starting…' : 'Start agent'}
              </Button>
            )}
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
              <ListingList
                listings={listings}
                onOpen={(l) => setOpenListing(l)}
              />
            </section>
          </div>
        )}
      </main>

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
