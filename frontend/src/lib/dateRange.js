import {
  endOfMonth,
  endOfWeek,
  endOfYear,
  format,
  parseISO,
  startOfMonth,
  startOfWeek,
  startOfYear,
  subDays,
  subMonths,
  subWeeks,
  subYears,
} from 'date-fns'

const iso = (date) => format(date, 'yyyy-MM-dd')
const WEEK = { weekStartsOn: 1 } // Monday, matching the calendar

const lastDays = (n) => () => {
  const today = new Date()
  return { from: iso(subDays(today, n - 1)), to: iso(today) }
}

/**
 * Quick ranges for the date picker. ``range()`` is evaluated when needed so
 * "Last 7 days" always means "ending today". ``null`` means no date limit.
 * "This …" ranges stop at today; "Last …" ranges are the full previous period.
 */
export const PRESETS = [
  { key: 'today', label: 'Today', range: lastDays(1) },
  {
    key: 'yesterday',
    label: 'Yesterday',
    range: () => {
      const day = iso(subDays(new Date(), 1))
      return { from: day, to: day }
    },
  },
  { key: '7d', label: 'Last 7 Days', range: lastDays(7) },
  { key: '14d', label: 'Last 14 Days', range: lastDays(14) },
  { key: '30d', label: 'Last 30 Days', range: lastDays(30) },
  { key: '365d', label: 'Last 365 Days', range: lastDays(365) },
  {
    key: 'this_week',
    label: 'This Week',
    range: () => ({ from: iso(startOfWeek(new Date(), WEEK)), to: iso(new Date()) }),
  },
  {
    key: 'this_month',
    label: 'This Month',
    range: () => ({ from: iso(startOfMonth(new Date())), to: iso(new Date()) }),
  },
  {
    key: 'this_year',
    label: 'This Year',
    range: () => ({ from: iso(startOfYear(new Date())), to: iso(new Date()) }),
  },
  {
    key: 'last_week',
    label: 'Last Week',
    range: () => {
      const previous = subWeeks(new Date(), 1)
      return { from: iso(startOfWeek(previous, WEEK)), to: iso(endOfWeek(previous, WEEK)) }
    },
  },
  {
    key: 'last_month',
    label: 'Last Month',
    range: () => {
      const previous = subMonths(new Date(), 1)
      return { from: iso(startOfMonth(previous)), to: iso(endOfMonth(previous)) }
    },
  },
  {
    key: 'last_year',
    label: 'Last Year',
    range: () => {
      const previous = subYears(new Date(), 1)
      return { from: iso(startOfYear(previous)), to: iso(endOfYear(previous)) }
    },
  },
  { key: 'all', label: 'All Time', range: () => null },
]

export const presetByKey = (key) => PRESETS.find((p) => p.key === key)

/** The preset that exactly matches the given dates (or "all" when there are none). */
export function matchPreset(from, to) {
  if (!from && !to) return presetByKey('all')
  return PRESETS.find((preset) => {
    const range = preset.range()
    return range && range.from === from && range.to === to
  })
}

const pretty = (value) => (value ? format(parseISO(value), 'd MMM yyyy') : '…')

/** "Last 7 Days", or "1 Sep 2026 → 15 Sep 2026" for a custom range. */
export function rangeLabel(from, to) {
  const preset = matchPreset(from, to)
  if (preset) return preset.label
  if (from && from === to) return pretty(from)
  return `${pretty(from)} → ${pretty(to)}`
}
