import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { PlaceAutocomplete } from '../components/PlaceAutocomplete'
import { Card, Chip, Input } from '../components/ui'
import { ApiError, getSearchProfile, putSearchProfile } from '../lib/api'
import { onboardingSteps } from '../lib/onboarding'
import { useSession } from '../lib/session'
import type { Mode, PlaceLocation, UpsertSearchProfileBody } from '../types'

type LocalState = {
  priceMax: string
  mainLocations: PlaceLocation[]
  hasCar: boolean
  hasBike: boolean
  mode: Mode
  moveInFrom: string
  moveInUntil: string
}

type ValidationErrors = {
  price?: string
  locations?: string
  commute?: string
  moveInWindow?: string
}

const DEFAULT_STATE: LocalState = {
  priceMax: '900',
  mainLocations: [],
  hasCar: false,
  hasBike: false,
  mode: 'both',
  moveInFrom: '',
  moveInUntil: '',
}

export default function OnboardingRequirements() {
  const navigate = useNavigate()
  const { username, isReady } = useSession()
  const [state, setState] = useState<LocalState>(DEFAULT_STATE)
  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)
  const [hydrated, setHydrated] = useState(false)
  const [errors, setErrors] = useState<ValidationErrors>({})
  const progressSteps = onboardingSteps({
    canAccessRequirements: Boolean(username),
    canAccessPreferences: hydrated,
    canAccessDashboard: hydrated,
  })

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
        if (cancelled || !searchProfile) {
          setHydrated(true)
          return
        }
        setState({
          priceMax: searchProfile.priceMaxEur !== null ? String(searchProfile.priceMaxEur) : '',
          mainLocations: searchProfile.mainLocations,
          hasCar: searchProfile.hasCar,
          hasBike: searchProfile.hasBike,
          mode: searchProfile.mode,
          moveInFrom: searchProfile.moveInFrom ?? '',
          moveInUntil: searchProfile.moveInUntil ?? '',
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
    if (!state.priceMax) return 'Flexible'
    return `Up to ${state.priceMax} EUR`
  }, [state.priceMax])

  const validate = (): ValidationErrors => {
    const nextErrors: ValidationErrors = {}
    const priceMax = state.priceMax === '' ? null : Number(state.priceMax)

    if (priceMax !== null && (!Number.isFinite(priceMax) || priceMax < 0)) {
      nextErrors.price = 'Maximum rent must be a non-negative number.'
    }

    if (state.mainLocations.length === 0) {
      nextErrors.locations = 'Add at least one city, campus, or district to anchor the search.'
    }

    const invalidLocation = state.mainLocations.find(
      (location) =>
        location.maxCommuteMinutes !== null &&
        (!Number.isInteger(location.maxCommuteMinutes) ||
          location.maxCommuteMinutes < 5 ||
          location.maxCommuteMinutes > 240),
    )
    if (invalidLocation) {
      nextErrors.commute = `Commute for “${invalidLocation.label}” must stay between 5 and 240 minutes, or be left blank.`
    }

    if (state.moveInFrom && state.moveInUntil && state.moveInUntil < state.moveInFrom) {
      nextErrors.moveInWindow = 'Latest move-in date must be on or after the earliest one.'
    }

    return nextErrors
  }

  const handleNext = async () => {
    setFooter(null)
    if (!username) return

    const nextErrors = validate()
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) return

    const body: UpsertSearchProfileBody = {
      priceMinEur: 0,
      priceMaxEur: state.priceMax === '' ? null : Number(state.priceMax),
      mainLocations: state.mainLocations,
      hasCar: state.hasCar,
      hasBike: state.hasBike,
      mode: state.mode,
      moveInFrom: state.moveInFrom || null,
      moveInUntil: state.moveInUntil || null,
      preferences: [],
      rescanIntervalMinutes: 30,
      schedule: 'periodic',
    }

    setBusy(true)
    try {
      await putSearchProfile(username, body)
      navigate('/onboarding/preferences')
    } catch (error) {
      if (error instanceof ApiError) {
        setFooter(<p className="text-[15px] text-bad">{error.message}</p>)
      } else {
        setFooter(<p className="text-[15px] text-bad">{String(error)}</p>)
      }
    } finally {
      setBusy(false)
    }
  }

  if (!isReady || !hydrated) {
    return (
      <OnboardingShell
        step={2}
        eyebrow="Requirements"
        title="Set your requirements"
        onNext={() => undefined}
        busy
        progressSteps={progressSteps}
      >
        <div />
      </OnboardingShell>
    )
  }

  return (
    <OnboardingShell
      step={2}
      eyebrow="Requirements"
      title="Set your requirements"
      description="Start with the essentials: budget, key destinations, and move-in timing. Sherlock Homes keeps checking automatically every 30 minutes."
      onBack={() => navigate('/onboarding/profile')}
      onNext={() => void handleNext()}
      busy={busy}
      footer={footer}
      progressSteps={progressSteps}
      aside={
        <Card className="panel p-6">
          <p className="section-kicker">Current brief</p>
          <div className="mt-5 space-y-3">
            <SummaryRow label="Budget" value={priceSummary} />
            <SummaryRow
              label="Anchors"
              value={state.mainLocations.length > 0 ? `${state.mainLocations.length} places` : 'Not set'}
            />
            <SummaryRow label="Travel" value={mobilitySummary(state)} />
            <SummaryRow label="Listing type" value={modeLabel(state.mode)} />
            <SummaryRow label="Updates" value="Every 30 min" />
          </div>
        </Card>
      }
    >
      <div className="overflow-hidden rounded-card border border-hairline bg-surface">
        <RequirementSection
          title="Monthly rent"
          hint={errors.price ?? 'Set the highest monthly rent you would genuinely consider.'}
          error={Boolean(errors.price)}
        >
          <div className="max-w-sm">
            <Input
              id="req-price-max"
              type="number"
              inputMode="numeric"
              min={0}
              max={5000}
              value={state.priceMax}
              onChange={(event) => {
                setState({ ...state, priceMax: event.target.value })
                if (errors.price) setErrors((prev) => ({ ...prev, price: undefined }))
              }}
              placeholder="Leave blank if flexible"
            />
          </div>
        </RequirementSection>

        <RequirementSection
          title="Places that matter"
          hint={
            errors.locations ??
            errors.commute ??
            'Add the campus, workplace, or district you actually need to reach. Each place can carry its own commute limit.'
          }
          error={Boolean(errors.locations || errors.commute)}
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
            Use the commute field to define your comfort limit. Leave it blank if the place matters, but timing does not.
          </p>
        </RequirementSection>

        <RequirementSection
          title="How you can travel"
          hint="Transit is always considered. Turn on bike or car only if you would genuinely use them in daily travel."
        >
          <div className="flex flex-wrap gap-2">
            <Chip selected={state.hasBike} onToggle={() => setState({ ...state, hasBike: !state.hasBike })}>
              Bike
            </Chip>
            <Chip selected={state.hasCar} onToggle={() => setState({ ...state, hasCar: !state.hasCar })}>
              Car
            </Chip>
          </div>
        </RequirementSection>

        <RequirementSection
          title="What to search"
          hint="Choose what you want to see most often."
        >
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
        </RequirementSection>

        <RequirementSection
          title="Move-in window"
          hint={errors.moveInWindow ?? 'Optional, but useful when timing rules out otherwise good listings.'}
          error={Boolean(errors.moveInWindow)}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="req-move-from" className="data-label">
                Earliest
              </label>
              <Input
                id="req-move-from"
                type="date"
                value={state.moveInFrom}
                max={state.moveInUntil || undefined}
                onChange={(event) => {
                  setState({ ...state, moveInFrom: event.target.value })
                  if (errors.moveInWindow) setErrors((prev) => ({ ...prev, moveInWindow: undefined }))
                }}
                className="mt-2"
              />
            </div>
            <div>
              <label htmlFor="req-move-until" className="data-label">
                Latest
              </label>
              <Input
                id="req-move-until"
                type="date"
                value={state.moveInUntil}
                min={state.moveInFrom || undefined}
                onChange={(event) => {
                  setState({ ...state, moveInUntil: event.target.value })
                  if (errors.moveInWindow) setErrors((prev) => ({ ...prev, moveInWindow: undefined }))
                }}
                className="mt-2"
              />
            </div>
          </div>
        </RequirementSection>

        <RequirementSection
          title="Updates"
          hint="Sherlock Homes keeps your shortlist fresh automatically, so you do not need to configure update timing."
        >
          <div className="rounded-card border border-hairline bg-surface-raised p-4">
            <p className="text-[15px] font-semibold text-ink">Automatic every 30 minutes</p>
            <p className="mt-2 text-[14px] leading-6 text-ink-muted">
              After onboarding, Sherlock Homes keeps checking for new listings in the background. You can pause the search later from the dashboard.
            </p>
          </div>
        </RequirementSection>
      </div>
    </OnboardingShell>
  )
}

function RequirementSection({
  title,
  hint,
  children,
  error = false,
}: {
  title: string
  hint: string
  children: ReactNode
  error?: boolean
}) {
  return (
    <section className="grid gap-4 border-t border-hairline px-5 py-5 first:border-t-0 md:grid-cols-[200px_minmax(0,1fr)] md:gap-6 md:px-6">
      <div>
        <h2 className="text-[15px] font-semibold text-ink">{title}</h2>
        <p className={`mt-1 text-[13px] leading-6 ${error ? 'text-bad' : 'text-ink-muted'}`}>{hint}</p>
      </div>
      <div>{children}</div>
    </section>
  )
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-hairline pt-3 first:border-t-0 first:pt-0">
      <span className="data-label">{label}</span>
      <span className="text-right text-[14px] text-ink">{value}</span>
    </div>
  )
}

function mobilitySummary(state: LocalState): string {
  if (state.hasBike && state.hasCar) return 'Transit, bike, and car'
  if (state.hasBike) return 'Transit and bike'
  if (state.hasCar) return 'Transit and car'
  return 'Transit and walking'
}

function modeLabel(mode: Mode): string {
  if (mode === 'wg') return 'WG room'
  if (mode === 'flat') return 'Whole flat'
  return 'WG room or flat'
}
