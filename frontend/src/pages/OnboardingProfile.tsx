import clsx from 'clsx'
import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { Input, Select } from '../components/ui'
import { ApiError, createUser, getUser } from '../lib/api'
import { useSession } from '../lib/session'
import type { Gender } from '../types'

const USERNAME_RE = /^[a-zA-Z0-9_-]+$/

const GENDER_OPTIONS: { value: Gender; label: string }[] = [
  { value: 'female', label: 'Female' },
  { value: 'male', label: 'Male' },
  { value: 'diverse', label: 'Diverse' },
  { value: 'prefer_not_to_say', label: 'Prefer not to say' },
]

type Mode = 'create' | 'signin'

export default function OnboardingProfile() {
  const navigate = useNavigate()
  const { setUsername } = useSession()

  const [mode, setMode] = useState<Mode>('create')

  const [usernameInput, setUsernameInput] = useState('')
  const [gender, setGender] = useState<Gender | ''>('')
  const [ageInput, setAgeInput] = useState('')

  const [signInUsername, setSignInUsername] = useState('')

  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)

  const switchMode = (next: Mode) => {
    if (next === mode || busy) return
    setMode(next)
    setFooter(null)
  }

  const validateUsername = (value: string): string | null => {
    if (value.length < 1 || value.length > 40) {
      return 'Username must be between 1 and 40 characters.'
    }
    if (!USERNAME_RE.test(value)) {
      return 'Username may only contain letters, numbers, underscore, and hyphen.'
    }
    return null
  }

  const handleCreate = async () => {
    setFooter(null)
    const username = usernameInput.trim()
    const nameErr = validateUsername(username)
    if (nameErr) {
      setFooter(<p className="text-[15px] text-bad">{nameErr}</p>)
      return
    }
    if (!gender) {
      setFooter(<p className="text-[15px] text-bad">Select a gender.</p>)
      return
    }
    const age = Number(ageInput)
    if (!Number.isInteger(age) || age < 16 || age > 99) {
      setFooter(<p className="text-[15px] text-bad">Age must be a whole number between 16 and 99.</p>)
      return
    }

    setBusy(true)
    try {
      await createUser({ username, age, gender })
      setUsername(username)
      navigate('/onboarding/requirements', { replace: false })
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        setFooter(
          <p className="text-[15px] text-bad">
            That username is already taken — try signing in instead.
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
    if (nameErr) {
      setFooter(<p className="text-[15px] text-bad">{nameErr}</p>)
      return
    }

    setBusy(true)
    try {
      const user = await getUser(username)
      if (user === null) {
        setFooter(
          <p className="text-[15px] text-bad">
            No account with that username. Create one instead?
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
  const nextLabel = mode === 'create' ? 'Continue' : 'Sign in'
  const title = mode === 'create' ? 'Tell us a bit about you' : 'Welcome back'

  return (
    <OnboardingShell
      step={1}
      title={title}
      onNext={() => void onNext()}
      busy={busy}
      nextLabel={nextLabel}
      nextDisabled={false}
      footer={footer}
      showProgress={mode === 'create'}
    >
      <div className="space-y-6">
        <ModeTabs mode={mode} onChange={switchMode} disabled={busy} />

        {mode === 'create' ? (
          <div className="space-y-6">
            <div className="space-y-2">
              <label htmlFor="onboarding-username" className="block text-[15px] text-ink">
                Choose a username
              </label>
              <Input
                id="onboarding-username"
                value={usernameInput}
                onChange={(ev) => setUsernameInput(ev.target.value.trim())}
                autoComplete="username"
                maxLength={40}
                aria-invalid={footer !== null}
              />
            </div>
            <div className="space-y-2">
              <label htmlFor="onboarding-gender" className="block text-[15px] text-ink">
                Gender
              </label>
              <Select
                id="onboarding-gender"
                value={gender}
                onChange={(ev) => setGender(ev.target.value as Gender | '')}
                aria-invalid={footer !== null}
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
            </div>
            <div className="space-y-2">
              <label htmlFor="onboarding-age" className="block text-[15px] text-ink">
                Age
              </label>
              <Input
                id="onboarding-age"
                type="number"
                min={16}
                max={99}
                step={1}
                value={ageInput}
                onChange={(ev) => setAgeInput(ev.target.value)}
                aria-invalid={footer !== null}
              />
            </div>
          </div>
        ) : (
          <div className="space-y-6">
            <div className="space-y-2">
              <label htmlFor="signin-username" className="block text-[15px] text-ink">
                Username
              </label>
              <Input
                id="signin-username"
                value={signInUsername}
                onChange={(ev) => setSignInUsername(ev.target.value.trim())}
                autoComplete="username"
                maxLength={40}
                aria-invalid={footer !== null}
                onKeyDown={(ev) => {
                  if (ev.key === 'Enter' && !busy) {
                    ev.preventDefault()
                    void handleSignIn()
                  }
                }}
              />
              <p className="text-[13px] text-ink-muted">
                Enter the username you chose when you created your account.
              </p>
            </div>
          </div>
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
      className="inline-flex rounded border border-hairline bg-surface p-1"
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
        'rounded px-3 h-8 text-[13px] font-medium transition-colors duration-150 ease-out',
        'outline-none focus:ring-2 focus:ring-accent focus:ring-offset-2 focus:ring-offset-canvas',
        active
          ? 'bg-surface-raised text-ink shadow-[inset_0_0_0_1px_var(--hairline)]'
          : 'text-ink-muted hover:text-ink',
        disabled && 'opacity-60 cursor-not-allowed',
      )}
    >
      {children}
    </button>
  )
}
