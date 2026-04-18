import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { PlaceAutocomplete } from '../components/PlaceAutocomplete'
import { Chip, Input } from '../components/ui'
import { ApiError, getSearchProfile, putSearchProfile } from '../lib/api'
import { useSession } from '../lib/session'
import type { Mode, PlaceLocation, Schedule, UpsertSearchProfileBody } from '../types'

type LocalState = {
  priceMin: string
  priceMax: string
  mainLocations: PlaceLocation[]
  hasCar: boolean
  hasBike: boolean
  mode: Mode
  moveInFrom: string
  moveInUntil: string
  schedule: Schedule
  rescanIntervalMinutes: string
}

const DEFAULT_STATE: LocalState = {
  priceMin: '400',
  priceMax: '900',
  mainLocations: [],
  hasCar: false,
  hasBike: true,
  mode: 'wg',
  moveInFrom: '',
  moveInUntil: '',
  schedule: 'periodic',
  rescanIntervalMinutes: '30',
}

export default function OnboardingRequirements() {
  const navigate = useNavigate()
  const { username, isReady } = useSession()
  const [state, setState] = useState<LocalState>(DEFAULT_STATE)
  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)
  const [hydrated, setHydrated] = useState(false)

  useEffect(() => {
    if (!isReady) return
    if (!username) {
      navigate('/onboarding/profile', { replace: true })
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const sp = await getSearchProfile(username)
        if (cancelled || !sp) {
          setHydrated(true)
          return
        }
        setState({
          priceMin: String(sp.priceMinEur),
          priceMax: sp.priceMaxEur !== null ? String(sp.priceMaxEur) : '',
          mainLocations: sp.mainLocations,
          hasCar: sp.hasCar,
          hasBike: sp.hasBike,
          mode: sp.mode,
          moveInFrom: sp.moveInFrom ?? '',
          moveInUntil: sp.moveInUntil ?? '',
          schedule: sp.schedule,
          rescanIntervalMinutes: String(sp.rescanIntervalMinutes),
        })
      } finally {
        if (!cancelled) setHydrated(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username, navigate])

  const handleNext = async () => {
    setFooter(null)
    if (!username) return

    const priceMin = Number(state.priceMin)
    const priceMax = state.priceMax === '' ? null : Number(state.priceMax)
    if (!Number.isFinite(priceMin) || priceMin < 0) {
      setFooter(<p className="text-[15px] text-bad">Minimum price must be a non-negative number.</p>)
      return
    }
    if (priceMax !== null && (!Number.isFinite(priceMax) || priceMax < priceMin)) {
      setFooter(
        <p className="text-[15px] text-bad">Maximum price must be a number at least as large as the minimum.</p>,
      )
      return
    }
    const rescan = Number(state.rescanIntervalMinutes)
    if (!Number.isInteger(rescan) || rescan < 5 || rescan > 1440) {
      setFooter(<p className="text-[15px] text-bad">Rescan interval must be between 5 and 1440 minutes.</p>)
      return
    }
    if (state.mainLocations.length === 0) {
      setFooter(<p className="text-[15px] text-bad">Add at least one city, university, or neighbourhood.</p>)
      return
    }

    const body: UpsertSearchProfileBody = {
      priceMinEur: priceMin,
      priceMaxEur: priceMax,
      mainLocations: state.mainLocations,
      hasCar: state.hasCar,
      hasBike: state.hasBike,
      mode: state.mode,
      moveInFrom: state.moveInFrom || null,
      moveInUntil: state.moveInUntil || null,
      preferences: [],
      rescanIntervalMinutes: rescan,
      schedule: state.schedule,
    }

    setBusy(true)
    try {
      await putSearchProfile(username, body)
      navigate('/onboarding/preferences')
    } catch (e) {
      if (e instanceof ApiError) {
        setFooter(<p className="text-[15px] text-bad">{e.message}</p>)
      } else {
        setFooter(<p className="text-[15px] text-bad">{String(e)}</p>)
      }
    } finally {
      setBusy(false)
    }
  }

  if (!isReady || !hydrated) {
    return (
      <OnboardingShell step={2} title="What are you looking for?" onNext={() => undefined} busy>
        <div />
      </OnboardingShell>
    )
  }

  return (
    <OnboardingShell
      step={2}
      title="What are you looking for?"
      description="Rough strokes. You can always refine these later from the dashboard."
      onBack={() => navigate('/onboarding/profile')}
      onNext={() => void handleNext()}
      busy={busy}
      footer={footer}
    >
      <div className="space-y-8">
        <div className="space-y-3">
          <span className="block text-[15px] text-ink">Monthly rent (€)</span>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label htmlFor="req-price-min" className="block text-[13px] text-ink-muted">
                Minimum
              </label>
              <Input
                id="req-price-min"
                type="number"
                inputMode="numeric"
                min={0}
                max={5000}
                value={state.priceMin}
                onChange={(e) => setState({ ...state, priceMin: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="req-price-max" className="block text-[13px] text-ink-muted">
                Maximum
              </label>
              <Input
                id="req-price-max"
                type="number"
                inputMode="numeric"
                min={0}
                max={5000}
                value={state.priceMax}
                onChange={(e) => setState({ ...state, priceMax: e.target.value })}
              />
            </div>
          </div>
        </div>

        <div className="space-y-2">
          <label htmlFor="req-main-locations" className="block text-[15px] text-ink">
            Main locations
          </label>
          <PlaceAutocomplete
            id="req-main-locations"
            value={state.mainLocations}
            onChange={(mainLocations) => setState({ ...state, mainLocations })}
          />
          <p className="text-[13px] text-ink-muted">
            Pick cities, universities, or addresses. These drive how we score listings by location.
          </p>
        </div>

        <div className="space-y-3">
          <span className="block text-[15px] text-ink">Mobility</span>
          <div className="flex flex-wrap gap-2">
            <Chip selected={state.hasBike} onToggle={() => setState({ ...state, hasBike: !state.hasBike })}>
              Bike
            </Chip>
            <Chip selected={state.hasCar} onToggle={() => setState({ ...state, hasCar: !state.hasCar })}>
              Car
            </Chip>
          </div>
        </div>

        <div className="space-y-3">
          <span className="block text-[15px] text-ink">Mode</span>
          <div className="flex flex-wrap gap-2">
            <Chip selected={state.mode === 'wg'} onToggle={() => setState({ ...state, mode: 'wg' })}>
              WG room
            </Chip>
            <Chip selected={state.mode === 'flat'} onToggle={() => setState({ ...state, mode: 'flat' })}>
              Whole flat
            </Chip>
            <Chip selected={state.mode === 'both'} onToggle={() => setState({ ...state, mode: 'both' })}>
              Either
            </Chip>
          </div>
        </div>

        <div className="space-y-3">
          <span className="block text-[15px] text-ink">Move-in window</span>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <label htmlFor="req-move-from" className="block text-[13px] text-ink-muted">
                Earliest
              </label>
              <Input
                id="req-move-from"
                type="date"
                value={state.moveInFrom}
                onChange={(e) => setState({ ...state, moveInFrom: e.target.value })}
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="req-move-until" className="block text-[13px] text-ink-muted">
                Latest
              </label>
              <Input
                id="req-move-until"
                type="date"
                value={state.moveInUntil}
                onChange={(e) => setState({ ...state, moveInUntil: e.target.value })}
              />
            </div>
          </div>
        </div>

        <div className="space-y-3">
          <span className="block text-[15px] text-ink">How should the agent run?</span>
          <div className="flex flex-wrap gap-2">
            <Chip
              selected={state.schedule === 'one_shot'}
              onToggle={() => setState({ ...state, schedule: 'one_shot' })}
            >
              One-off sweep
            </Chip>
            <Chip
              selected={state.schedule === 'periodic'}
              onToggle={() => setState({ ...state, schedule: 'periodic' })}
            >
              Keep rescanning
            </Chip>
          </div>
          {state.schedule === 'periodic' ? (
            <div className="mt-2 grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <label htmlFor="req-rescan" className="block text-[13px] text-ink-muted">
                  Rescan every (minutes)
                </label>
                <Input
                  id="req-rescan"
                  type="number"
                  inputMode="numeric"
                  min={5}
                  max={1440}
                  value={state.rescanIntervalMinutes}
                  onChange={(e) => setState({ ...state, rescanIntervalMinutes: e.target.value })}
                />
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </OnboardingShell>
  )
}
