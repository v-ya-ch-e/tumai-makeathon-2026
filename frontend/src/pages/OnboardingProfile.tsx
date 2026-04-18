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

  const usernamePreview = useMemo(() => usernameInput.trim() || 'room-hunt-2026', [usernameInput])

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
    if (!gender) nextErrors.gender = 'Select the profile value that matches the backend model.'

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
        notificationEmail: notificationEmail || null,
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
      setUsername(username)
      navigate('/', { replace: true })
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
      eyebrow={createMode ? 'Profile' : 'Resume'}
      title={createMode ? 'Create the hunt profile' : 'Open an existing hunt'}
      description={
        createMode
          ? 'Use a local username so this browser can store the search brief, resume hunts, and fetch saved results. Age and gender stay aligned with the WG-Gesucht demo flow.'
          : 'Use the same username you already created on this device. The demo keeps this step intentionally lightweight.'
      }
      onNext={() => void (createMode ? handleCreate() : handleSignIn())}
      busy={busy}
      nextLabel={createMode ? 'Continue to requirements' : 'Open dashboard'}
      footer={footer}
      showProgress={createMode}
      progressSteps={onboardingSteps({
        canAccessRequirements: false,
        canAccessPreferences: false,
        canAccessDashboard: false,
      })}
      aside={
        createMode ? (
          <div className="space-y-4">
            <Card className="panel-muted p-6">
              <p className="section-kicker">Stored locally</p>
              <p className="mt-4 text-[24px] font-semibold text-ink">{usernamePreview}</p>
              <dl className="mt-5 space-y-3">
                <PreviewRow label="Age" value={ageInput || 'Not set'} />
                <PreviewRow
                  label="Gender"
                  value={gender ? GENDER_OPTIONS.find((option) => option.value === gender)?.label ?? 'Not set' : 'Not set'}
                />
              </dl>
            </Card>
            <Card className="panel p-6">
              <p className="text-[15px] font-semibold text-ink">What happens next</p>
              <ul className="mt-3 space-y-2 text-[14px] leading-6 text-ink-muted">
                <li>Define a rent ceiling and the places you need to reach.</li>
                <li>Mark the details that change how listings get ranked.</li>
                <li>Start a hunt and review the agent log beside the ranked results.</li>
              </ul>
            </Card>
          </div>
        ) : (
          <Card className="panel p-6">
            <p className="text-[15px] font-semibold text-ink">Sign-in note</p>
            <p className="mt-3 text-[14px] leading-6 text-ink-muted">
              Usernames are local identifiers for the demo. If you do not see your hunt, create a new profile and continue.
            </p>
          </Card>
        )
      }
    >
      <div className="space-y-6">
        <ModeTabs mode={mode} onChange={switchMode} disabled={busy} />

        <div className="overflow-hidden rounded-card border border-hairline bg-surface-raised">
          {createMode ? (
            <>
              <FieldRow
                label="Username"
                hint="Pick a short local handle. This is how the browser reopens the same hunt later."
                error={errors.username}
              >
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
                  placeholder="Enter your username here"
                />
              </FieldRow>

              <FieldRow
                label="Age"
                hint="Included because many WG listings mention a preferred age range."
                error={errors.age}
              >
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
                  placeholder="Enter your age here"
                />
              </FieldRow>

              <FieldRow
                label="Notification email"
                hint="Optional for now. It becomes useful once alerting is wired up."
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
                  placeholder="Enter your email here"
                />
              </FieldRow>

              <FieldRow
                label="Gender"
                hint="Matches the backend profile field used for the demo."
                error={errors.gender}
              >
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
              </FieldRow>
            </>
          ) : (
            <FieldRow
              label="Username"
              hint="Use the same local username you created earlier."
              error={errors.signInUsername}
            >
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
                placeholder="Enter your username here"
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !busy) {
                    event.preventDefault()
                    void handleSignIn()
                  }
                }}
              />
            </FieldRow>
          )}
        </div>
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
    <div role="tablist" aria-label="Account mode" className="inline-flex rounded border border-hairline bg-surface-raised p-1">
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
        'rounded px-3 py-2 text-[14px] font-medium transition-colors duration-150 ease-out',
        active ? 'bg-surface text-ink' : 'text-ink-muted hover:text-ink',
      )}
    >
      {children}
    </button>
  )
}

function FieldRow({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint: string
  error?: string
  children: ReactNode
}) {
  return (
    <section className="grid gap-3 border-t border-hairline px-5 py-5 first:border-t-0 md:grid-cols-[190px_minmax(0,1fr)] md:gap-6 md:px-6">
      <div>
        <h2 className="text-[15px] font-semibold text-ink">{label}</h2>
        <p className={clsx('mt-1 text-[13px] leading-6', error ? 'text-bad' : 'text-ink-muted')}>{error ?? hint}</p>
      </div>
      <div>{children}</div>
    </section>
  )
}

function PreviewRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-4 border-t border-hairline pt-3 first:border-t-0 first:pt-0">
      <span className="data-label">{label}</span>
      <span className="text-right text-[14px] text-ink">{value}</span>
    </div>
  )
}
