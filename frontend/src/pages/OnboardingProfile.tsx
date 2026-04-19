import clsx from 'clsx'
import { useState, type ReactNode, type SVGProps } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { Input, Select } from '../components/ui'
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
  const { setUsername, setSession } = useSession()

  const [mode, setMode] = useState<Mode>('create')
  const [usernameInput, setUsernameInput] = useState('')
  const [gender, setGender] = useState<Gender | ''>('')
  const [ageInput, setAgeInput] = useState('')
  const [notificationEmailInput, setNotificationEmailInput] = useState('')
  const [signInUsername, setSignInUsername] = useState('')
  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)
  const [errors, setErrors] = useState<FieldErrors>({})

  const switchMode = (nextMode: Mode) => {
    if (nextMode === mode || busy) return
    setMode(nextMode)
    setFooter(null)
    setErrors({})
  }

  const validateUsername = (value: string): string | null => {
    if (value.length < 1 || value.length > 40) return 'Username must be between 1 and 40 characters.'
    if (!USERNAME_RE.test(value)) return 'Use only letters, numbers, underscores, and hyphens.'
    return null
  }

  const handleCreate = async () => {
    setFooter(null)
    const username = usernameInput.trim()
    const nextErrors: FieldErrors = {}

    const nameError = validateUsername(username)
    if (nameError) nextErrors.username = nameError
    if (!gender) nextErrors.gender = 'Select your gender.'

    const age = Number(ageInput)
    if (!Number.isInteger(age) || age < 16 || age > 99) {
      nextErrors.age = 'Age must be a whole number between 16 and 99.'
    }

    const notificationEmail = notificationEmailInput.trim()
    if (notificationEmail && !EMAIL_RE.test(notificationEmail)) {
      nextErrors.notificationEmail = 'Enter a valid email address or leave it blank.'
    }

    setErrors(nextErrors)
    if (Object.keys(nextErrors).length > 0) return

    setBusy(true)
    try {
      await createUser({
        username,
        age,
        gender: gender as Gender,
        email: notificationEmail || null,
      })
      setUsername(username)
      navigate('/onboarding/requirements', { replace: false })
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setFooter(<p className="text-[15px] text-bad">That username already exists. Sign in or choose another one.</p>)
      } else if (error instanceof ApiError) {
        setFooter(<p className="text-[15px] text-bad">{error.message}</p>)
      } else {
        setFooter(<p className="text-[15px] text-bad">{String(error)}</p>)
      }
    } finally {
      setBusy(false)
    }
  }

  const handleSignIn = async () => {
    setFooter(null)
    const username = signInUsername.trim()
    const nameError = validateUsername(username)
    const nextErrors: FieldErrors = nameError ? { signInUsername: nameError } : {}
    setErrors(nextErrors)
    if (nameError) return

    setBusy(true)
    try {
      const user = await getUser(username)
      if (user === null) {
        setFooter(<p className="text-[15px] text-bad">No saved profile with that username exists yet.</p>)
        return
      }
      setSession(username, user)
      // Navigate straight to /dashboard instead of going through HomeRedirect
      // at '/'. React Router 7 re-renders the matched route synchronously from
      // its external history store, which can out-race the React state commit
      // from setSession. If HomeRedirect reads the still-null session it
      // bounces us right back to /onboarding/profile, which is why sign-in
      // appeared to only succeed on the second attempt.
      navigate('/dashboard', { replace: true })
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

  const createMode = mode === 'create'

  return (
    <OnboardingShell
      step={1}
      eyebrow="Profile"
      title={createMode ? 'Create your profile' : 'Welcome back'}
      description={
        createMode
          ? 'Tell us about yourself to find the best options.'
          : 'Use the same username you created earlier to reopen your saved search.'
      }
      onNext={() => void (createMode ? handleCreate() : handleSignIn())}
      busy={busy}
      nextLabel={createMode ? 'Continue to requirements' : 'Open results'}
      footer={footer}
      showProgress={createMode}
      progressSteps={onboardingSteps({
        canAccessRequirements: false,
        canAccessPreferences: false,
      })}
      intro={createMode ? <FeatureStrip /> : null}
    >
      <div className="space-y-6">
        <ModeTabs mode={mode} onChange={switchMode} disabled={busy} />

        {createMode ? (
          <div className="space-y-5">
            <Field label="Username" error={errors.username}>
              <Input
                id="onboarding-username"
                value={usernameInput}
                onChange={(event) => {
                  setUsernameInput(event.target.value)
                  if (errors.username) setErrors((prev) => ({ ...prev, username: undefined }))
                }}
                autoComplete="username"
                maxLength={40}
                aria-invalid={Boolean(errors.username)}
                placeholder="e.g. alex_b"
              />
            </Field>

            <div className="grid gap-5 md:grid-cols-2">
              <Field label="Age" error={errors.age}>
                <Input
                  id="onboarding-age"
                  type="number"
                  min={16}
                  max={99}
                  step={1}
                  value={ageInput}
                  onChange={(event) => {
                    setAgeInput(event.target.value)
                    if (errors.age) setErrors((prev) => ({ ...prev, age: undefined }))
                  }}
                  aria-invalid={Boolean(errors.age)}
                  placeholder="28"
                />
              </Field>

              <Field label="Gender" error={errors.gender}>
                <Select
                  id="onboarding-gender"
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

            <Field
              label={
                <>
                  Email <span className="font-normal text-ink-muted">(optional)</span>
                </>
              }
              error={errors.notificationEmail}
            >
              <Input
                id="onboarding-notification-email"
                type="email"
                value={notificationEmailInput}
                onChange={(event) => {
                  setNotificationEmailInput(event.target.value)
                  if (errors.notificationEmail) {
                    setErrors((prev) => ({ ...prev, notificationEmail: undefined }))
                  }
                }}
                autoComplete="email"
                aria-invalid={Boolean(errors.notificationEmail)}
                placeholder="you@example.com"
              />
            </Field>
          </div>
        ) : (
          <Field label="Username" error={errors.signInUsername}>
            <Input
              id="signin-username"
              value={signInUsername}
              onChange={(event) => {
                setSignInUsername(event.target.value)
                if (errors.signInUsername) {
                  setErrors((prev) => ({ ...prev, signInUsername: undefined }))
                }
              }}
              autoComplete="username"
              maxLength={40}
              aria-invalid={Boolean(errors.signInUsername)}
              placeholder="Enter your username"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !busy) {
                  event.preventDefault()
                  void handleSignIn()
                }
              }}
            />
          </Field>
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
  onChange: (nextMode: Mode) => void
  disabled: boolean
}) {
  return (
    <div
      role="tablist"
      aria-label="Account mode"
      className="inline-flex rounded-full bg-surface-raised p-1"
    >
      <ModeTabButton active={mode === 'create'} disabled={disabled} onClick={() => onChange('create')}>
        Create profile
      </ModeTabButton>
      <ModeTabButton active={mode === 'signin'} disabled={disabled} onClick={() => onChange('signin')}>
        Sign in
      </ModeTabButton>
    </div>
  )
}

function ModeTabButton({
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
        'rounded-full px-4 py-2 text-[14px] font-medium transition-colors duration-150 ease-out',
        active ? 'bg-surface text-ink shadow-sm' : 'text-ink-muted hover:text-ink',
      )}
    >
      {children}
    </button>
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

function FeatureStrip() {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      <FeatureCard
        icon={<SearchIcon />}
        title="Find"
        body="Listings that match your budget and area."
      />
      <FeatureCard
        icon={<ListCheckIcon />}
        title="Rank"
        body="Best-fit results surface first."
      />
      <FeatureCard
        icon={<SparkleIcon />}
        title="Refresh"
        body="Your shortlist stays up to date."
      />
    </div>
  )
}

function FeatureCard({
  icon,
  title,
  body,
}: {
  icon: ReactNode
  title: string
  body: string
}) {
  return (
    <div className="flex items-start gap-3 rounded-card border border-hairline bg-surface px-4 py-4">
      <span
        aria-hidden
        className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-accent-muted text-accent"
      >
        {icon}
      </span>
      <div>
        <p className="text-[15px] font-semibold text-ink">{title}</p>
        <p className="mt-0.5 text-[13px] leading-5 text-ink-muted">{body}</p>
      </div>
    </div>
  )
}

function iconProps(props: SVGProps<SVGSVGElement>) {
  return {
    viewBox: '0 0 24 24',
    width: 18,
    height: 18,
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.7,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    ...props,
  }
}

function SearchIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <circle cx="11" cy="11" r="6" />
      <path d="M20 20l-3.5-3.5" />
    </svg>
  )
}

function ListCheckIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg {...iconProps(props)}>
      <path d="M9 6h11" />
      <path d="M9 12h11" />
      <path d="M9 18h11" />
      <path d="M4 6l1.5 1.5L7 6" />
      <path d="M4 12l1.5 1.5L7 12" />
      <path d="M4 18l1.5 1.5L7 18" />
    </svg>
  )
}

function SparkleIcon(props: SVGProps<SVGSVGElement>) {
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
