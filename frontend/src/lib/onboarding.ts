import type { ProgressStepLink } from '../components/ui/ProgressSteps'

type OnboardingAccess = {
  canAccessRequirements: boolean
  canAccessPreferences: boolean
  canAccessDashboard: boolean
}

export function onboardingSteps({
  canAccessRequirements,
  canAccessPreferences,
  canAccessDashboard,
}: OnboardingAccess): [ProgressStepLink, ProgressStepLink, ProgressStepLink, ProgressStepLink] {
  return [
    { label: 'Profile', href: '/onboarding/profile' },
    {
      label: 'Requirements',
      href: canAccessRequirements ? '/onboarding/requirements' : undefined,
      disabled: !canAccessRequirements,
    },
    {
      label: 'Preferences',
      href: canAccessPreferences ? '/onboarding/preferences' : undefined,
      disabled: !canAccessPreferences,
    },
    {
      label: 'Dashboard',
      href: canAccessDashboard ? '/dashboard' : undefined,
      disabled: !canAccessDashboard,
    },
  ]
}
