import clsx from 'clsx'
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { PlaceAutocomplete } from '../components/PlaceAutocomplete'
import { Card, Chip, Input } from '../components/ui'
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

type ValidationErrors = {
  price?: string
  locations?: string
  commute?: string
  rescanInterval?: string
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

const BUDGET_PRESETS = [
  { label: 'Lean', min: '350', max: '700' },
  { label: 'Balanced', min: '500', max: '950' },
  { label: 'Flexible', min: '700', max: '1400' },
]

export default function OnboardingRequirements() {
  const navigate = useNavigate()
  const { username, isReady } = useSession()
  const [state, setState] = useState<LocalState>(DEFAULT_STATE)
  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)
  const [hydrated, setHydrated] = useState(false)
  const [errors, setErrors] = useState<ValidationErrors>({})

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

  const priceSummary = useMemo(() => {
    const min = state.priceMin || '0'
    const max = state.priceMax || 'flexible'
    return `${min}€ – ${max === 'flexible' ? max : `${max}€`}`
  }, [state.priceMin, state.priceMax])

  const validate = (): ValidationErrors => {
    const nextErrors: ValidationErrors = {}
    const priceMin = Number(state.priceMin)
    const priceMax = state.priceMax === '' ? null : Number(state.priceMax)

    if (!Number.isFinite(priceMin) || priceMin < 0) {
      nextErrors.price = 'Minimum price must be a non-negative number.'
    } else if (priceMax !== null && (!Number.isFinite(priceMax) || priceMax < priceMin)) {
      nextErrors.price = 'Maximum price must be at least as large as the minimum.'
    }

    const rescan = Number(state.rescanIntervalMinutes)
    if (state.schedule === 'periodic' && (!Number.isInteger(rescan) || rescan < 5 || rescan > 1440)) {
      nextErrors.rescanInterval = 'Rescan interval must be between 5 and 1440 minutes.'
    }

    if (state.mainLocations.length === 0) {
      nextErrors.locations = 'Add at least one city, university, or neighbourhood.'
    }

    const bad = state.mainLocations.find(
      (location) =>
        location.maxCommuteMinutes !== null &&
        (!Number.isInteger(location.maxCommuteMinutes) ||
          location.maxCommuteMinutes < 5 ||
          location.maxCommuteMinutes > 240),
    )
    if (bad) {
      nextErrors.commute = `Ideal commute for “${bad.label}” must stay between 5 and 240 minutes, or be left blank.`
    }

    return nextErrors
  }

  const handleNext = async () => {
    setFooter(null)
    if (!username) return

    const nextErrors = validate()
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) {
      return
    }

    const body: UpsertSearchProfileBody = {
      priceMinEur: Number(state.priceMin),
      priceMaxEur: state.priceMax === '' ? null : Number(state.priceMax),
      mainLocations: state.mainLocations,
      hasCar: state.hasCar,
      hasBike: state.hasBike,
      mode: state.mode,
      moveInFrom: state.moveInFrom || null,
      moveInUntil: state.moveInUntil || null,
      preferences: [],
      rescanIntervalMinutes: Number(state.rescanIntervalMinutes),
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
      <OnboardingShell
        step={2}
        eyebrow="Search brief"
        title="What are you looking for?"
        onNext={() => undefined}
        busy
      >
        <div />
      </OnboardingShell>
    )
  }

  return (
    <OnboardingShell
      step={2}
      eyebrow="Search brief"
      title="Shape the hunt around your real life"
      description="Set your budget, commute anchors, and search behavior. We use this brief to filter and rank listings before they ever hit the dashboard."
      onBack={() => navigate('/onboarding/profile')}
      onNext={() => void handleNext()}
      busy={busy}
      footer={footer}
      aside={
        <div className="space-y-4">
          <Card className="rounded-[28px] border-hairline/80 bg-surface/92 p-6">
            <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">Search summary</p>
            <SummaryItem label="Budget" value={priceSummary} />
            <SummaryItem label="Places" value={state.mainLocations.length > 0 ? `${state.mainLocations.length} added` : 'Not set'} />
            <SummaryItem label="Mobility" value={mobilitySummary(state)} />
            <SummaryItem label="Mode" value={modeLabel(state.mode)} />
            <SummaryItem label="Cadence" value={state.schedule === 'periodic' ? `Every ${state.rescanIntervalMinutes || '30'} min` : 'One-off sweep'} />
          </Card>
          <Card className="rounded-[28px] border-hairline/80 bg-surface/92 p-6">
            <p className="text-[14px] font-semibold text-ink">What makes this strong</p>
            <ul className="mt-3 space-y-3 text-[14px] leading-6 text-ink-muted">
              <li>At least one place with a commute target.</li>
              <li>A realistic max rent, not just a dream number.</li>
              <li>The run mode that matches how urgently you are searching.</li>
            </ul>
          </Card>
        </div>
      }
    >
      <div className="space-y-6">
        <SectionCard
          title="Budget"
          hint={errors.price ?? 'Give the agent a workable monthly range so it can veto obviously bad fits fast.'}
          tone={errors.price ? 'bad' : 'default'}
        >
          <div className="flex flex-wrap gap-2">
            {BUDGET_PRESETS.map((preset) => (
              <button
                key={preset.label}
                type="button"
                onClick={() => {
                  setState((prev) => ({ ...prev, priceMin: preset.min, priceMax: preset.max }))
                  setErrors((prev) => ({ ...prev, price: undefined }))
                }}
                className="rounded-full border border-hairline bg-surface px-3 py-1.5 text-[13px] text-ink transition-colors hover:bg-surface-raised"
              >
                {preset.label}
              </button>
            ))}
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <label htmlFor="req-price-min" className="block text-[13px] text-ink-muted">
                Minimum monthly rent
              </label>
              <Input
                id="req-price-min"
                type="number"
                inputMode="numeric"
                min={0}
                max={5000}
                value={state.priceMin}
                onChange={(e) => {
                  setState({ ...state, priceMin: e.target.value })
                  if (errors.price) setErrors((prev) => ({ ...prev, price: undefined }))
                }}
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="req-price-max" className="block text-[13px] text-ink-muted">
                Maximum monthly rent
              </label>
              <Input
                id="req-price-max"
                type="number"
                inputMode="numeric"
                min={0}
                max={5000}
                value={state.priceMax}
                onChange={(e) => {
                  setState({ ...state, priceMax: e.target.value })
                  if (errors.price) setErrors((prev) => ({ ...prev, price: undefined }))
                }}
                placeholder="Leave blank if flexible"
              />
            </div>
          </div>
        </SectionCard>

        <SectionCard
          title="Commute anchors"
          hint={errors.locations ?? errors.commute ?? 'Choose the places that matter most: campus, office, neighborhood, or even a friend\'s area.'}
          tone={errors.locations || errors.commute ? 'bad' : 'default'}
        >
          <PlaceAutocomplete
            id="req-main-locations"
            value={state.mainLocations}
            onChange={(mainLocations) => {
              setState({ ...state, mainLocations })
              setErrors((prev) => ({ ...prev, locations: undefined, commute: undefined }))
            }}
          />
          <p className="mt-3 text-[13px] leading-6 text-ink-muted">
            Add an ideal commute time to each location. Listings that overshoot your limit get penalized automatically.
          </p>
        </SectionCard>

        <div className="grid gap-6 lg:grid-cols-2">
          <SectionCard title="Mobility" hint="Turn on every transport mode you realistically use." compact>
            <div className="flex flex-wrap gap-2">
              <Chip selected={state.hasBike} onToggle={() => setState({ ...state, hasBike: !state.hasBike })}>
                Bike
              </Chip>
              <Chip selected={state.hasCar} onToggle={() => setState({ ...state, hasCar: !state.hasCar })}>
                Car
              </Chip>
            </div>
          </SectionCard>

          <SectionCard title="Listing type" hint="Keep the search focused on the kind of place you would actually take." compact>
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
          </SectionCard>
        </div>

        <SectionCard title="Move-in window" hint="Optional, but helpful if your timing is non-negotiable." compact>
          <div className="grid gap-4 sm:grid-cols-2">
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
        </SectionCard>

        <SectionCard
          title="Run behavior"
          hint={errors.rescanInterval ?? 'Choose whether the agent should do one sweep or keep scanning in the background.'}
          tone={errors.rescanInterval ? 'bad' : 'default'}
        >
          <div className="flex flex-wrap gap-2">
            <Chip
              selected={state.schedule === 'one_shot'}
              onToggle={() => {
                setState({ ...state, schedule: 'one_shot' })
                setErrors((prev) => ({ ...prev, rescanInterval: undefined }))
              }}
            >
              One-off sweep
            </Chip>
            <Chip
              selected={state.schedule === 'periodic'}
              onToggle={() => {
                setState({ ...state, schedule: 'periodic' })
                setErrors((prev) => ({ ...prev, rescanInterval: undefined }))
              }}
            >
              Keep rescanning
            </Chip>
          </div>
          {state.schedule === 'periodic' ? (
            <div className="mt-4 max-w-xs space-y-2">
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
                onChange={(e) => {
                  setState({ ...state, rescanIntervalMinutes: e.target.value })
                  if (errors.rescanInterval) {
                    setErrors((prev) => ({ ...prev, rescanInterval: undefined }))
                  }
                }}
              />
            </div>
          ) : null}
        </SectionCard>
      </div>
    </OnboardingShell>
  )
}

function SectionCard({
  title,
  hint,
  children,
  compact = false,
  tone = 'default',
}: {
  title: string
  hint: string
  children: ReactNode
  compact?: boolean
  tone?: 'default' | 'bad'
}) {
  return (
    <Card
      className={clsx(
        'rounded-[28px] border-hairline/80 bg-surface-raised/85',
        compact ? 'p-5' : 'p-6',
        tone === 'bad' && 'border-bad/40 bg-bad/5',
      )}
    >
      <p className="text-[18px] font-semibold tracking-[-0.02em] text-ink">{title}</p>
      <p className="mt-2 text-[13px] leading-6 text-ink-muted">{hint}</p>
      <div className="mt-4">{children}</div>
    </Card>
  )
}

function SummaryItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="mt-4 rounded-2xl border border-hairline/80 bg-surface-raised px-4 py-3">
      <p className="text-[11px] uppercase tracking-[0.2em] text-ink-muted">{label}</p>
      <p className="mt-1 text-[15px] font-semibold text-ink">{value}</p>
    </div>
  )
}

function mobilitySummary(state: LocalState): string {
  if (state.hasBike && state.hasCar) return 'Bike + car'
  if (state.hasBike) return 'Bike'
  if (state.hasCar) return 'Car'
  return 'Transit / walking'
}

function modeLabel(mode: Mode): string {
  if (mode === 'wg') return 'WG room'
  if (mode === 'flat') return 'Whole flat'
  return 'WG or flat'
}
