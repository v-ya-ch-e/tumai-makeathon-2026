import { useEffect, useMemo, useState } from 'react'
import { Navigate } from 'react-router-dom'
import { AppTabs } from '../components/AppTabs'
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
  return new Intl.DateTimeFormat([], {
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
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error instanceof ApiError ? error.message : String(error))
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
      } catch (error) {
        if (!cancelled) {
          setErrorMessage(error instanceof ApiError ? error.message : String(error))
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
    <div className="relative min-h-screen overflow-hidden bg-canvas">
      <div className="relative mx-auto max-w-7xl px-5 py-5 sm:px-8 lg:px-10">
        <section className="overflow-hidden rounded-[34px] border border-hairline/80 bg-surface/95 shadow-[0_30px_80px_rgba(15,23,42,0.08)]">
          <div className="grid gap-6 border-b border-hairline/80 px-6 py-6 lg:grid-cols-[minmax(0,1.2fr)_auto] lg:px-8 xl:px-10">
            <div>
              <p className="font-mono text-[12px] uppercase tracking-[0.28em] text-accent">Timeline hub</p>
              <h1 className="mt-3 text-[30px] font-semibold tracking-[-0.035em] text-ink sm:text-[38px]">
                Student timeline
              </h1>
              <p className="mt-3 max-w-2xl text-[15px] leading-7 text-ink-muted">
                Track deadlines, registrations, course sessions, and campus events in one place.
              </p>
            </div>
            <div className="flex items-start justify-start lg:justify-end">
              <AppTabs
                current="/timeline"
                tabs={[
                  { label: 'Dashboard', href: '/dashboard' },
                  { label: 'Timeline', href: '/timeline' },
                  { label: 'Profile', href: '/profile' },
                ]}
              />
            </div>
          </div>

          <div className="grid gap-4 px-6 py-6 sm:grid-cols-2 xl:grid-cols-3 xl:px-10">
            <SummaryCard
              label="Urgent now"
              value={String(summary.urgentNow)}
              note="Items that need attention immediately."
            />
            <SummaryCard
              label="This week"
              value={String(summary.thisWeek)}
              note="Everything landing in the next seven days."
            />
            <SummaryCard
              label="Sports openings"
              value={String(summary.sportsOpenings)}
              note="ZHS items worth checking this week."
            />
          </div>

          <div className="border-t border-hairline/80 px-6 py-6 lg:px-8 xl:px-10">
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

            {errorMessage ? (
              <p className="mt-4 rounded-2xl border border-bad/30 bg-bad/5 px-4 py-3 text-[13px] text-bad">
                {errorMessage}
              </p>
            ) : null}

            <div className="mt-6 space-y-4">
              {loading ? (
                <Card className="rounded-[28px] p-6">
                  <p className="text-[15px] text-ink-muted">Loading timeline…</p>
                </Card>
              ) : visibleItems.length === 0 ? (
                <Card className="rounded-[28px] p-6">
                  <p className="text-[15px] text-ink-muted">No timeline items for this filter.</p>
                </Card>
              ) : (
                visibleItems.map((item) => (
                  <Card
                    key={`${item.source}-${item.title}-${item.date}`}
                    className="rounded-[28px] border-hairline/80 bg-surface-raised/85 p-6"
                  >
                    <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                      <div className="space-y-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusPill tone={urgencyTone(item.urgency)}>
                            {urgencyLabel(item.urgency)}
                          </StatusPill>
                          <Chip selected onToggle={() => undefined} className="pointer-events-none">
                            {categoryLabel(item.category)}
                          </Chip>
                        </div>
                        <h2 className="text-[24px] font-semibold tracking-[-0.03em] text-ink">
                          {item.title}
                        </h2>
                        <p className="max-w-2xl text-[15px] leading-7 text-ink-muted">
                          {actionText(item)}
                        </p>
                      </div>
                      <div className="min-w-[180px] rounded-[22px] border border-hairline/80 bg-surface px-4 py-4">
                        <p className="text-[12px] uppercase tracking-[0.16em] text-ink-muted">Date / time</p>
                        <p className="mt-2 text-[16px] font-semibold text-ink">{formatTimelineDate(item.date)}</p>
                        <p className="mt-3 text-[12px] uppercase tracking-[0.16em] text-ink-muted">Source</p>
                        <p className="mt-2 text-[15px] text-ink">{item.source}</p>
                      </div>
                    </div>
                  </Card>
                ))
              )}
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}

function SummaryCard({
  label,
  value,
  note,
}: {
  label: string
  value: string
  note: string
}) {
  return (
    <div className="rounded-[24px] border border-hairline/80 bg-surface-raised/90 px-5 py-5 shadow-[0_16px_32px_rgba(39,33,29,0.04)]">
      <p className="text-[12px] uppercase tracking-[0.16em] text-ink-muted">{label}</p>
      <p className="mt-3 text-[28px] font-semibold tracking-[-0.03em] text-ink">{value}</p>
      <p className="mt-1 text-[13px] text-ink-muted">{note}</p>
    </div>
  )
}
