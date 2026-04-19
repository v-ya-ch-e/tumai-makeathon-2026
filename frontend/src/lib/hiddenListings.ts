import { useCallback, useEffect, useMemo, useState } from 'react'

/** Per-user persisted set of listing IDs the user has chosen to hide from
 * their dashboard. Hiding is frontend-only: we do not round-trip to the
 * backend (listings reappear for other users and survive a DB reset). */

function storageKey(username: string | null): string | null {
  if (!username) return null
  return `wg-hunter.hidden-listings.${username}`
}

function read(username: string | null): Set<string> {
  const key = storageKey(username)
  if (!key || typeof window === 'undefined') return new Set()
  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) return new Set()
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return new Set()
    return new Set(parsed.filter((value): value is string => typeof value === 'string'))
  } catch {
    return new Set()
  }
}

function write(username: string | null, ids: Set<string>): void {
  const key = storageKey(username)
  if (!key || typeof window === 'undefined') return
  try {
    window.localStorage.setItem(key, JSON.stringify([...ids]))
  } catch {
    // Ignore quota / disabled-storage errors; hide state is best-effort.
  }
}

export type HiddenListingsApi = {
  hiddenIds: Set<string>
  isHidden: (id: string) => boolean
  toggle: (id: string) => void
}

export function useHiddenListings(username: string | null): HiddenListingsApi {
  const [hiddenIds, setHiddenIds] = useState<Set<string>>(() => read(username))

  useEffect(() => {
    setHiddenIds(read(username))
  }, [username])

  const isHidden = useCallback((id: string) => hiddenIds.has(id), [hiddenIds])

  const toggle = useCallback(
    (id: string) => {
      setHiddenIds((prev) => {
        const next = new Set(prev)
        if (next.has(id)) next.delete(id)
        else next.add(id)
        write(username, next)
        return next
      })
    },
    [username],
  )

  return useMemo(() => ({ hiddenIds, isHidden, toggle }), [hiddenIds, isHidden, toggle])
}
