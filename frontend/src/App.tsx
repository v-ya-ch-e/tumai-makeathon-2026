import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { SessionProvider, useSession } from './lib/session'
import HealthPage from './pages/Health'
import OnboardingPreferences from './pages/OnboardingPreferences'
import OnboardingProfile from './pages/OnboardingProfile'
import OnboardingRequirements from './pages/OnboardingRequirements'

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

function DashboardPlaceholder() {
  return <div className="min-h-screen bg-canvas" />
}

export default function App() {
  return (
    <SessionProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<HomeRedirect />} />
          <Route path="/health" element={<HealthPage />} />
          <Route path="/onboarding/profile" element={<OnboardingProfile />} />
          <Route path="/onboarding/requirements" element={<OnboardingRequirements />} />
          <Route path="/onboarding/preferences" element={<OnboardingPreferences />} />
          <Route path="/dashboard" element={<DashboardPlaceholder />} />
        </Routes>
      </BrowserRouter>
    </SessionProvider>
  )
}
