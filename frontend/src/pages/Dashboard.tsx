import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ConnectWGDialog } from '../components/ConnectWGDialog'
import { Button, StatusPill, type StatusPillTone } from '../components/ui'
import {
  ApiError,
  createHunt,
  getCredentialsStatus,
  getHunt,
  getSearchProfile,
  stopHunt,
} from '../lib/api'
import { useSession } from '../lib/session'
import type { CredentialsStatus, Hunt, SearchProfile } from '../types'

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
  const [uiStatus, setUiStatus] = useState<UiStatus>('idle')
  const [credStatus, setCredStatus] = useState<CredentialsStatus | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

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
        setHunt(h)
        setUiStatus(huntToUiStatus(h))
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username, navigate, refreshCredStatus, refreshHunt])

  const onStart = async () => {
    if (!username || !profile) return
    setErrorMessage(null)
    setUiStatus('starting')
    try {
      const h = await createHunt(username, { schedule: profile.schedule })
      localStorage.setItem(LS_HUNT_ID, h.id)
      setHunt(h)
      setUiStatus(huntToUiStatus(h))
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
      setHunt(h)
      setUiStatus(huntToUiStatus(h))
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
        <p className="text-[15px] text-ink-muted">
          {hunt === null
            ? 'Press Start agent to begin scanning wg-gesucht for listings that match your search profile.'
            : `Hunt ${hunt.id} · started ${new Date(hunt.startedAt).toLocaleString()}.`}
        </p>
      </main>

      {username ? (
        <ConnectWGDialog
          open={dialogOpen}
          username={username}
          onClose={() => setDialogOpen(false)}
          onSaved={() => void onCredentialsSaved()}
        />
      ) : null}
    </div>
  )
}
