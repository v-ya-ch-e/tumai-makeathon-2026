import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { AppTabs } from '../components/AppTabs'
import { Button, Card, Input, Select } from '../components/ui'
import { ApiError, getSearchProfile, updateUser } from '../lib/api'
import { formatGermanDate } from '../lib/date'
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
      <div className="app-shell space-y-6">
        <header className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <p className="brand-wordmark">Sherlock Homes</p>
            <p className="mt-1 max-w-xl text-[14px] text-ink-muted">
              A smarter search for places in Munich that fit your lifestyle.
            </p>
          </div>
          <div className="flex flex-wrap items-center justify-end gap-2">
            <AppTabs
              current="/profile"
              tabs={[
                { label: 'Dashboard', href: '/dashboard' },
                { label: 'Profile', href: '/profile' },
              ]}
            />
            <button
              type="button"
              onClick={handleLogout}
              className="rounded-full border border-hairline bg-surface px-4 py-2 text-[13px] font-medium text-ink transition-colors hover:border-ink"
            >
              Log out
            </button>
          </div>
        </header>

        <section className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="page-frame overflow-hidden">
            <div className="px-6 py-6 sm:px-8">
              <p className="section-kicker text-accent">Profile</p>
            </div>
            <div className="space-y-5 border-t border-hairline px-6 py-6 sm:px-8">
              <Field label="Username" hint="Stays the same so you can find your saved search again.">
                <div className="rounded border border-accent/40 bg-accent-muted/40 px-3 py-2.5 text-[15px] text-ink">
                  {user.username}
                </div>
              </Field>

              <Field label="Age" error={errors.age}>
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
              </Field>

              <Field
                label={
                  <>
                    Email <span className="font-normal text-ink-muted">(optional)</span>
                  </>
                }
                error={errors.notificationEmail}
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
              </Field>

              <Field label="Gender" error={errors.gender}>
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
              </Field>
            </div>

            <div className="border-t border-hairline px-6 py-5 sm:px-8">
              {footer ? <div className="mb-4">{footer}</div> : null}
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-[13px] text-ink-muted">Created {formatGermanDate(user.createdAt)}</p>
                <Button variant="primary" onClick={() => void handleSave()} disabled={busy}>
                  {busy ? 'Saving…' : 'Save changes'}
                </Button>
              </div>
            </div>
          </div>

          <div className="space-y-6">
            <Card className="panel p-6">
              <p className="section-kicker">Jump back</p>
              <div className="mt-5 space-y-4">
                <JumpRow
                  title="Requirements"
                  disabled={!profile}
                  onClick={() => navigate('/onboarding/requirements')}
                />
                <JumpRow
                  title="Preferences"
                  disabled={!profile}
                  onClick={() => navigate('/onboarding/preferences')}
                />
              </div>
            </Card>

            <Card className="panel p-6">
              <p className="section-kicker">Current brief</p>
              <div className="mt-5 space-y-3">
                <BriefRow
                  label="Budget"
                  value={
                    profile
                      ? profile.priceMaxEur !== null
                        ? `${profile.priceMaxEur} EUR`
                        : 'Flexible'
                      : 'Not saved'
                  }
                />
                <BriefRow
                  label="Anchors"
                  value={
                    profile
                      ? `${profile.mainLocations.length} ${profile.mainLocations.length === 1 ? 'place' : 'places'}`
                      : 'Not saved'
                  }
                />
                <BriefRow
                  label="Preferences"
                  value={profile ? `${profile.preferences.length} weighted` : 'Not saved'}
                />
              </div>
            </Card>
          </div>
        </section>
      </div>
    </div>
  )
}

function Field({
  label,
  hint,
  error,
  children,
}: {
  label: ReactNode
  hint?: string
  error?: string
  children: ReactNode
}) {
  return (
    <div>
      <p className="mb-1.5 text-[14px] font-medium text-ink">{label}</p>
      {children}
      {error ? (
        <p className="mt-1.5 text-[13px] text-bad">{error}</p>
      ) : hint ? (
        <p className="mt-1.5 text-[13px] text-ink-muted">{hint}</p>
      ) : null}
    </div>
  )
}

function JumpRow({
  title,
  disabled,
  onClick,
}: {
  title: string
  disabled: boolean
  onClick: () => void
}) {
  return (
    <div className="flex items-center justify-between gap-4 border-t border-hairline pt-4 first:border-t-0 first:pt-0">
      <p className="text-[18px] font-semibold text-ink">{title}</p>
      <Button variant="secondary" size="sm" onClick={onClick} disabled={disabled}>
        Edit
      </Button>
    </div>
  )
}

function BriefRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4 border-t border-hairline pt-3 first:border-t-0 first:pt-0">
      <span className="data-label">{label}</span>
      <span className="text-right text-[14px] text-ink">{value}</span>
    </div>
  )
}
