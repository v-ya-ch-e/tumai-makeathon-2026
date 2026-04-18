import clsx from 'clsx'
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { Button, Card, WeightSlider } from '../components/ui'
import { ApiError, getSearchProfile, putSearchProfile } from '../lib/api'
import { onboardingSteps } from '../lib/onboarding'
import { useSession } from '../lib/session'
import type { PreferenceWeight, SearchProfile, UpsertSearchProfileBody } from '../types'

type PreferenceItem = {
  key: string
  label: string
  detail: string
}

type PreferenceGroup = {
  id: string
  title: string
  intro: string
  items: PreferenceItem[]
}

const GROUPS: PreferenceGroup[] = [
  {
    id: 'neighbourhood',
    title: 'Neighbourhood',
    intro: 'Use this when the surrounding area changes whether you would actually take the room.',
    items: [
      { key: 'supermarket', label: 'Supermarket nearby', detail: 'Daily errands should stay easy without crossing town.' },
      { key: 'gym', label: 'Gym nearby', detail: 'Useful when training is part of the weekly routine.' },
      { key: 'park', label: 'Park nearby', detail: 'Prioritise quick access to outdoor space.' },
      { key: 'cafe', label: 'Cafe nearby', detail: 'Good for casual work, reading, or meeting people nearby.' },
      { key: 'bars', label: 'Bars nearby', detail: 'Useful if being close to nightlife matters.' },
      { key: 'library', label: 'Library nearby', detail: 'Helpful if quiet study space is part of the brief.' },
      { key: 'coworking', label: 'Coworking nearby', detail: 'Useful when you work away from home regularly.' },
      { key: 'nightlife', label: 'Nightlife nearby', detail: 'A stronger signal than just having a few bars around.' },
      { key: 'green_space', label: 'Green space', detail: 'Useful when the area should feel less built-up.' },
      { key: 'quiet_area', label: 'Quiet neighbourhood', detail: 'Use this when noise should actively count against a listing.' },
    ],
  },
  {
    id: 'place-features',
    title: 'Place features',
    intro: 'Use these when the space itself should noticeably affect which places rise to the top.',
    items: [
      { key: 'furnished', label: 'Furnished', detail: 'Important if you want to move in with minimal setup.' },
      { key: 'balcony', label: 'Balcony', detail: 'Useful if outdoor private space matters.' },
      { key: 'washing_machine', label: 'Washing machine', detail: 'A practical requirement for everyday living.' },
      { key: 'dishwasher', label: 'Dishwasher', detail: 'Useful if shared kitchen effort matters.' },
      { key: 'garden', label: 'Garden', detail: 'A stronger version of wanting outdoor space on the property.' },
      { key: 'elevator', label: 'Elevator', detail: 'Useful when stairs would be a recurring problem.' },
      { key: 'bike_storage', label: 'Bike storage', detail: 'Important if you expect to commute by bike.' },
      { key: 'parking', label: 'Parking', detail: 'Only add this if car access changes the decision.' },
    ],
  },
  {
    id: 'living-style',
    title: 'Living style',
    intro: 'Use these when the people and household feel matter as much as the room itself.',
    items: [
      { key: 'pet_friendly', label: 'Pet-friendly', detail: 'Relevant if pets need to be welcome.' },
      { key: 'non_smoking', label: 'Non-smoking', detail: 'Use this when smoking rules should strongly affect ranking.' },
      { key: 'lgbt_friendly', label: 'LGBT-friendly', detail: 'Prioritise listings that explicitly signal safety and fit.' },
      { key: 'student_household', label: 'Student household', detail: 'Useful if you want a clearly student-oriented flatshare.' },
      { key: 'couples_ok', label: 'Couples OK', detail: 'Only add this if a couple-compatible listing matters.' },
      { key: 'english_speaking', label: 'English-speaking', detail: 'Helpful when language fit should count toward ranking.' },
    ],
  },
]

const DEFAULT_WEIGHT = 3

/*
const PRESETS: Array<{ label: string; detail: string; keys: string[] }> = [
  {
    label: 'Practical setup',
    detail: 'Furnished room, laundry, groceries, and bike storage.',
    keys: ['furnished', 'washing_machine', 'supermarket', 'bike_storage'],
  },
  {
    label: 'Social city life',
    detail: 'Cafe, bars, nightlife, and English-speaking household signals.',
    keys: ['cafe', 'bars', 'nightlife', 'english_speaking'],
  },
  {
    label: 'Quiet and green',
    detail: 'Low-noise area with outdoor space and non-smoking preference.',
    keys: ['quiet_area', 'park', 'green_space', 'non_smoking'],
  },
]
*/

export default function OnboardingPreferences() {
  const navigate = useNavigate()
  const { username, isReady } = useSession()
  const [profile, setProfile] = useState<SearchProfile | null>(null)
  const [selected, setSelected] = useState<Map<string, number>>(new Map())
  const [hydrated, setHydrated] = useState(false)
  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)
  const progressSteps = onboardingSteps({
    canAccessRequirements: Boolean(username),
    canAccessPreferences: Boolean(username),
    canAccessDashboard: hydrated && profile !== null,
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
        if (cancelled) return
        if (searchProfile === null) {
          navigate('/onboarding/requirements', { replace: true })
          return
        }
        setProfile(searchProfile)
        const nextSelected = new Map<string, number>()
        for (const preference of searchProfile.preferences) {
          nextSelected.set(preference.key, clampWeight(preference.weight))
        }
        setSelected(nextSelected)
      } finally {
        if (!cancelled) setHydrated(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username, navigate])

  const selectedPreferences = useMemo(
    () =>
      GROUPS.flatMap((group) =>
        group.items
          .filter((item) => selected.has(item.key))
          .map((item) => ({ ...item, weight: selected.get(item.key) ?? DEFAULT_WEIGHT })),
      ),
    [selected],
  )

  const selectedByGroup = useMemo(
    () =>
      GROUPS.map((group) => ({
        ...group,
        items: group.items
          .filter((item) => selected.has(item.key))
          .map((item) => ({ ...item, weight: selected.get(item.key) ?? DEFAULT_WEIGHT })),
      })).filter((group) => group.items.length > 0),
    [selected],
  )

  const toggle = (key: string) => {
    setSelected((prev) => {
      const next = new Map(prev)
      if (next.has(key)) next.delete(key)
      else next.set(key, DEFAULT_WEIGHT)
      return next
    })
  }

  const setWeight = (key: string, weight: number) => {
    setSelected((prev) => {
      if (!prev.has(key)) return prev
      const next = new Map(prev)
      next.set(key, clampWeight(weight))
      return next
    })
  }

  /*
  const applyPreset = (keys: string[]) => {
    setSelected((prev) => {
      const next = new Map(prev)
      for (const key of keys) {
        if (!next.has(key)) next.set(key, DEFAULT_WEIGHT)
      }
      return next
    })
  }
  */

  const handleNext = async () => {
    setFooter(null)
    if (!username || !profile) return

    const preferences: PreferenceWeight[] = Array.from(selected.entries()).map(([key, weight]) => ({
      key,
      weight: Math.round(clampWeight(weight)),
    }))
    const body: UpsertSearchProfileBody = {
      priceMinEur: profile.priceMinEur,
      priceMaxEur: profile.priceMaxEur,
      mainLocations: profile.mainLocations,
      hasCar: profile.hasCar,
      hasBike: profile.hasBike,
      mode: profile.mode,
      moveInFrom: profile.moveInFrom,
      moveInUntil: profile.moveInUntil,
      preferences,
      rescanIntervalMinutes: profile.rescanIntervalMinutes,
      schedule: profile.schedule,
    }

    setBusy(true)
    try {
      await putSearchProfile(username, body)
      navigate('/dashboard', { state: { autoStart: true } })
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
        step={3}
        eyebrow="Preferences"
        title="Set your preferences"
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
      step={3}
      eyebrow="Preferences"
      title="Set your preferences"
      description="Only choose preferences that should change the order of otherwise similar places. Use the weight slider to decide what matters a little and what matters a lot."
      onBack={() => navigate('/onboarding/requirements')}
      onNext={() => void handleNext()}
      busy={busy}
      nextLabel="Save and view matches"
      footer={footer}
      progressSteps={progressSteps}
      aside={
        <Card className="panel p-6">
          <p className="section-kicker">Selected now</p>
          <p className="mt-4 text-[28px] font-semibold text-ink">{selectedPreferences.length}</p>
          <p className="mt-1 text-[14px] leading-6 text-ink-muted">
            preferences shaping your results.
          </p>
          {selectedPreferences.length > 0 ? (
            <div className="mt-5 space-y-5">
              {selectedByGroup.map((group) => (
                <div key={group.id} className="border-t border-hairline pt-4 first:border-t-0 first:pt-0">
                  <p className="data-label">{group.title}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {group.items.map((item) => (
                      <span
                        key={item.key}
                        className="inline-flex items-center gap-2 rounded-full border border-hairline bg-surface-raised px-3 py-1.5 text-[13px] text-ink"
                      >
                        <span>{item.label}</span>
                        <span className="text-[11px] uppercase tracking-[0.12em] text-ink-muted">
                          {weightShortLabel(item.weight)}
                        </span>
                      </span>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="mt-4 text-[13px] leading-6 text-ink-muted">
              Leaving this empty is valid. Use it only when specific details should move a listing up or down.
            </p>
          )}
        </Card>
      }
    >
      <div className="overflow-hidden rounded-card border border-hairline bg-surface">
        {/* <section className="border-b border-hairline px-5 py-5 md:px-6">
          <div className="grid gap-4 md:grid-cols-[200px_minmax(0,1fr)] md:gap-6">
            <div>
              <h2 className="text-[15px] font-semibold text-ink">Quick starting points</h2>
              <p className="mt-1 text-[13px] leading-6 text-ink-muted">
                Presets add a few related preferences. You can still edit each one after applying them.
              </p>
            </div>
            <div className="space-y-3">
              {PRESETS.map((preset) => (
                <button
                  key={preset.label}
                  type="button"
                  onClick={() => applyPreset(preset.keys)}
                  className="flex w-full items-start justify-between gap-4 rounded border border-hairline bg-surface-raised px-4 py-3 text-left transition-colors hover:border-ink hover:bg-surface"
                >
                  <span>
                    <span className="block text-[14px] font-medium text-ink">{preset.label}</span>
                    <span className="mt-1 block text-[13px] leading-6 text-ink-muted">{preset.detail}</span>
                  </span>
                  <span className="data-label">Apply</span>
                </button>
              ))}
            </div>
          </div>
        </section> */}

        {GROUPS.map((group) => (
          <section key={group.id} className="border-t border-hairline px-5 py-5 md:px-6">
            <div className="grid gap-4 md:grid-cols-[200px_minmax(0,1fr)] md:gap-6">
              <div>
                <h2 className="text-[15px] font-semibold text-ink">{group.title}</h2>
                <p className="mt-1 text-[13px] leading-6 text-ink-muted">{group.intro}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {group.items.map((item) => {
                  const weight = selected.get(item.key)
                  const isSelected = weight !== undefined
                  return (
                    <PreferenceCard
                      key={item.key}
                      item={item}
                      selected={isSelected}
                      weight={weight ?? DEFAULT_WEIGHT}
                      onToggle={() => toggle(item.key)}
                      onWeightChange={(nextWeight) => setWeight(item.key, nextWeight)}
                    />
                  )
                })}
              </div>
            </div>
          </section>
        ))}
      </div>
    </OnboardingShell>
  )
}

function PreferenceCard({
  item,
  selected,
  weight,
  onToggle,
  onWeightChange,
}: {
  item: PreferenceItem
  selected: boolean
  weight: number
  onToggle: () => void
  onWeightChange: (next: number) => void
}) {
  const sliderId = `weight-${item.key}`
  const badge = preferenceBadge(item.label)

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onToggle()
  }

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onToggle}
      onKeyDown={handleKeyDown}
      className={clsx(
        'rounded-card border p-4 transition-colors duration-150 ease-out focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-canvas',
        selected
          ? 'border-accent bg-[#f3e5d6]'
          : 'border-hairline bg-surface-raised hover:border-[#cdbca9] hover:bg-surface',
      )}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex items-start gap-3">
          <span
            className={clsx(
              'flex h-10 w-10 shrink-0 items-center justify-center rounded-full border text-[11px] font-semibold uppercase tracking-[0.16em]',
              selected
                ? 'border-accent bg-surface text-accent'
                : 'border-hairline bg-surface text-ink-muted',
            )}
            aria-hidden
          >
            {badge}
          </span>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-[15px] font-medium text-ink">{item.label}</h3>
              <span
                className={clsx(
                  'rounded-full px-2 py-1 text-[10px] uppercase tracking-[0.16em]',
                  selected ? 'bg-surface text-accent' : 'bg-canvas text-ink-muted',
                )}
              >
                {selected ? weightLabel(weight) : 'Optional'}
              </span>
            </div>
            <p className="mt-2 text-[13px] leading-6 text-ink-muted">{item.detail}</p>
          </div>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={(event) => {
            event.stopPropagation()
            onToggle()
          }}
          className={clsx(selected ? 'border-accent text-accent hover:border-accent' : undefined)}
        >
          {selected ? 'Selected' : 'Add'}
        </Button>
      </div>
      {selected ? (
        <div
          className="mt-4 rounded border border-hairline bg-surface px-3 py-3"
          onClick={(event) => event.stopPropagation()}
        >
          <div className="flex items-center justify-between gap-3">
            <label htmlFor={sliderId} className="data-label">
              Importance
            </label>
            <span className="text-[12px] text-ink-muted">{weightLabel(weight)}</span>
          </div>
          <WeightSlider
            id={sliderId}
            ariaLabel={`Importance of ${item.label}`}
            value={weight}
            onChange={onWeightChange}
            className="mt-3"
          />
        </div>
      ) : (
        <div className="mt-4 flex items-center justify-between border-t border-hairline pt-3">
          <span className="data-label">Ranking impact</span>
          <span className="text-[13px] text-ink-muted">Only used if you add it</span>
        </div>
      )}
    </div>
  )
}

function preferenceBadge(label: string): string {
  const initials = label
    .split(/[\s-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')

  return initials || 'P'
}

function weightShortLabel(weight: number): string {
  if (weight >= 5) return 'Core'
  if (weight >= 4) return 'High'
  if (weight <= 2) return 'Light'
  return 'Mid'
}

function clampWeight(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_WEIGHT
  if (value < 1) return 1
  if (value > 5) return 5
  return value
}

function weightLabel(weight: number): string {
  if (weight >= 5) return 'Must-have'
  if (weight >= 4) return 'Important'
  if (weight <= 2) return 'Nice bonus'
  return 'Useful signal'
}
