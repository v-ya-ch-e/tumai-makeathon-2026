const DATE_ONLY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/

export function formatGermanDate(value: string | null, fallback = 'Flexible'): string {
  if (!value) return fallback
  const match = DATE_ONLY_PATTERN.exec(value)
  if (match) {
    const [, year, month, day] = match
    return `${day}.${month}.${year}`
  }
  return value
}

export function formatGermanDateRange(from: string | null, to: string | null, fallback = '—'): string {
  if (from && to) return `${formatGermanDate(from, fallback)} – ${formatGermanDate(to, fallback)}`
  if (from) return `From ${formatGermanDate(from, fallback)}`
  if (to) return `Until ${formatGermanDate(to, fallback)}`
  return fallback
}
