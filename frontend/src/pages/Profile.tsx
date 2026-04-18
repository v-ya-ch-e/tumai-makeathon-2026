import { useCallback, useEffect, useState, type ReactNode } from 'react'
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
  const { username, user, isReady, refreshUser, setUsername } = useSession()
  const [ageInput, setAgeInput] = useState('')
  const [gender, setGender] = useState<Gender | ''>('')
  const [notificationEmailInput, setNotificationEmailInput] = useState('')
  const [profile, setProfile] = useState<SearchProfile | null>(null)
  const [busy, setBusy] = useState(false)
  const [hydrated, setHydrated] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)
  const [errors, setErrors] = useState<{ age?: string; gender?: string; notificationEmail?: string }>({})

  const handleLogout = useCallback(() => {
    localStorage.removeItem('wg-hunter.hunt-id')
    setUsername(null)
    navigate('/onboarding/profile', { replace: true })
  }, [navigate, setUsername])

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
        if (!cancelled) setProfile(nextProfile)
      } finally {
        if (!cancelled) setHydrated(true)
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
    setNotificationEmailInput(user.email ?? '')
  }, [user])

  const handleSave = async () => {
    if (!username || !user) return
    setFooter(null)

    const nextErrors: { age?: string; gender?: string; notificationEmail?: string } = {}
    const age = Number(ageInput)
    if (!Number.isInteger(age) || age < 16 || age > 99) {
      nextErrors.age = 'Age must be a whole number between 16 and 99.'
    }
    if (!gender) {
      nextErrors.gender = 'Select the current profile value.'
    }
    const notificationEmail = notificationEmailInput.trim()
    if (notificationEmail && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(notificationEmail)) {
      nextErrors.notificationEmail = 'Enter a valid email address or leave it blank.'
    }

    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) return

    setBusy(true)
    try {
      await updateUser(username, {
        age,
        gender: gender as Gender,
        email: notificationEmail || null,
      })
      await refreshUser()
      setFooter(<p className="text-[15px] text-good">Changes saved.</p>)
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
    <div className="min-h-screen bg-canvas">
      <div className="app-shell space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-4 border-b border-hairline pb-4">
          <div>
            <p className="section-kicker text-accent">Sherlock Homes</p>
            <p className="mt-1 text-[14px] text-ink-muted">Profile and saved brief</p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-3">
            <AppTabs
              current="/profile"
              tabs={[
                { label: 'Dashboard', href: '/dashboard' },
                { label: 'Profile', href: '/profile' },
              ]}
            />
            <Button variant="secondary" size="sm" onClick={handleLogout}>
              Log out
            </Button>
          </div>
        </div>

        <header className="page-frame overflow-hidden">
          <div className="px-6 py-8 lg:px-8">
            <p className="section-kicker text-accent">Profile</p>
            <h1 className="page-title mt-4">Your profile</h1>
            <p className="body-copy mt-4 max-w-3xl">
              Update the personal details tied to your saved search. Your search brief and signals stay in the next steps.
            </p>
          </div>
        </header>

        <section className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="overflow-hidden rounded-card border border-hairline bg-surface">
            <FieldRow label="Username" hint="This stays the same so you can find your saved search again.">
              <div className="rounded border border-hairline bg-surface-raised px-3 py-3 text-[15px] text-ink">
                {user.username}
              </div>
            </FieldRow>

            <FieldRow label="Age" hint={errors.age ?? 'Helpful when listings mention a preferred age range.'} error={Boolean(errors.age)}>
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
            </FieldRow>

            <FieldRow
              label="Notification email"
              hint={errors.notificationEmail ?? 'Optional for future updates and alerts.'}
              error={Boolean(errors.notificationEmail)}
            >
              <Input
                type="email"
                value={notificationEmailInput}
                onChange={(event) => {
                  setNotificationEmailInput(event.target.value)
                  if (errors.notificationEmail) setErrors((prev) => ({ ...prev, notificationEmail: undefined }))
                }}
                aria-invalid={Boolean(errors.notificationEmail)}
                placeholder="you@example.com"
              />
            </FieldRow>

            <FieldRow label="Gender" hint={errors.gender ?? 'Used when a listing mentions a preferred fit.'} error={Boolean(errors.gender)}>
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
            </FieldRow>

            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-hairline px-5 py-5 md:px-6">
              <p className="text-[13px] text-ink-muted">Created {new Date(user.createdAt).toLocaleDateString()}</p>
              <Button variant="primary" onClick={() => void handleSave()} disabled={busy}>
                {busy ? 'Saving…' : 'Save changes'}
              </Button>
            </div>
            {footer ? <div className="px-5 pb-5 md:px-6">{footer}</div> : null}
          </div>

          <div className="space-y-6">
            <Card className="panel p-6">
              <p className="section-kicker">Jump back</p>
              <div className="mt-5 space-y-4">
                <JumpRow
                  title="Requirements"
                  detail={profile ? 'Budget, key destinations, and search mode.' : 'Finish your profile first to set up the search.'}
                  disabled={!profile}
                  onClick={() => navigate('/onboarding/requirements')}
                />
                <JumpRow
                  title="Preferences"
                  detail={profile ? 'Details that help sort similar places.' : 'Set your requirements first so preferences stay useful.'}
                  disabled={!profile}
                  onClick={() => navigate('/onboarding/preferences')}
                />
              </div>
            </Card>

            <Card className="panel-muted p-6">
              <p className="section-kicker">Current brief</p>
              <div className="mt-5 space-y-3">
                <BriefRow label="Budget" value={profile ? (profile.priceMaxEur !== null ? `${profile.priceMaxEur} EUR` : 'Flexible') : 'Not saved'} />
                <BriefRow label="Anchors" value={profile ? `${profile.mainLocations.length} places` : 'Not saved'} />
                <BriefRow label="Preferences" value={profile ? `${profile.preferences.length} weighted` : 'Not saved'} />
              </div>
            </Card>
          </div>
        </section>
      </div>
    </div>
  )
}

function FieldRow({
  label,
  hint,
  error = false,
  children,
}: {
  label: string
  hint: string
  error?: boolean
  children: ReactNode
}) {
  return (
    <section className="grid gap-3 border-t border-hairline px-5 py-5 first:border-t-0 md:grid-cols-[190px_minmax(0,1fr)] md:gap-6 md:px-6">
      <div>
        <h2 className="text-[15px] font-semibold text-ink">{label}</h2>
        <p className={`mt-1 text-[13px] leading-6 ${error ? 'text-bad' : 'text-ink-muted'}`}>{hint}</p>
      </div>
      <div>{children}</div>
    </section>
  )
}

function JumpRow({
  title,
  detail,
  disabled,
  onClick,
}: {
  title: string
  detail: string
  disabled: boolean
  onClick: () => void
}) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-hairline pt-4 first:border-t-0 first:pt-0">
      <div>
        <p className="text-[15px] font-medium text-ink">{title}</p>
        <p className="mt-1 text-[13px] leading-6 text-ink-muted">{detail}</p>
      </div>
      <Button variant="secondary" size="sm" onClick={onClick} disabled={disabled}>
        Edit
      </Button>
    </div>
  )
}

function BriefRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-hairline pt-3 first:border-t-0 first:pt-0">
      <span className="data-label">{label}</span>
      <span className="text-right text-[14px] text-ink">{value}</span>
    </div>
  )
}
