import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { PlaceAutocomplete } from '../components/PlaceAutocomplete'
import { Chip, Input } from '../components/ui'
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
      description="Just the essentials: budget, area, and timing."
      onBack={() => navigate('/onboarding/profile')}
      onNext={() => void handleNext()}
      busy={busy}
      footer={footer}
      progressSteps={progressSteps}
    >
      <div className="space-y-6">
        <Field label="Monthly rent (EUR)" error={errors.price}>
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
            placeholder="900"
          />
        </Field>

        <Field
          label="Places that matter"
          error={errors.locations ?? errors.commute}
        >
          <PlaceAutocomplete
            id="req-main-locations"
            value={state.mainLocations}
            onChange={(mainLocations) => {
              setState({ ...state, mainLocations })
              setErrors((prev) => ({ ...prev, locations: undefined, commute: undefined }))
            }}
            placeholder="City, university or address"
          />
        </Field>

        <Field label="How you can travel">
          <div className="flex flex-wrap gap-2">
            <Chip selected={state.hasBike} onToggle={() => setState({ ...state, hasBike: !state.hasBike })}>
              Bike
            </Chip>
            <Chip selected={state.hasCar} onToggle={() => setState({ ...state, hasCar: !state.hasCar })}>
              Car
            </Chip>
            <span
              role="note"
              title="Always considered"
              className="inline-flex min-h-9 cursor-default items-center rounded border border-hairline bg-surface px-3 py-1.5 text-[12px] text-ink-muted"
            >
              Public transport
            </span>
          </div>
        </Field>

        <Field label="What to search">
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
        </Field>

        <div className="grid gap-5 sm:grid-cols-2">
          <Field label="Move-in earliest" error={errors.moveInWindow}>
            <Input
              id="req-move-from"
              type="date"
              value={state.moveInFrom}
              max={state.moveInUntil || undefined}
              onChange={(event) => {
                setState({ ...state, moveInFrom: event.target.value })
                if (errors.moveInWindow) setErrors((prev) => ({ ...prev, moveInWindow: undefined }))
              }}
            />
          </Field>
          <Field label="Move-in latest">
            <Input
              id="req-move-until"
              type="date"
              value={state.moveInUntil}
              min={state.moveInFrom || undefined}
              onChange={(event) => {
                setState({ ...state, moveInUntil: event.target.value })
                if (errors.moveInWindow) setErrors((prev) => ({ ...prev, moveInWindow: undefined }))
              }}
            />
          </Field>
        </div>
      </div>
    </OnboardingShell>
  )
}

function Field({
  label,
  error,
  children,
}: {
  label: ReactNode
  error?: string
  children: ReactNode
}) {
  return (
    <div>
      <p className="mb-1.5 text-[14px] font-medium text-ink">{label}</p>
      {children}
      {error ? <p className="mt-1.5 text-[13px] text-bad">{error}</p> : null}
    </div>
  )
}
