import type { ProgressStepLink } from '../components/ui/ProgressSteps'

type OnboardingAccess = {
  canAccessRequirements: boolean
  canAccessPreferences: boolean
}

export function onboardingSteps({
  canAccessRequirements,
  canAccessPreferences,
}: OnboardingAccess): [ProgressStepLink, ProgressStepLink, ProgressStepLink] {
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
  ]
}
