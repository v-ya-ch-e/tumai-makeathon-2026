import clsx from 'clsx'
import { NavLink } from 'react-router-dom'

const NAV_ITEMS = [
  { to: '/dashboard', label: 'Dashboard' },
  { to: '/timeline', label: 'Timeline' },
]

export function AppNav() {
  return (
    <nav aria-label="Primary" className="flex flex-wrap gap-2">
      {NAV_ITEMS.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          className={({ isActive }) =>
            clsx(
              'inline-flex h-8 items-center rounded-full border px-3 text-[13px] transition-colors duration-150 ease-out',
              isActive
                ? 'border-accent bg-accent-muted text-ink'
                : 'border-hairline bg-surface text-ink hover:bg-surface-raised',
            )
          }
        >
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}
