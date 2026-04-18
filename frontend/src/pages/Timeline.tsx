import { useEffect, useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { AppNav } from '../components/AppNav'
import { Card, Chip, StatusPill, type StatusPillTone } from '../components/ui'
import { ApiError, getTimelineItems } from '../lib/api'
import { useSession } from '../lib/session'
import type { TimelineCategory, TimelineItem, UrgencyLevel } from '../types'

type FilterValue = 'all' | TimelineCategory

const FILTERS: Array<{ value: FilterValue; label: string }> = [
  { value: 'all', label: 'All' },
  { value: 'deadline', label: 'Deadlines' },
  { value: 'course', label: 'Courses' },
  { value: 'sport', label: 'Sports' },
  { value: 'event', label: 'Events' },
]

function startOfDay(value: Date): Date {
  const next = new Date(value)
  next.setHours(0, 0, 0, 0)
  return next
}

function daysUntil(isoDate: string): number {
  const today = startOfDay(new Date())
  const itemDate = startOfDay(new Date(isoDate))
  return Math.round((itemDate.getTime() - today.getTime()) / 86400000)
}

function formatTimelineDate(isoDate: string): string {
  return new Intl.DateTimeFormat('en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(new Date(isoDate))
}

function urgencyTone(urgency: UrgencyLevel): StatusPillTone {
  if (urgency === 'high') return 'bad'
  if (urgency === 'medium') return 'warn'
  return 'idle'
}

function urgencyLabel(urgency: UrgencyLevel): string {
  if (urgency === 'high') return 'Urgent'
  if (urgency === 'medium') return 'Soon'
  return 'Planned'
}

function categoryLabel(category: TimelineCategory): string {
  if (category === 'deadline') return 'Deadline'
  if (category === 'course') return 'Course'
  if (category === 'sport') return 'Sport'
  return 'Event'
}

function actionText(item: TimelineItem): string {
  if (item.category === 'deadline') {
    return item.urgency === 'high' ? 'Submit or review now.' : 'Check the submission details.'
  }
  if (item.category === 'course') {
    return item.title.toLowerCase().includes('registration')
      ? 'Open TUMonline and review the registration window.'
      : 'Review the course event details.'
  }
  if (item.category === 'sport') {
    return 'Open the ZHS page and check the registration status.'
  }
  return 'Save it to your calendar and share it if needed.'
}

export default function TimelinePage() {
  const { username, isReady } = useSession()
  const [allItems, setAllItems] = useState<TimelineItem[]>([])
  const [visibleItems, setVisibleItems] = useState<TimelineItem[]>([])
  const [activeFilter, setActiveFilter] = useState<FilterValue>('all')
  const [loading, setLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    if (!isReady || !username) return
    let cancelled = false
    void (async () => {
      try {
        const items = await getTimelineItems()
        if (!cancelled) {
          setAllItems(items)
        }
      } catch (err) {
        if (!cancelled) {
          setErrorMessage(err instanceof ApiError ? err.message : String(err))
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [isReady, username])

  useEffect(() => {
    if (!isReady || !username) return
    let cancelled = false
    setLoading(true)
    void (async () => {
      try {
        const items = await getTimelineItems(activeFilter === 'all' ? undefined : activeFilter)
        if (!cancelled) {
          setVisibleItems(items)
          setErrorMessage(null)
        }
      } catch (err) {
        if (!cancelled) {
          setErrorMessage(err instanceof ApiError ? err.message : String(err))
          setVisibleItems([])
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [activeFilter, isReady, username])

  const summary = useMemo(() => {
    const urgentNow = allItems.filter((item) => item.urgency === 'high').length
    const thisWeek = allItems.filter((item) => {
      const days = daysUntil(item.date)
      return days >= 0 && days <= 7
    }).length
    const sportsOpenings = allItems.filter((item) => {
      const days = daysUntil(item.date)
      return item.category === 'sport' && days >= 0 && days <= 7
    }).length
    return { urgentNow, thisWeek, sportsOpenings }
  }, [allItems])

  if (!isReady) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas font-sans text-[15px] text-ink-muted">
        Loading…
      </div>
    )
  }

  if (!username) {
    return <Navigate to="/onboarding/profile" replace />
  }

  return (
    <div className="min-h-screen bg-canvas">
      <header className="border-b border-hairline bg-surface">
        <div className="mx-auto max-w-6xl px-12 py-6">
          <div className="space-y-1">
            <h1 className="font-sans text-[22px] font-semibold tracking-tight text-ink">
              Student timeline
            </h1>
            <p className="text-[14px] text-ink-muted">
              One place for deadlines, registrations, and campus events.
            </p>
          </div>
          <div className="mt-4">
            <AppNav />
          </div>
          {errorMessage ? (
            <div className="pt-4 text-[13px] text-bad">{errorMessage}</div>
          ) : null}
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-8 px-12 py-12">
        <section className="grid gap-4 md:grid-cols-3">
          <Card className="space-y-2">
            <p className="text-[13px] uppercase tracking-[0.16em] text-ink-muted">Urgent now</p>
            <p className="font-sans text-[32px] font-semibold tracking-tight text-ink">
              {summary.urgentNow}
            </p>
            <p className="text-[14px] text-ink-muted">Items that need attention immediately.</p>
          </Card>
          <Card className="space-y-2">
            <p className="text-[13px] uppercase tracking-[0.16em] text-ink-muted">This week</p>
            <p className="font-sans text-[32px] font-semibold tracking-tight text-ink">
              {summary.thisWeek}
            </p>
            <p className="text-[14px] text-ink-muted">Everything landing in the next seven days.</p>
          </Card>
          <Card className="space-y-2">
            <p className="text-[13px] uppercase tracking-[0.16em] text-ink-muted">Sports openings</p>
            <p className="font-sans text-[32px] font-semibold tracking-tight text-ink">
              {summary.sportsOpenings}
            </p>
            <p className="text-[14px] text-ink-muted">ZHS items worth checking this week.</p>
          </Card>
        </section>

        <section className="space-y-4">
          <div className="flex flex-wrap gap-2">
            {FILTERS.map((filter) => (
              <Chip
                key={filter.value}
                selected={activeFilter === filter.value}
                onToggle={() => setActiveFilter(filter.value)}
              >
                {filter.label}
              </Chip>
            ))}
          </div>

          {loading ? (
            <Card>
              <p className="text-[15px] text-ink-muted">Loading timeline…</p>
            </Card>
          ) : visibleItems.length === 0 ? (
            <Card>
              <p className="text-[15px] text-ink-muted">No timeline items for this filter.</p>
            </Card>
          ) : (
            <div className="space-y-4">
              {visibleItems.map((item) => (
                <Card key={`${item.source}-${item.title}-${item.date}`} className="space-y-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="space-y-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <StatusPill tone={urgencyTone(item.urgency)}>
                          {urgencyLabel(item.urgency)}
                        </StatusPill>
                        <span className="inline-flex h-7 items-center rounded-full border border-hairline px-3 text-[13px] text-ink-muted">
                          {categoryLabel(item.category)}
                        </span>
                      </div>
                      <h2 className="font-sans text-[20px] font-semibold tracking-tight text-ink">
                        {item.title}
                      </h2>
                    </div>
                    <div className="space-y-1 text-left sm:text-right">
                      <p className="text-[13px] uppercase tracking-[0.16em] text-ink-muted">
                        Date/time
                      </p>
                      <p className="text-[15px] font-medium text-ink">{formatTimelineDate(item.date)}</p>
                    </div>
                  </div>

                  <div className="grid gap-4 border-t border-hairline pt-4 sm:grid-cols-2 lg:grid-cols-3">
                    <div>
                      <p className="text-[13px] uppercase tracking-[0.16em] text-ink-muted">Source</p>
                      <p className="mt-1 text-[15px] text-ink">{item.source}</p>
                    </div>
                    <div>
                      <p className="text-[13px] uppercase tracking-[0.16em] text-ink-muted">Category</p>
                      <p className="mt-1 text-[15px] text-ink">{categoryLabel(item.category)}</p>
                    </div>
                    <div>
                      <p className="text-[13px] uppercase tracking-[0.16em] text-ink-muted">Action</p>
                      <p className="mt-1 text-[15px] text-ink">{actionText(item)}</p>
                    </div>
                  </div>
                </Card>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  )
}
