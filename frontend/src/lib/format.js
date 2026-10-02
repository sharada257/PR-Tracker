import { format, formatDistanceToNowStrict, isValid, parseISO } from 'date-fns'
import { rangeLabel } from '@/lib/dateRange'

function toDate(value) {
  if (!value) return null
  const date = typeof value === 'string' ? parseISO(value) : value
  return isValid(date) ? date : null
}

export function formatDate(value) {
  const date = toDate(value)
  return date ? format(date, 'MMM d, yyyy') : '—'
}

export function formatDateTime(value) {
  const date = toDate(value)
  return date ? format(date, 'MMM d, h:mm a') : '—'
}

export function formatDay(value) {
  const date = toDate(value)
  return date ? format(date, 'MMM d, yyyy') : '—'
}

export function timeAgo(value) {
  const date = toDate(value)
  return date ? `${formatDistanceToNowStrict(date)} ago` : '—'
}

/** 0.5 -> "30m", 5 -> "5h", 54 -> "2.3d" */
export function formatDuration(hours) {
  if (hours === null || hours === undefined) return '—'
  if (hours < 1) return `${Math.max(Math.round(hours * 60), 1)}m`
  if (hours < 48) return `${hours < 10 ? Number(hours.toFixed(1)) : Math.round(hours)}h`
  return `${Number((hours / 24).toFixed(1))}d`
}

export function formatNumber(value) {
  if (value === null || value === undefined) return '—'
  return Number.isInteger(value) ? String(value) : value.toFixed(1)
}

export const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]
export const MONTHS_SHORT = MONTHS.map((m) => m.slice(0, 3))

export function periodLabel(filters) {
  return rangeLabel(filters.date_from, filters.date_to)
}
