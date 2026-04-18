import { useState, type FormEvent, type ReactNode } from 'react'
import { Button, Drawer, Input } from './ui'
import { ApiError, putCredentials } from '../lib/api'

export type ConnectWGDialogProps = {
  open: boolean
  username: string
  onClose: () => void
  onSaved: () => void
}

export function ConnectWGDialog({ open, username, onClose, onSaved }: ConnectWGDialogProps) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [storageStateText, setStorageStateText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<ReactNode>(null)

  const reset = () => {
    setEmail('')
    setPassword('')
    setStorageStateText('')
    setError(null)
  }

  const handleClose = () => {
    if (busy) return
    reset()
    onClose()
  }

  const submitEmailPassword = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    if (!email.trim() || !password) {
      setError(<p className="text-[13px] text-bad">Both email and password are required.</p>)
      return
    }
    setBusy(true)
    try {
      await putCredentials(username, { email: email.trim(), password })
      reset()
      onSaved()
    } catch (err) {
      setError(
        <p className="text-[13px] text-bad">
          {err instanceof ApiError ? err.message : String(err)}
        </p>,
      )
    } finally {
      setBusy(false)
    }
  }

  const submitStorageState = async (e: FormEvent) => {
    e.preventDefault()
    setError(null)
    let parsed: unknown
    try {
      parsed = JSON.parse(storageStateText)
    } catch {
      setError(<p className="text-[13px] text-bad">That doesn't look like valid JSON.</p>)
      return
    }
    if (typeof parsed !== 'object' || parsed === null) {
      setError(<p className="text-[13px] text-bad">Expected a JSON object from Playwright's storage_state().</p>)
      return
    }
    setBusy(true)
    try {
      await putCredentials(username, { storageState: parsed as object })
      reset()
      onSaved()
    } catch (err) {
      setError(
        <p className="text-[13px] text-bad">
          {err instanceof ApiError ? err.message : String(err)}
        </p>,
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer open={open} onClose={handleClose} title="Connect wg-gesucht">
      <div className="space-y-8">
        <p className="text-[14px] text-ink-muted">
          Credentials are stored encrypted on your own machine. The agent uses them only when the
          site requires login; most listings are readable without an account. In v1 the agent
          always runs anonymously, but you can connect now so it's ready for the next increment.
        </p>

        <form className="space-y-4" onSubmit={submitEmailPassword}>
          <h3 className="text-[15px] font-semibold text-ink">Email &amp; password</h3>
          <div className="space-y-2">
            <label htmlFor="wg-email" className="block text-[13px] text-ink-muted">
              wg-gesucht email
            </label>
            <Input
              id="wg-email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <label htmlFor="wg-password" className="block text-[13px] text-ink-muted">
              Password
            </label>
            <Input
              id="wg-password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <Button type="submit" variant="primary" disabled={busy}>
            {busy ? 'Saving…' : 'Save credentials'}
          </Button>
        </form>

        <div className="border-t border-hairline pt-6">
          <form className="space-y-4" onSubmit={submitStorageState}>
            <h3 className="text-[15px] font-semibold text-ink">Or paste storage_state.json</h3>
            <p className="text-[13px] text-ink-muted">
              If you saved a Playwright session already, drop the JSON here. Safer than password
              login — avoids CAPTCHAs.
            </p>
            <textarea
              aria-label="storage_state JSON"
              value={storageStateText}
              onChange={(e) => setStorageStateText(e.target.value)}
              placeholder='{"cookies": [...], "origins": [...]}'
              rows={8}
              className="w-full rounded border border-hairline bg-surface-raised px-3 py-2 font-mono text-[13px] text-ink placeholder:text-ink-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent"
            />
            <Button type="submit" variant="secondary" disabled={busy || !storageStateText.trim()}>
              {busy ? 'Saving…' : 'Save storage state'}
            </Button>
          </form>
        </div>

        {error}
      </div>
    </Drawer>
  )
}
