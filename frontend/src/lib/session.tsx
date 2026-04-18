import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { getUser } from './api'
import type { User } from '../types'

const LS_KEY = 'wg-hunter.username'

export type SessionContextValue = {
  username: string | null
  setUsername: (u: string | null) => void
  user: User | null
  refreshUser: () => Promise<void>
  isReady: boolean
}

const SessionContext = createContext<SessionContextValue | null>(null)

export function SessionProvider({ children }: { children: ReactNode }) {
  const [username, setUsernameState] = useState<string | null>(null)
  const [user, setUser] = useState<User | null>(null)
  const [isReady, setIsReady] = useState(false)

  const refreshUser = useCallback(async () => {
    const name = localStorage.getItem(LS_KEY)
    if (!name) {
      setUsernameState(null)
      setUser(null)
      return
    }
    const u = await getUser(name)
    if (u === null) {
      localStorage.removeItem(LS_KEY)
      setUsernameState(null)
      setUser(null)
    } else {
      setUsernameState(name)
      setUser(u)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    void (async () => {
      await refreshUser()
      if (!cancelled) {
        setIsReady(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [refreshUser])

  const setUsername = useCallback(
    (u: string | null) => {
      if (u) {
        localStorage.setItem(LS_KEY, u)
      } else {
        localStorage.removeItem(LS_KEY)
      }
      setUsernameState(u)
      void refreshUser()
    },
    [refreshUser],
  )

  const value = useMemo(
    () => ({
      username,
      setUsername,
      user,
      refreshUser,
      isReady,
    }),
    [username, setUsername, user, refreshUser, isReady],
  )

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext)
  if (!ctx) {
    throw new Error('useSession must be used within SessionProvider')
  }
  return ctx
}
