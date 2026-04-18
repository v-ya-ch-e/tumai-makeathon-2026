import clsx from 'clsx'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { WeightSlider } from '../components/ui'
import { ApiError, getSearchProfile, putSearchProfile } from '../lib/api'
import { useSession } from '../lib/session'
import type { PreferenceWeight, SearchProfile, UpsertSearchProfileBody } from '../types'

type PreferenceTile = {
  key: string
  label: string
  svg: ReactNode
}

type PreferenceGroup = {
  id: string
  title: string
  tiles: PreferenceTile[]
}

const STROKE = 1.5

const Icon = ({ path }: { path: string }) => (
  <svg
    viewBox="0 0 24 24"
    width={24}
    height={24}
    fill="none"
    stroke="currentColor"
    strokeWidth={STROKE}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden
  >
    <path d={path} />
  </svg>
)

const GROUPS: PreferenceGroup[] = [
  {
    id: 'neighbourhood',
    title: 'Neighbourhood',
    tiles: [
      {
        key: 'supermarket',
        label: 'Supermarket nearby',
        svg: <Icon path="M3 6h18l-2 13H5L3 6zM8 10v4M12 10v4M16 10v4" />,
      },
      {
        key: 'gym',
        label: 'Gym nearby',
        svg: <Icon path="M4 10h2v4H4zM18 10h2v4h-2zM6 12h12M9 8v8M15 8v8" />,
      },
      {
        key: 'park',
        label: 'Park nearby',
        svg: <Icon path="M12 3c3 3 3 7 0 10s-3-7 0-10zM12 13v8M8 21h8" />,
      },
      {
        key: 'cafe',
        label: 'Cafe nearby',
        svg: <Icon path="M4 9h14v5a4 4 0 0 1-4 4H8a4 4 0 0 1-4-4V9zM18 10h2a2 2 0 0 1 0 4h-2" />,
      },
      {
        key: 'bars',
        label: 'Bars nearby',
        svg: <Icon path="M5 4h14l-5 7v6h2v3H8v-3h2v-6L5 4z" />,
      },
      {
        key: 'library',
        label: 'Library nearby',
        svg: <Icon path="M4 5h6v14H4zM14 5h6v14h-6zM4 9h6M4 13h6M14 9h6M14 13h6" />,
      },
      {
        key: 'coworking',
        label: 'Coworking nearby',
        svg: <Icon path="M3 6h18v10H3zM3 16v3h18v-3M9 12h6" />,
      },
      {
        key: 'nightlife',
        label: 'Nightlife nearby',
        svg: <Icon path="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />,
      },
      {
        key: 'green_space',
        label: 'Green space',
        svg: <Icon path="M7 14a5 5 0 0 1 10 0M9 20h6M12 20v-6M6 14h12" />,
      },
      {
        key: 'quiet_area',
        label: 'Quiet neighbourhood',
        svg: <Icon path="M4 17V7l6-4v18l-6-4zM14 9a5 5 0 0 1 0 6M17 6a9 9 0 0 1 0 12" />,
      },
    ],
  },
  {
    id: 'place-features',
    title: 'Place features',
    tiles: [
      {
        key: 'furnished',
        label: 'Furnished',
        svg: <Icon path="M4 10V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2M3 14h18v4H3zM5 18v2M19 18v2" />,
      },
      {
        key: 'balcony',
        label: 'Balcony',
        svg: <Icon path="M4 10h16v3H4zM6 13v8M12 13v8M18 13v8M4 21h16" />,
      },
      {
        key: 'washing_machine',
        label: 'Washing machine',
        svg: <Icon path="M5 4h14v16H5zM8 8h8M12 14m-4 0a4 4 0 1 0 8 0 4 4 0 0 0-8 0" />,
      },
      {
        key: 'dishwasher',
        label: 'Dishwasher',
        svg: <Icon path="M5 4h14v16H5zM5 9h14M9 13h6M9 17h6" />,
      },
      {
        key: 'garden',
        label: 'Garden',
        svg: <Icon path="M12 3v18M5 10c3-1 5 1 7 3 2-2 4-4 7-3M5 18c3-1 5 1 7 3 2-2 4-4 7-3" />,
      },
      {
        key: 'elevator',
        label: 'Elevator',
        svg: <Icon path="M5 3h14v18H5zM12 3v18M9 8l3-3 3 3M9 16l3 3 3-3" />,
      },
      {
        key: 'bike_storage',
        label: 'Bike storage',
        svg: <Icon path="M5 17m-3 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0M19 17m-3 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0M9 17l3-8h4M9 10h4" />,
      },
      {
        key: 'parking',
        label: 'Parking',
        svg: <Icon path="M6 3h8a5 5 0 0 1 0 10h-4V21H6zM10 7v4h4a2 2 0 0 0 0-4z" />,
      },
    ],
  },
  {
    id: 'living-style',
    title: 'Living style',
    tiles: [
      {
        key: 'pet_friendly',
        label: 'Pet-friendly',
        svg: (
          <Icon path="M5 12a2 2 0 1 1 4 0 2 2 0 0 1-4 0zM15 12a2 2 0 1 1 4 0 2 2 0 0 1-4 0zM8 6a2 2 0 1 1 4 0 2 2 0 0 1-4 0zM12 6a2 2 0 1 1 4 0 2 2 0 0 1-4 0zM7 18a5 5 0 0 1 10 0v1H7v-1z" />
        ),
      },
      {
        key: 'non_smoking',
        label: 'Non-smoking',
        svg: <Icon path="M3 12h11v3H3zM16 12h2v3h-2zM19 12h2v3h-2zM4 4l16 16" />,
      },
      {
        key: 'lgbt_friendly',
        label: 'LGBT-friendly',
        svg: <Icon path="M3 18c0-8 6-12 12-12M3 18h18M3 15h18M3 12h15" />,
      },
      {
        key: 'student_household',
        label: 'Student household',
        svg: <Icon path="M3 9l9-4 9 4-9 4-9-4zM7 11v5a5 5 0 0 0 10 0v-5M21 9v6" />,
      },
      {
        key: 'couples_ok',
        label: 'Couples OK',
        svg: <Icon path="M12 21s-7-4.5-7-10a4 4 0 0 1 7-2 4 4 0 0 1 7 2c0 5.5-7 10-7 10z" />,
      },
      {
        key: 'english_speaking',
        label: 'English-speaking',
        svg: <Icon path="M3 12a9 9 0 1 0 18 0 9 9 0 0 0-18 0zM3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18" />,
      },
    ],
  },
]

const DEFAULT_WEIGHT = 3

export default function OnboardingPreferences() {
  const navigate = useNavigate()
  const { username, isReady } = useSession()
  const [profile, setProfile] = useState<SearchProfile | null>(null)
  const [selected, setSelected] = useState<Map<string, number>>(new Map())
  const [hydrated, setHydrated] = useState(false)
  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)

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
        if (cancelled) return
        if (sp === null) {
          navigate('/onboarding/requirements', { replace: true })
          return
        }
        setProfile(sp)
        const next = new Map<string, number>()
        for (const p of sp.preferences) {
          next.set(p.key, clampWeight(p.weight))
        }
        setSelected(next)
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

  const handleNext = async () => {
    setFooter(null)
    if (!username || !profile) return
    const preferences: PreferenceWeight[] = Array.from(selected.entries()).map(
      ([key, weight]) => ({ key, weight }),
    )
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
      navigate('/dashboard')
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
      <OnboardingShell step={3} title="Nice-to-haves" onNext={() => undefined} busy>
        <div />
      </OnboardingShell>
    )
  }

  return (
    <OnboardingShell
      step={3}
      title="Nice-to-haves"
      description="Pick anything that would make a place feel like home. Use the slider to tell the agent how important each one is — ‘must-have’ acts as a near-hard filter."
      onBack={() => navigate('/onboarding/requirements')}
      onNext={() => void handleNext()}
      busy={busy}
      nextLabel="Start hunting"
      footer={footer}
    >
      <div className="space-y-8">
        {GROUPS.map((group) => (
          <section key={group.id} className="space-y-3">
            <h2 className="text-[13px] uppercase tracking-wide text-ink-muted">
              {group.title}
            </h2>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {group.tiles.map((tile) => {
                const weight = selected.get(tile.key)
                const isSelected = weight !== undefined
                const sliderId = `weight-${tile.key}`
                return (
                  <div
                    key={tile.key}
                    className={clsx(
                      'flex flex-col gap-3 rounded-card border p-4 transition-colors duration-150 ease-out',
                      isSelected
                        ? 'border-accent bg-accent-muted text-ink'
                        : 'border-hairline bg-surface text-ink hover:bg-surface-raised',
                    )}
                  >
                    <button
                      type="button"
                      onClick={() => toggle(tile.key)}
                      aria-pressed={isSelected}
                      className="flex items-start gap-3 text-left"
                    >
                      <span
                        className={clsx(
                          'transition-colors',
                          isSelected ? 'text-accent' : 'text-ink-muted',
                        )}
                      >
                        {tile.svg}
                      </span>
                      <span className="text-[14px] font-medium leading-snug">
                        {tile.label}
                      </span>
                    </button>
                    {isSelected && (
                      <div>
                        <label
                          htmlFor={sliderId}
                          className="mb-1 block text-[11px] text-ink-muted"
                        >
                          Importance
                        </label>
                        <WeightSlider
                          id={sliderId}
                          ariaLabel={`Importance of ${tile.label}`}
                          value={weight ?? DEFAULT_WEIGHT}
                          onChange={(w) => setWeight(tile.key, w)}
                        />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </section>
        ))}
      </div>
    </OnboardingShell>
  )
}

function clampWeight(n: number): number {
  if (!Number.isFinite(n)) return DEFAULT_WEIGHT
  const rounded = Math.round(n)
  if (rounded < 1) return 1
  if (rounded > 5) return 5
  return rounded
}
