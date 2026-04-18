import clsx from 'clsx'
import { useMemo, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { Card, Input, Select } from '../components/ui'
import { ApiError, createUser, getUser } from '../lib/api'
import { onboardingSteps } from '../lib/onboarding'
import { useSession } from '../lib/session'
import type { Gender } from '../types'

const USERNAME_RE = /^[a-zA-Z0-9_-]+$/
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

const GENDER_OPTIONS: { value: Gender; label: string }[] = [
  { value: 'female', label: 'Female' },
  { value: 'male', label: 'Male' },
  { value: 'diverse', label: 'Diverse' },
  { value: 'prefer_not_to_say', label: 'Prefer not to say' },
]

type Mode = 'create' | 'signin'

type FieldErrors = {
  username?: string
  gender?: string
  age?: string
  notificationEmail?: string
  signInUsername?: string
}

export default function OnboardingProfile() {
  const navigate = useNavigate()
  const { setUsername } = useSession()

  const [mode, setMode] = useState<Mode>('create')

  const [usernameInput, setUsernameInput] = useState('')
  const [gender, setGender] = useState<Gender | ''>('')
  const [ageInput, setAgeInput] = useState('')
  const [notificationEmailInput, setNotificationEmailInput] = useState('')
  const [signInUsername, setSignInUsername] = useState('')

  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)
  const [errors, setErrors] = useState<FieldErrors>({})

  const usernamePreview = useMemo(() => usernameInput.trim() || 'your-hunt-name', [usernameInput])

  const switchMode = (next: Mode) => {
    if (next === mode || busy) return
    setMode(next)
    setFooter(null)
    setErrors({})
  }

  const validateUsername = (value: string): string | null => {
    if (value.length < 1 || value.length > 40) {
      return 'Username must be between 1 and 40 characters.'
    }
    if (!USERNAME_RE.test(value)) {
      return 'Use only letters, numbers, underscore, and hyphen.'
    }
    return null
  }

  const handleCreate = async () => {
    setFooter(null)
    const username = usernameInput.trim()
    const nextErrors: FieldErrors = {}

    const nameErr = validateUsername(username)
    if (nameErr) nextErrors.username = nameErr
    if (!gender) nextErrors.gender = 'Select a gender.'

    const age = Number(ageInput)
    if (!Number.isInteger(age) || age < 16 || age > 99) {
      nextErrors.age = 'Age must be a whole number between 16 and 99.'
    }

    const notificationEmail = notificationEmailInput.trim()
    if (notificationEmail && !EMAIL_RE.test(notificationEmail)) {
      nextErrors.notificationEmail = 'Enter a valid email address or leave it blank.'
    }

    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) {
      return
    }

    setBusy(true)
    try {
      await createUser({
        username,
        age,
        gender: gender as Gender,
        notificationEmail: notificationEmailInput.trim() || null,
      })
      setUsername(username)
      navigate('/onboarding/requirements', { replace: false })
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setFooter(
          <p className="text-[15px] text-bad">
            That username is already taken. Switch to sign in or pick another one.
          </p>,
        )
      } else if (e instanceof ApiError) {
        setFooter(<p className="text-[15px] text-bad">{e.message}</p>)
      } else {
        setFooter(<p className="text-[15px] text-bad">{String(e)}</p>)
      }
    } finally {
      setBusy(false)
    }
  }

  const handleSignIn = async () => {
    setFooter(null)
    const username = signInUsername.trim()
    const nameErr = validateUsername(username)
    const nextErrors: FieldErrors = nameErr ? { signInUsername: nameErr } : {}
    setErrors(nextErrors)
    if (nameErr) return

    setBusy(true)
    try {
      const user = await getUser(username)
      if (user === null) {
        setFooter(
          <p className="text-[15px] text-bad">
            No account with that username yet. Create one to start your hunt.
          </p>,
        )
        return
      }
      setUsername(username)
      navigate('/', { replace: true })
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

  const onNext = mode === 'create' ? handleCreate : handleSignIn
  const nextLabel = mode === 'create' ? 'Continue to search brief' : 'Go to dashboard'
  const title = mode === 'create' ? 'Set up your hunt identity' : 'Welcome back'
  const description =
    mode === 'create'
      ? 'Start with a lightweight profile. We only collect enough to personalize the hunt and rank rooms more intelligently.'
      : 'Pick up where you left off. Your saved search profile and last hunt will be waiting.'

  return (
    <OnboardingShell
      step={1}
      eyebrow={mode === 'create' ? 'Profile setup' : 'Return to your hunt'}
      title={title}
      description={description}
      onNext={() => void onNext()}
      busy={busy}
      nextLabel={nextLabel}
      nextDisabled={false}
      footer={footer}
      showProgress={mode === 'create'}
      progressSteps={onboardingSteps({
        canAccessRequirements: false,
        canAccessPreferences: false,
        canAccessDashboard: false,
      })}
      aside={
        mode === 'create' ? (
          <div className="space-y-4">
            <Card className="rounded-[28px] border-hairline/80 bg-surface/92 p-6">
              <p className="font-mono text-[12px] uppercase tracking-[0.24em] text-accent">
                Preview
              </p>
              <p className="mt-4 text-[24px] font-semibold tracking-[-0.03em] text-ink">
                {usernamePreview}
              </p>
              <p className="mt-2 text-[14px] leading-6 text-ink-muted">
                This becomes the anchor for your saved search profile on this device.
              </p>
              <div className="mt-5 grid grid-cols-2 gap-3 text-[13px]">
                <Stat label="Age" value={ageInput || '—'} />
                <Stat label="Gender" value={gender ? GENDER_OPTIONS.find((option) => option.value === gender)?.label ?? '—' : '—'} />
              </div>
            </Card>
            <Card className="rounded-[28px] border-hairline/80 bg-surface/92 p-6">
              <p className="text-[14px] font-semibold text-ink">What happens next</p>
              <ul className="mt-3 space-y-3 text-[14px] leading-6 text-ink-muted">
                <li>Define your budget, commute targets, and search mode.</li>
                <li>Tell the agent which details feel optional versus non-negotiable.</li>
                <li>Launch a hunt and watch listings stream in live.</li>
              </ul>
            </Card>
          </div>
        ) : (
          <Card className="rounded-[28px] border-hairline/80 bg-surface/92 p-6">
            <p className="text-[14px] font-semibold text-ink">Returning users</p>
            <p className="mt-3 text-[14px] leading-6 text-ink-muted">
              Sign in with the username you created earlier. No password is required for the local demo flow.
            </p>
          </Card>
        )
      }
    >
      <div className="space-y-6">
        <ModeTabs mode={mode} onChange={switchMode} disabled={busy} />

        {mode === 'create' ? (
          <div className="grid gap-5 md:grid-cols-2">
            <FieldCard
              label="Choose a hunt name"
              hint="Use something memorable on this device - initials, a nickname, or a short theme all work well."
              error={errors.username}
            >
              <Input
                id="onboarding-username"
                value={usernameInput}
                onChange={(ev) => {
                  setUsernameInput(ev.target.value)
                  if (errors.username) setErrors((prev) => ({ ...prev, username: undefined }))
                }}
                autoComplete="username"
                maxLength={40}
                aria-invalid={Boolean(errors.username)}
                placeholder="room-hunt-2026"
              />
            </FieldCard>

            <FieldCard label="Age" hint="Used to make the profile feel more realistic to landlords." error={errors.age}>
              <Input
                id="onboarding-age"
                type="number"
                min={16}
                max={99}
                step={1}
                value={ageInput}
                onChange={(ev) => {
                  setAgeInput(ev.target.value)
                  if (errors.age) setErrors((prev) => ({ ...prev, age: undefined }))
                }}
                aria-invalid={Boolean(errors.age)}
                placeholder="24"
              />
            </FieldCard>

            <FieldCard
              label="Notification email"
              hint="Optional. We can use this later to alert you as soon as a strong offer appears."
              error={errors.notificationEmail}
            >
              <Input
                id="onboarding-notification-email"
                type="email"
                value={notificationEmailInput}
                onChange={(ev) => {
                  setNotificationEmailInput(ev.target.value)
                  if (errors.notificationEmail) {
                    setErrors((prev) => ({ ...prev, notificationEmail: undefined }))
                  }
                }}
                autoComplete="email"
                aria-invalid={Boolean(errors.notificationEmail)}
                placeholder="you@example.com"
              />
            </FieldCard>

            <FieldCard
              label="Gender"
              hint="We keep the profile options aligned with the existing backend model."
              error={errors.gender}
              className="md:col-span-2"
            >
              <Select
                id="onboarding-gender"
                value={gender}
                onChange={(ev) => {
                  setGender(ev.target.value as Gender | '')
                  if (errors.gender) setErrors((prev) => ({ ...prev, gender: undefined }))
                }}
                aria-invalid={Boolean(errors.gender)}
              >
                <option value="" disabled>
                  Select…
                </option>
                {GENDER_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </FieldCard>
          </div>
        ) : (
          <FieldCard
            label="Username"
            hint="Enter the same username you used when you first created your hunt profile."
            error={errors.signInUsername}
          >
            <Input
              id="signin-username"
              value={signInUsername}
              onChange={(ev) => {
                setSignInUsername(ev.target.value)
                if (errors.signInUsername) {
                  setErrors((prev) => ({ ...prev, signInUsername: undefined }))
                }
              }}
              autoComplete="username"
              maxLength={40}
              aria-invalid={Boolean(errors.signInUsername)}
              placeholder="room-hunt-2026"
              onKeyDown={(ev) => {
                if (ev.key === 'Enter' && !busy) {
                  ev.preventDefault()
                  void handleSignIn()
                }
              }}
            />
          </FieldCard>
        )}
      </div>
    </OnboardingShell>
  )
}

function ModeTabs({
  mode,
  onChange,
  disabled,
}: {
  mode: Mode
  onChange: (next: Mode) => void
  disabled: boolean
}) {
  return (
    <div
      role="tablist"
      aria-label="Account mode"
      className="inline-flex rounded-full border border-hairline bg-[#f3ecdf] p-1"
    >
      <TabButton active={mode === 'create'} disabled={disabled} onClick={() => onChange('create')}>
        Create account
      </TabButton>
      <TabButton active={mode === 'signin'} disabled={disabled} onClick={() => onChange('signin')}>
        Sign in
      </TabButton>
    </div>
  )
}

function TabButton({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean
  disabled: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      disabled={disabled}
      onClick={onClick}
      className={clsx(
        'rounded-full px-4 py-2 text-[14px] font-medium transition-all duration-150 ease-out',
        active ? 'bg-accent text-canvas shadow-sm' : 'text-ink-muted hover:text-ink',
      )}
    >
      {children}
    </button>
  )
}

function FieldCard({
  label,
  hint,
  error,
  children,
  className,
}: {
  label: string
  hint: string
  error?: string
  children: ReactNode
  className?: string
}) {
  return (
    <Card className={clsx('rounded-[24px] border-hairline/80 bg-surface-raised/80 p-5', className)}>
      <label className="block text-[15px] font-medium text-ink">{label}</label>
      <p className="mt-1 text-[13px] leading-6 text-ink-muted">{error ?? hint}</p>
      <div className="mt-4">{children}</div>
    </Card>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-hairline/80 bg-surface-raised px-3 py-3">
      <p className="text-[11px] uppercase tracking-[0.2em] text-ink-muted">{label}</p>
      <p className="mt-1 text-[15px] font-semibold text-ink">{value}</p>
    </div>
  )
}
