import { useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { OnboardingShell } from '../components/OnboardingShell'
import { Input, Select } from '../components/ui'
import { ApiError, createUser } from '../lib/api'
import { useSession } from '../lib/session'
import type { Gender } from '../types'

const USERNAME_RE = /^[a-zA-Z0-9_-]+$/

const GENDER_OPTIONS: { value: Gender; label: string }[] = [
  { value: 'female', label: 'Female' },
  { value: 'male', label: 'Male' },
  { value: 'diverse', label: 'Diverse' },
  { value: 'prefer_not_to_say', label: 'Prefer not to say' },
]

export default function OnboardingProfile() {
  const navigate = useNavigate()
  const { setUsername } = useSession()
  const [usernameInput, setUsernameInput] = useState('')
  const [gender, setGender] = useState<Gender | ''>('')
  const [ageInput, setAgeInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [footer, setFooter] = useState<ReactNode>(null)

  const handleNext = async () => {
    setFooter(null)
    const username = usernameInput.trim()
    if (username.length < 1 || username.length > 40) {
      setFooter(<p className="text-[15px] text-bad">Username must be between 1 and 40 characters.</p>)
      return
    }
    if (!USERNAME_RE.test(username)) {
      setFooter(
        <p className="text-[15px] text-bad">
          Username may only contain letters, numbers, underscore, and hyphen.
        </p>,
      )
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
            That username is already taken — pick another.
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

  return (
    <OnboardingShell
      step={1}
      title="Tell us a bit about you"
      onNext={() => void handleNext()}
      busy={busy}
      nextDisabled={false}
      footer={footer}
    >
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
    </OnboardingShell>
  )
}
