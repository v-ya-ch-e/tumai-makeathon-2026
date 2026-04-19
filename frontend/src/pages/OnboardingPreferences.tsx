import clsx from 'clsx'
import { useEffect, useState, type KeyboardEvent, type ReactNode, type SVGProps } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { WeightSlider } from '../components/ui'
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
  items: PreferenceItem[]
}

const GROUPS: PreferenceGroup[] = [
  {
    id: 'neighbourhood',
    title: 'Neighbourhood',
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

const ICONS: Record<string, (props: SVGProps<SVGSVGElement>) => ReactNode> = {
  supermarket: StoreIcon,
  gym: DumbbellIcon,
  park: LeafIcon,
  cafe: CupIcon,
  bars: GlassIcon,
  library: BookIcon,
  coworking: DeskIcon,
  nightlife: MoonIcon,
  green_space: LeafIcon,
  quiet_area: QuietIcon,
  furnished: SofaIcon,
  balcony: BalconyIcon,
  washing_machine: WasherIcon,
  dishwasher: PlateIcon,
  garden: FlowerIcon,
  elevator: LiftIcon,
  bike_storage: BikeIcon,
  parking: ParkingIcon,
  pet_friendly: PawIcon,
  non_smoking: SmokeFreeIcon,
  lgbt_friendly: SparkIcon,
  student_household: CapIcon,
  couples_ok: HeartIcon,
  english_speaking: ChatIcon,
}

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
      rescanIntervalMinutes: 30,
      schedule: 'periodic',
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
      description="Optional. Pick what should nudge results to the top."
      onBack={() => navigate('/onboarding/requirements')}
      onNext={() => void handleNext()}
      busy={busy}
      nextLabel="Save and view matches"
      footer={footer}
      progressSteps={progressSteps}
    >
      <div className="space-y-8">
        {GROUPS.map((group) => (
          <section key={group.id}>
            <h2 className="text-[22px] font-semibold text-ink">{group.title}</h2>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
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
  const Icon = ICONS[item.key] ?? SparkIcon

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
        'rounded-card border px-4 py-3 transition-colors duration-150 ease-out focus:outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-canvas',
        selected
          ? 'border-accent bg-accent-muted'
          : 'border-hairline bg-surface hover:border-ink/30',
      )}
    >
      <div className="flex min-w-0 items-center gap-3">
        <span
          className={clsx(
            'flex h-10 w-10 shrink-0 items-center justify-center rounded-full',
            selected ? 'bg-surface text-accent' : 'bg-surface-raised text-ink-muted',
          )}
          aria-hidden
        >
          <Icon className="h-5 w-5" />
        </span>
        <span className="min-w-0 truncate text-[15px] font-medium text-ink">
          {item.label}
        </span>
      </div>
      {selected ? (
        <div
          className="mt-3 rounded border border-hairline bg-surface px-3 py-3"
          onClick={(event) => event.stopPropagation()}
          onMouseDown={(event) => event.stopPropagation()}
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
            className="mt-2"
          />
        </div>
      ) : null}
    </div>
  )
}

function iconProps(props: SVGProps<SVGSVGElement>) {
  return {
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.7,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    ...props,
  }
}

function StoreIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M4 10h16" />
      <path d="M6 10V7.5L8 5h8l2 2.5V10" />
      <path d="M6 10v8h12v-8" />
      <path d="M9 18v-4h6v4" />
    </svg>
  )
}

function DumbbellIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M7 10v4" />
      <path d="M17 10v4" />
      <path d="M5 9v6" />
      <path d="M19 9v6" />
      <path d="M7 12h10" />
    </svg>
  )
}

function LeafIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M19 5c-6 0-10 4-10 10 0 2 1 4 3 4 6 0 10-4 10-10 0-2-1-4-3-4Z" />
      <path d="M8 16c3-2 5-4 8-8" />
    </svg>
  )
}

function CupIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M6 9h9v3a4 4 0 0 1-4 4H9a3 3 0 0 1-3-3V9Z" />
      <path d="M15 10h2a2 2 0 1 1 0 4h-1" />
      <path d="M5 19h11" />
    </svg>
  )
}

function GlassIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M7 5h10l-4 5v5l2 4H9l2-4v-5L7 5Z" />
    </svg>
  )
}

function BookIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M6 6.5A2.5 2.5 0 0 1 8.5 4H18v15H8.5A2.5 2.5 0 0 0 6 21V6.5Z" />
      <path d="M6 7h12" />
    </svg>
  )
}

function DeskIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M4 12h16" />
      <path d="M6 12V8h12v4" />
      <path d="M8 12v6" />
      <path d="M16 12v6" />
    </svg>
  )
}

function MoonIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M15 4a7 7 0 1 0 5 12 8 8 0 1 1-5-12Z" />
    </svg>
  )
}

function QuietIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M7 9v6" />
      <path d="M10 7v10" />
      <path d="M14 10v4" />
      <path d="M18 8c1.5 1.2 2 3.6 0 6" />
      <path d="M5 19h14" />
    </svg>
  )
}

function SofaIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M5 12a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v4H5v-4Z" />
      <path d="M7 10V8a2 2 0 0 1 2-2h2" />
      <path d="M17 10V8a2 2 0 0 0-2-2h-2" />
      <path d="M6 16v2" />
      <path d="M18 16v2" />
    </svg>
  )
}

function BalconyIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M6 5h12v6H6z" />
      <path d="M4 13h16" />
      <path d="M7 13v6" />
      <path d="M17 13v6" />
    </svg>
  )
}

function WasherIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <rect x="5" y="4" width="14" height="16" rx="2" />
      <circle cx="12" cy="13" r="3.5" />
      <path d="M8 7h.01" />
      <path d="M11 7h5" />
    </svg>
  )
}

function PlateIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="12" cy="12" r="5" />
      <circle cx="12" cy="12" r="2" />
      <path d="M5 5l14 14" />
    </svg>
  )
}

function FlowerIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="12" cy="12" r="2" />
      <path d="M12 7c1.2-2 4-2 4 0s-2 3-4 3" />
      <path d="M17 12c2-1.2 4 1.4 2.5 3.2S16 15.5 15 14" />
      <path d="M12 17c-1.2 2-4 2-4 0s2-3 4-3" />
      <path d="M7 12c-2 1.2-4-1.4-2.5-3.2S8 8.5 9 10" />
    </svg>
  )
}

function LiftIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <rect x="7" y="4" width="10" height="16" rx="2" />
      <path d="M10 8l2-2 2 2" />
      <path d="M14 16l-2 2-2-2" />
    </svg>
  )
}

function BikeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="7.5" cy="16.5" r="2.5" />
      <circle cx="16.5" cy="16.5" r="2.5" />
      <path d="M9 10h4l3 6" />
      <path d="M12 10l-2 6" />
      <path d="M8 10h2" />
    </svg>
  )
}

function ParkingIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <rect x="6" y="4" width="12" height="16" rx="2" />
      <path d="M10 16V8h3a2.5 2.5 0 0 1 0 5h-3" />
    </svg>
  )
}

function PawIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="8" cy="9" r="1.5" />
      <circle cx="12" cy="7.5" r="1.5" />
      <circle cx="16" cy="9" r="1.5" />
      <path d="M8 16c0-2.2 2-4 4-4s4 1.8 4 4c0 1.5-1.2 2-4 2s-4-.5-4-2Z" />
    </svg>
  )
}

function SmokeFreeIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M5 14h8" />
      <path d="M17 13v2" />
      <path d="M19 13v2" />
      <path d="M7 8c1 0 2 .8 2 2" />
      <path d="M4 6l16 12" />
    </svg>
  )
}

function SparkIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M12 4v4" />
      <path d="M12 16v4" />
      <path d="M4 12h4" />
      <path d="M16 12h4" />
      <path d="M7 7l2 2" />
      <path d="M15 15l2 2" />
      <path d="M17 7l-2 2" />
      <path d="M9 15l-2 2" />
    </svg>
  )
}

function CapIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M4 10l8-4 8 4-8 4-8-4Z" />
      <path d="M8 12.5v3c0 1 1.8 2 4 2s4-1 4-2v-3" />
    </svg>
  )
}

function HeartIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M12 19s-6-3.8-6-8.2A3.8 3.8 0 0 1 12 8a3.8 3.8 0 0 1 6 2.8C18 15.2 12 19 12 19Z" />
    </svg>
  )
}

function ChatIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M5 7h14v9H9l-4 3V7Z" />
      <path d="M8 11h8" />
      <path d="M8 14h5" />
    </svg>
  )
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
