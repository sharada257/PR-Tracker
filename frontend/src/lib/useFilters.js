import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { FILTER_KEYS } from '@/lib/queries'
import { presetByKey } from '@/lib/dateRange'

/**
 * Filters live in the URL, so every combination is linkable, survives reloads and
 * works with the back button.
 *
 * ``defaultRange`` is a preset key (e.g. ``'30d'``) used while the URL has no dates,
 * so every page opens on a sensible window (e.g. This Year). ``range=all`` in the
 * URL explicitly selects "All time". Without ``defaultRange`` no dates are applied.
 */
export function useFilters({ defaultRange = null } = {}) {
  const [searchParams, setSearchParams] = useSearchParams()

  const filters = useMemo(() => {
    const out = {}
    for (const key of FILTER_KEYS) {
      const value = searchParams.get(key)
      if (value) out[key] = value
    }
    if (!out.date_from && !out.date_to && defaultRange && searchParams.get('range') !== 'all') {
      const range = presetByKey(defaultRange)?.range()
      if (range) {
        out.date_from = range.from
        out.date_to = range.to
      }
    }
    return out
  }, [searchParams, defaultRange])

  const update = useCallback(
    (patch) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current)
          for (const [key, value] of Object.entries(patch)) {
            if (value === undefined || value === null || value === '') next.delete(key)
            else next.set(key, value)
          }
          // Picking dates replaces any "All time" marker; a bare marker means "no dates".
          if (patch.date_from || patch.date_to) next.delete('range')
          next.delete('page')
          return next
        },
        { replace: true },
      )
    },
    [setSearchParams],
  )

  /** ``null`` (or an empty range) selects All time. */
  const setDates = useCallback(
    (range) => {
      if (range?.from || range?.to) update({ date_from: range.from ?? '', date_to: range.to ?? '', range: '' })
      else update({ date_from: '', date_to: '', range: 'all' })
    },
    [update],
  )

  const reset = useCallback(() => setSearchParams(new URLSearchParams(), { replace: true }), [setSearchParams])

  // "Dirty" = anything the user changed, i.e. not just the implicit default window.
  const explicitDates = Boolean(searchParams.get('date_from') || searchParams.get('date_to'))
  const dirty =
    Object.keys(filters).some((key) => key !== 'date_from' && key !== 'date_to') ||
    explicitDates ||
    (Boolean(defaultRange) && searchParams.get('range') === 'all')

  return { filters, update, setDates, reset, dirty, searchParams, setSearchParams }
}

/** Turns a filters object into a query string for links. */
export function filtersToSearch(filters) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value)
  const text = params.toString()
  return text ? `?${text}` : ''
}
