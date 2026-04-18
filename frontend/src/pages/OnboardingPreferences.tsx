import clsx from 'clsx'
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { ApiError, getSearchProfile, putSearchProfile } from '../lib/api'
import { useSession } from '../lib/session'
import type { SearchProfile, UpsertSearchProfileBody } from '../types'

type PreferenceTile = {
  key: string
  label: string
  svg: ReactNode
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

const TILES: PreferenceTile[] = [
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
    key: 'public_transport',
    label: 'Public transport',
    svg: <Icon path="M6 4h12v12H6zM6 16l-2 4M18 16l2 4M9 8h6M8 12h.01M16 12h.01" />,
  },
  {
    key: 'quiet_area',
    label: 'Quiet neighbourhood',
    svg: <Icon path="M4 17V7l6-4v18l-6-4zM14 9a5 5 0 0 1 0 6M17 6a9 9 0 0 1 0 12" />,
  },
  {
    key: 'furnished',
    label: 'Furnished',
    svg: <Icon path="M4 10V8a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v2M3 14h18v4H3zM5 18v2M19 18v2" />,
  },
  {
    key: 'pet_friendly',
    label: 'Pet-friendly',
    svg: (
      <Icon path="M5 12a2 2 0 1 1 4 0 2 2 0 0 1-4 0zM15 12a2 2 0 1 1 4 0 2 2 0 0 1-4 0zM8 6a2 2 0 1 1 4 0 2 2 0 0 1-4 0zM12 6a2 2 0 1 1 4 0 2 2 0 0 1-4 0zM7 18a5 5 0 0 1 10 0v1H7v-1z" />
    ),
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
    key: 'bike_storage',
    label: 'Bike storage',
    svg: <Icon path="M5 17m-3 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0M19 17m-3 0a3 3 0 1 0 6 0 3 3 0 0 0-6 0M9 17l3-8h4M9 10h4" />,
  },
  {
    key: 'short_commute',
    label: 'Short commute',
    svg: <Icon path="M12 3v18M5 12h14M7 8l10 8M17 8 7 16" />,
  },
]

export default function OnboardingPreferences() {
  const navigate = useNavigate()
  const { username, isReady } = useSession()
  const [profile, setProfile] = useState<SearchProfile | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
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
        setSelected(new Set(sp.preferences))
      } finally {
        if (!cancelled) setHydrated(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username, navigate])

  const toggle = (key: string) => {
    const next = new Set(selected)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    setSelected(next)
  }

  const handleNext = async () => {
    setFooter(null)
    if (!username || !profile) return
    const body: UpsertSearchProfileBody = {
      priceMinEur: profile.priceMinEur,
      priceMaxEur: profile.priceMaxEur,
      mainLocations: profile.mainLocations,
      hasCar: profile.hasCar,
      hasBike: profile.hasBike,
      mode: profile.mode,
      moveInFrom: profile.moveInFrom,
      moveInUntil: profile.moveInUntil,
      preferences: Array.from(selected),
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
      description="Pick anything that would make a place feel like home. Skip freely — nothing is required."
      onBack={() => navigate('/onboarding/requirements')}
      onNext={() => void handleNext()}
      busy={busy}
      nextLabel="Start hunting"
      footer={footer}
    >
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {TILES.map((t) => {
          const isSelected = selected.has(t.key)
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => toggle(t.key)}
              aria-pressed={isSelected}
              className={clsx(
                'flex flex-col items-start gap-3 rounded-card border p-4 text-left transition-colors duration-150 ease-out',
                isSelected
                  ? 'border-accent bg-accent-muted text-ink'
                  : 'border-hairline bg-surface text-ink hover:bg-surface-raised',
              )}
            >
              <span className={clsx('transition-colors', isSelected ? 'text-accent' : 'text-ink-muted')}>
                {t.svg}
              </span>
              <span className="text-[14px] font-medium leading-snug">{t.label}</span>
            </button>
          )
        })}
      </div>
    </OnboardingShell>
  )
}
