import clsx from 'clsx'
import { Link } from 'react-router-dom'

type Tab = {
  label: string
  href: string
}

type AppTabsProps = {
  current: string
  tabs: Tab[]
}

export function AppTabs({ current, tabs }: AppTabsProps) {
  return (
    <div className="inline-flex rounded-full border border-hairline/80 bg-surface-raised p-1">
      {tabs.map((tab) => {
        const active = tab.href === current
        return (
          <Link
            key={tab.href}
            to={tab.href}
            className={clsx(
              'rounded-full px-4 py-2 text-[13px] font-medium transition-colors',
              active ? 'bg-ink text-white' : 'text-ink-muted hover:text-ink',
            )}
          >
            {tab.label}
          </Link>
        )
      })}
    </div>
  )
}
