import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppTabs } from '../components/AppTabs'
import { Button, Card, Input, Select } from '../components/ui'
import { ApiError, getSearchProfile, updateUser } from '../lib/api'
import { useSession } from '../lib/session'
import type { Gender, SearchProfile } from '../types'

const GENDER_OPTIONS: { value: Gender; label: string }[] = [
  { value: 'female', label: 'Female' },
  { value: 'male', label: 'Male' },
  { value: 'diverse', label: 'Diverse' },
  { value: 'prefer_not_to_say', label: 'Prefer not to say' },
]

export default function Profile() {
  const navigate = useNavigate()
  const { username, user, isReady, refreshUser } = useSession()
  const [ageInput, setAgeInput] = useState('')
  const [gender, setGender] = useState<Gender | ''>('')
  const [profile, setProfile] = useState<SearchProfile | null>(null)
  const [busy, setBusy] = useState(false)
  const [hydrated, setHydrated] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)
  const [errors, setErrors] = useState<{ age?: string; gender?: string }>({})

  useEffect(() => {
    if (!isReady) return
    if (!username) {
      navigate('/onboarding/profile', { replace: true })
      return
    }
    let cancelled = false
    void (async () => {
      try {
        const nextProfile = await getSearchProfile(username)
        if (!cancelled) {
          setProfile(nextProfile)
        }
      } finally {
        if (!cancelled) {
          setHydrated(true)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username, navigate])

  useEffect(() => {
    if (!user) return
    setAgeInput(String(user.age))
    setGender(user.gender)
  }, [user])

  const handleSave = async () => {
    if (!username || !user) return
    setFooter(null)
    const nextErrors: { age?: string; gender?: string } = {}
    const age = Number(ageInput)
    if (!Number.isInteger(age) || age < 16 || age > 99) {
      nextErrors.age = 'Age must be a whole number between 16 and 99.'
    }
    if (!gender) {
      nextErrors.gender = 'Select a gender.'
    }
    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) {
      return
    }

    setBusy(true)
    try {
      await updateUser(username, { age, gender: gender as Gender })
      await refreshUser()
      setFooter(<p className="text-[15px] text-good">Profile updated.</p>)
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

  if (!isReady || !hydrated || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas font-sans text-[15px] text-ink-muted">
        Loading…
      </div>
    )
  }

  return (
    <div className="relative min-h-screen overflow-hidden bg-canvas">
      <div className="relative mx-auto max-w-7xl px-5 py-5 sm:px-8 lg:px-10">
        <section className="overflow-hidden rounded-[34px] border border-hairline/80 bg-surface/95 shadow-[0_30px_80px_rgba(15,23,42,0.08)]">
          <div className="grid gap-6 border-b border-hairline/80 px-6 py-6 lg:grid-cols-[minmax(0,1.2fr)_auto] lg:px-8 xl:px-10">
            <div>
              <p className="font-mono text-[12px] uppercase tracking-[0.28em] text-accent">Account</p>
              <h1 className="mt-3 text-[30px] font-semibold tracking-[-0.035em] text-ink sm:text-[38px]">
                Keep your hunt profile current
              </h1>
              <p className="mt-3 max-w-2xl text-[15px] leading-7 text-ink-muted">
                Update your core profile here, then jump back into the search brief whenever your budget, commute, or preferences change.
              </p>
            </div>
            <div className="flex items-start justify-start lg:justify-end">
              <AppTabs
                current="/profile"
                tabs={[
                  { label: 'Dashboard', href: '/dashboard' },
                  { label: 'Timeline', href: '/timeline' },
                  { label: 'Profile', href: '/profile' },
                ]}
              />
            </div>
          </div>

          <div className="grid gap-6 px-6 py-6 lg:grid-cols-[minmax(0,1.1fr)_320px] lg:px-8 xl:px-10">
            <Card className="rounded-[28px] border-hairline/80 bg-surface-raised/85 p-6">
              <p className="text-[18px] font-semibold tracking-[-0.02em] text-ink">Basic details</p>
              <div className="mt-6 grid gap-5 md:grid-cols-2">
                <FieldCard label="Username" hint="Usernames stay fixed because they key your stored hunt data.">
                  <div className="rounded-[18px] border border-hairline/80 bg-surface px-4 py-3 text-[15px] text-ink">
                    {user.username}
                  </div>
                </FieldCard>

                <FieldCard label="Age" hint="Used in your saved account profile." error={errors.age}>
                  <Input
                    type="number"
                    min={16}
                    max={99}
                    step={1}
                    value={ageInput}
                    onChange={(event) => {
                      setAgeInput(event.target.value)
                      if (errors.age) setErrors((prev) => ({ ...prev, age: undefined }))
                    }}
                  />
                </FieldCard>

                <FieldCard label="Gender" hint="Matches the current backend profile model." error={errors.gender} className="md:col-span-2">
                  <Select
                    id="profile-gender"
                    value={gender}
                    onChange={(event) => {
                      setGender(event.target.value as Gender | '')
                      if (errors.gender) setErrors((prev) => ({ ...prev, gender: undefined }))
                    }}
                    aria-invalid={Boolean(errors.gender)}
                  >
                    <option value="" disabled>
                      Select…
                    </option>
                    {GENDER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </Select>
                </FieldCard>
              </div>

              <div className="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-hairline/80 pt-5">
                <p className="text-[13px] text-ink-muted">Created {new Date(user.createdAt).toLocaleDateString()}</p>
                <Button variant="primary" onClick={() => void handleSave()} disabled={busy}>
                  {busy ? 'Saving…' : 'Save profile'}
                </Button>
              </div>
              {footer ? <div className="mt-4">{footer}</div> : null}
            </Card>

            <div className="space-y-6">
              <Card className="rounded-[28px] bg-surface-raised p-6">
                <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">Adjust setup</p>
                <div className="mt-5 space-y-3">
                  <JumpRow
                    title="Requirements"
                    detail={profile ? 'Budget, commute anchors, and search cadence.' : 'Finish your brief to unlock this step.'}
                    actionLabel="Edit"
                    disabled={!profile}
                    onClick={() => navigate('/onboarding/requirements')}
                  />
                  <JumpRow
                    title="Preferences"
                    detail={profile ? 'Weighted nice-to-haves for ranking.' : 'Add requirements first to keep the flow grounded.'}
                    actionLabel="Edit"
                    disabled={!profile}
                    onClick={() => navigate('/onboarding/preferences')}
                  />
                </div>
              </Card>

              <Card className="rounded-[28px] p-6">
                <p className="text-[18px] font-semibold tracking-[-0.02em] text-ink">Current brief</p>
                <ul className="mt-4 space-y-3 text-[14px] leading-6 text-ink-muted">
                  <li>{profile ? `Budget cap: ${profile.priceMaxEur !== null ? `${profile.priceMaxEur}€` : 'Flexible'}` : 'No search brief saved yet.'}</li>
                  <li>{profile ? `${profile.mainLocations.length} commute anchors saved.` : 'Commute anchors will appear once you add them.'}</li>
                  <li>{profile ? `${profile.preferences.length} weighted preferences configured.` : 'Preferences become available after requirements.'}</li>
                </ul>
              </Card>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}

function FieldCard({
  label,
  hint,
  error,
  className,
  children,
}: {
  label: string
  hint: string
  error?: string
  className?: string
  children: ReactNode
}) {
  return (
    <div className={className}>
      <div className="rounded-[24px] border border-hairline/80 bg-surface p-5">
        <p className="text-[14px] font-semibold text-ink">{label}</p>
        <p className="mt-1 text-[13px] leading-6 text-ink-muted">{error ?? hint}</p>
        <div className="mt-4">{children}</div>
      </div>
    </div>
  )
}

function JumpRow({
  title,
  detail,
  actionLabel,
  disabled,
  onClick,
}: {
  title: string
  detail: string
  actionLabel: string
  disabled: boolean
  onClick: () => void
}) {
  return (
    <div className="flex items-center justify-between gap-3 rounded-2xl border border-hairline/80 bg-surface-raised/85 px-4 py-3">
      <div>
        <p className="text-[15px] font-semibold text-ink">{title}</p>
        <p className="mt-1 text-[13px] leading-6 text-ink-muted">{detail}</p>
      </div>
      <Button variant="secondary" size="sm" onClick={onClick} disabled={disabled}>
        {actionLabel}
      </Button>
    </div>
  )
}
