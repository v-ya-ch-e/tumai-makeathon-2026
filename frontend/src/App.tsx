import { APIProvider } from '@vis.gl/react-google-maps'
import type { ReactNode } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { SessionProvider, useSession } from './lib/session'
import Dashboard from './pages/Dashboard'
import HealthPage from './pages/Health'
import OnboardingPreferences from './pages/OnboardingPreferences'
import OnboardingProfile from './pages/OnboardingProfile'
import OnboardingRequirements from './pages/OnboardingRequirements'

const GOOGLE_MAPS_API_KEY = import.meta.env.VITE_GOOGLE_MAPS_API_KEY

function MapsProvider({ children }: { children: ReactNode }) {
  if (!GOOGLE_MAPS_API_KEY) {
    return <>{children}</>
  }
  return (
    <APIProvider apiKey={GOOGLE_MAPS_API_KEY} libraries={['places']}>
      {children}
    </APIProvider>
  )
}

function HomeRedirect() {
  const { username, user, isReady } = useSession()
  if (!isReady) {
    return (
      <p className="flex min-h-screen items-center justify-center font-sans text-[15px] text-ink-muted">
        Loading…
      </p>
    )
  }
  if (username && user) {
    return <Navigate to="/dashboard" replace />
  }
  return <Navigate to="/onboarding/profile" replace />
}

export default function App() {
  return (
    <MapsProvider>
      <SessionProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<HomeRedirect />} />
            <Route path="/health" element={<HealthPage />} />
            <Route path="/onboarding/profile" element={<OnboardingProfile />} />
            <Route path="/onboarding/requirements" element={<OnboardingRequirements />} />
            <Route path="/onboarding/preferences" element={<OnboardingPreferences />} />
            <Route path="/dashboard" element={<Dashboard />} />
          </Routes>
        </BrowserRouter>
      </SessionProvider>
    </MapsProvider>
  )
}
