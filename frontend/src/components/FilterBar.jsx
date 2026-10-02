import { useEffect, useRef, useState } from 'react'
import { format, isValid, parse, parseISO } from 'date-fns'
import { CalendarRange, Search, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Calendar } from '@/components/ui/calendar'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { cn } from '@/lib/utils'
import { PRESETS, matchPreset, rangeLabel } from '@/lib/dateRange'
import { STATUSES } from '@/lib/status'
import { useFilterOptions, useIsAdmin, useMe } from '@/lib/queries'

const ALL = '__all__'
const ymd = (date) => format(date, 'yyyy-MM-dd')

function FilterSelect({ label, value, onChange, items, width = 'w-[150px]', inline = false, allLabel = 'All' }) {
  return (
    <div className={inline ? undefined : 'space-y-1'}>
      {!inline && <span className="text-xs text-muted-foreground">{label}</span>}
      <Select value={value || ALL} onValueChange={(v) => onChange(v === ALL ? '' : v)}>
        <SelectTrigger className={inline ? 'w-auto' : width} aria-label={label}>
          {inline && <span className="text-muted-foreground">{label}:</span>}
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {allLabel && <SelectItem value={ALL}>{allLabel}</SelectItem>}
          {items.map((item) => (
            <SelectItem key={item.value} value={String(item.value)}>
              {item.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

const INPUT_FORMAT = 'MM-dd-yyyy'

/** A typed date box: shows the committed value, applies on Enter / blur when it parses. */
function DateInput({ label, value, onCommit, inputRef }) {
  const shown = value ? format(parseISO(value), INPUT_FORMAT) : ''
  const [text, setText] = useState(shown)
  useEffect(() => setText(shown), [shown])
  const commit = () => {
    if (text === shown) return
    const parsed = parse(text, INPUT_FORMAT, new Date())
    if (isValid(parsed)) onCommit(ymd(parsed))
    else setText(shown)
  }
  return (
    <Input
      ref={inputRef}
      value={text}
      placeholder="MM-DD-YYYY"
      aria-label={label}
      onChange={(e) => setText(e.target.value)}
      onBlur={commit}
      onKeyDown={(e) => e.key === 'Enter' && commit()}
      className="h-8"
    />
  )
}

/**
 * One date control: start / end inputs, a two-month range calendar and a list of quick
 * presets on the right. The calendar takes two clicks (start, then end) to apply.
 */
function DateRangePicker({ from, to, onChange, inline = false }) {
  const [open, setOpen] = useState(false)
  const [anchor, setAnchor] = useState() // first click of a new range
  const startInput = useRef(null)
  const active = matchPreset(from, to)
  const committed = from || to ? { from: from ? parseISO(from) : undefined, to: to ? parseISO(to) : undefined } : undefined

  const apply = (range) => {
    onChange(range)
    setAnchor(undefined)
    setOpen(false)
  }
  const setEnd = (key, value) => {
    const next = { from, to, [key]: value }
    if (next.from && next.to && next.from > next.to) [next.from, next.to] = [next.to, next.from]
    onChange(next)
  }

  return (
    <div className={inline ? undefined : 'space-y-1'}>
      {!inline && <span className="text-xs text-muted-foreground">Date</span>}
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (!next) setAnchor(undefined)
        }}
      >
        <PopoverTrigger asChild>
          <Button variant="outline" className={cn('justify-start font-normal', inline ? 'w-auto' : 'w-[230px]')}
            aria-label="Date range">
            <CalendarRange className="size-4" />
            <span className="truncate">{rangeLabel(from, to)}</span>
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto p-0" align="start">
          <div className="flex flex-col sm:flex-row">
            <div>
              <div className="grid grid-cols-2 gap-2 border-b p-3">
                <DateInput label="Start date" value={from} inputRef={startInput} onCommit={(v) => setEnd('from', v)} />
                <DateInput label="End date" value={to} onCommit={(v) => setEnd('to', v)} />
              </div>
              <Calendar
                mode="range"
                numberOfMonths={2}
                weekStartsOn={1}
                defaultMonth={committed?.from}
                selected={anchor ? { from: anchor } : committed}
                onDayClick={(day) => {
                  if (!anchor) return setAnchor(day)
                  const [start, end] = day < anchor ? [day, anchor] : [anchor, day]
                  return apply({ from: ymd(start), to: ymd(end) })
                }}
              />
            </div>
            <div className="flex flex-row flex-wrap gap-1 border-t p-2 sm:w-[150px] sm:flex-col sm:flex-nowrap sm:border-t-0 sm:border-l">
              {PRESETS.map((preset) => (
                <Button
                  key={preset.key}
                  variant="ghost"
                  size="sm"
                  className={cn(
                    'justify-start font-normal',
                    active?.key === preset.key && 'bg-primary text-primary-foreground hover:bg-primary/90 hover:text-primary-foreground',
                  )}
                  onClick={() => apply(preset.range())}
                >
                  {preset.label}
                </Button>
              ))}
              <Button
                variant="ghost"
                size="sm"
                className={cn(
                  'justify-start font-normal',
                  !active && 'bg-primary text-primary-foreground hover:bg-primary/90 hover:text-primary-foreground',
                )}
                onClick={() => startInput.current?.focus()}
              >
                Custom Range
              </Button>
            </div>
          </div>
        </PopoverContent>
      </Popover>
    </div>
  )
}

/**
 * Combinable filters: Date (presets or custom), Repository, Reviewer, Status and
 * optionally free-text search. ``statuses`` replaces the status list (My Reviews uses review statuses). ``state`` is the object returned by ``useFilters``.
 */
export default function FilterBar({
  state,
  showSearch = false,
  showReviewer = true,
  showStatus = true,
  showAuthor = false,
  showViewing = false,
  allowEveryone = true,
  authorOptions,
  statuses = STATUSES,
  inline = false,
}) {
  const { filters, update, setDates, reset, dirty } = state
  const [text, setText] = useState(filters.q ?? '')
  // Keep the box in sync when filters are reset / changed from elsewhere, and debounce typing.
  useEffect(() => setText(filters.q ?? ''), [filters.q])
  useEffect(() => {
    if (text === (filters.q ?? '')) return undefined
    const timer = setTimeout(() => update({ q: text.trim() }), 300)
    return () => clearTimeout(timer)
  }, [text, filters.q, update])
  const { data: options } = useFilterOptions()
  const isAdmin = useIsAdmin()
  const me = useMe().data?.user
  // Admins can look at their own data (default), everyone's, or one member's. Members only ever see their own.
  const viewing = filters.member ? String(filters.member) : filters.scope === 'all' ? 'all' : 'me'
  const viewingItems = [
    { value: 'me', label: 'Me' },
    ...(allowEveryone ? [{ value: 'all', label: 'Everyone' }] : []),
    ...(options?.members ?? []).filter((m) => m.id !== me?.id).map((m) => ({ value: m.id, label: m.display_name })),
  ]

  const controls = (
    <>
        {showViewing && isAdmin && (
          <FilterSelect label="Viewing" value={viewing} width="w-[190px]" inline={inline} allLabel={null}
            items={viewingItems}
            onChange={(v) => update({ scope: v === 'all' ? 'all' : '', member: v === 'me' || v === 'all' ? '' : v })} />
        )}
        <DateRangePicker from={filters.date_from} to={filters.date_to} onChange={setDates} inline={inline} />
        <FilterSelect label="Repository" value={filters.repository} width="w-[190px]" inline={inline}
          items={(options?.repositories ?? []).map((r) => ({ value: r.id, label: r.full_name }))}
          onChange={(v) => update({ repository: v })} />
        {showAuthor && (
          <FilterSelect label="Author" value={filters.author} width="w-[160px]" inline={inline}
            items={(authorOptions ?? options?.authors ?? []).map((a) => ({ value: a, label: a }))}
            onChange={(v) => update({ author: v })} />
        )}
        {showReviewer && (
          <FilterSelect label="Reviewer" value={filters.reviewer} width="w-[160px]" inline={inline}
            items={(options?.reviewers ?? []).map((r) => ({ value: r, label: r }))}
            onChange={(v) => update({ reviewer: v })} />
        )}
        {showStatus && (
          <FilterSelect label="Status" value={filters.status} width="w-[170px]" items={statuses} inline={inline}
            onChange={(v) => update({ status: v })} />
        )}
        {dirty && (
          <Button variant="ghost" size="sm" onClick={reset} className={inline ? undefined : 'mb-0.5'}>
            <X className="size-4" /> Reset
          </Button>
        )}
    </>
  )

  const search = (
    <div className={cn('relative', inline && 'min-w-[200px] flex-1')}>
      <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Search PRs…"
        title="Search by title, repository, author or PR number"
        className={cn('pl-8', inline && 'h-8')}
        aria-label="Search pull requests"
      />
    </div>
  )

  // Inline: one bare row, no card. The search box (when asked for) comes first and takes whatever
  // space is left; the filter buttons follow. Used in the top bar and on the list pages.
  if (inline) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {showSearch && search}
        {controls}
      </div>
    )
  }

  return (
    <div className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-end gap-3">{controls}</div>
      {showSearch && search}
    </div>
  )
}
