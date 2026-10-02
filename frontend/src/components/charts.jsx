import { format, parseISO } from 'date-fns'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, XAxis, YAxis } from 'recharts'
import {
  ChartContainer,
  ChartLegend,
  ChartLegendContent,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { STATUSES, statusColor } from '@/lib/status'

const activityConfig = {
  created: { label: 'Created', color: 'var(--chart-1)' },
  merged: { label: 'Merged', color: 'var(--chart-2)' },
}

const BUCKET_LABEL = {
  day: (d) => format(d, 'd MMM'),
  week: (d) => format(d, 'd MMM'),
  month: (d) => format(d, 'MMM yy'),
  year: (d) => format(d, 'yyyy'),
}

const bucketTitle = (bucket, granularity) => {
  const start = parseISO(bucket.start)
  if (granularity === 'day') return format(start, 'EEE d MMM yyyy')
  if (granularity === 'year') return format(start, 'yyyy')
  if (granularity === 'month') return format(start, 'MMMM yyyy')
  return `${format(start, 'd MMM')} – ${format(parseISO(bucket.end), 'd MMM yyyy')}`
}

/**
 * Created vs merged PRs over the selected date range. ``activity`` comes from the API
 * as ``{granularity: day|week|month|year, buckets: [{start, end, created, merged}]}``;
 * clicking a bar calls ``onSelectBucket({from, to})`` so the page can zoom in.
 */
export function ActivityChart({ activity, onSelectBucket, height = 260 }) {
  const granularity = activity?.granularity ?? 'day'
  const rows = (activity?.buckets ?? []).map((b) => ({
    ...b,
    name: BUCKET_LABEL[granularity](parseISO(b.start)),
    title: bucketTitle(b, granularity),
  }))
  const select = (d) => {
    const row = d?.payload ?? d
    if (row?.start) onSelectBucket?.({ from: row.start, to: row.end })
  }
  if (!rows.length) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>
        No activity in this period.
      </div>
    )
  }
  return (
    <ChartContainer config={activityConfig} className="w-full" style={{ height }}>
      <BarChart data={rows} barGap={2}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="name" tickLine={false} axisLine={false} tickMargin={8} minTickGap={16} />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={28} />
        <ChartTooltip
          content={<ChartTooltipContent labelFormatter={(_, items) => items?.[0]?.payload?.title} />}
          cursor={{ fill: 'var(--muted)', opacity: 0.5 }}
        />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="created" fill="var(--color-created)" radius={[4, 4, 0, 0]}
          style={onSelectBucket ? { cursor: 'pointer' } : undefined} onClick={select} />
        <Bar dataKey="merged" fill="var(--color-merged)" radius={[4, 4, 0, 0]}
          style={onSelectBucket ? { cursor: 'pointer' } : undefined} onClick={select} />
      </BarChart>
    </ChartContainer>
  )
}

export function YearlyActivityChart({ data, height = 220 }) {
  const rows = (data ?? []).map((d) => ({ ...d, name: String(d.year) }))
  return (
    <ChartContainer config={activityConfig} className="w-full" style={{ height }}>
      <BarChart data={rows}>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="name" tickLine={false} axisLine={false} tickMargin={8} />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={28} />
        <ChartTooltip content={<ChartTooltipContent />} cursor={{ fill: 'var(--muted)', opacity: 0.5 }} />
        <ChartLegend content={<ChartLegendContent />} />
        <Bar dataKey="created" fill="var(--color-created)" radius={[4, 4, 0, 0]} />
        <Bar dataKey="merged" fill="var(--color-merged)" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ChartContainer>
  )
}

/** Horizontal bars of PR status counts (the "PR Status" panel). */
export function StatusChart({ counts, onSelectStatus, height = 220 }) {
  const rows = STATUSES.map((s) => ({ key: s.value, name: s.label, value: counts?.[s.value] ?? 0 }))
  const config = Object.fromEntries(rows.map((r) => [r.key, { label: r.name, color: statusColor(r.key) }]))
  return (
    <ChartContainer config={config} className="w-full" style={{ height }}>
      <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 16 }}>
        <CartesianGrid horizontal={false} />
        <YAxis dataKey="name" type="category" tickLine={false} axisLine={false} width={120} />
        <XAxis type="number" allowDecimals={false} hide />
        <ChartTooltip content={<ChartTooltipContent hideLabel />} cursor={{ fill: 'var(--muted)', opacity: 0.5 }} />
        <Bar
          dataKey="value"
          radius={4}
          label={{ position: 'right', className: 'fill-foreground text-xs' }}
          style={onSelectStatus ? { cursor: 'pointer' } : undefined}
          onClick={(d) => onSelectStatus?.(d?.payload?.key ?? d?.key)}
        >
          {rows.map((r) => (
            <Cell key={r.key} fill={statusColor(r.key)} />
          ))}
        </Bar>
      </BarChart>
    </ChartContainer>
  )
}

export function StatusDonut({ counts, height = 220 }) {
  const rows = STATUSES.map((s) => ({ key: s.value, name: s.label, value: counts?.[s.value] ?? 0 })).filter(
    (r) => r.value > 0,
  )
  const config = Object.fromEntries(rows.map((r) => [r.key, { label: r.name, color: statusColor(r.key) }]))
  return (
    <ChartContainer config={config} className="mx-auto aspect-square" style={{ height }}>
      <PieChart>
        <ChartTooltip content={<ChartTooltipContent hideLabel nameKey="key" />} />
        <Pie data={rows} dataKey="value" nameKey="key" innerRadius="60%" strokeWidth={2}>
          {rows.map((r) => (
            <Cell key={r.key} fill={statusColor(r.key)} />
          ))}
        </Pie>
      </PieChart>
    </ChartContainer>
  )
}

const reviewConfig = { reviews: { label: 'Reviews submitted', color: 'var(--chart-1)' } }

/**
 * Reviews you submitted over the selected dates, as a smooth trend line. ``activity`` is the API's
 * ``my_review_activity``: ``{granularity, buckets: [{start, end, reviews}]}``.
 */
export function ReviewActivityChart({ activity, height = 200 }) {
  const granularity = activity?.granularity ?? 'month'
  const rows = (activity?.buckets ?? []).map((b) => ({
    ...b,
    name: BUCKET_LABEL[granularity](parseISO(b.start)),
    title: bucketTitle(b, granularity),
  }))
  if (!rows.length) {
    return (
      <div className="flex items-center justify-center text-sm text-muted-foreground" style={{ height }}>
        No reviews in this period.
      </div>
    )
  }
  return (
    <ChartContainer config={reviewConfig} className="w-full" style={{ height }}>
      <AreaChart data={rows} margin={{ left: 4, right: 12, top: 8 }}>
        <defs>
          <linearGradient id="reviewFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-reviews)" stopOpacity={0.35} />
            <stop offset="100%" stopColor="var(--color-reviews)" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} />
        <XAxis dataKey="name" tickLine={false} axisLine={false} tickMargin={8} minTickGap={16} />
        <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={28} />
        <ChartTooltip
          content={<ChartTooltipContent labelFormatter={(_, items) => items?.[0]?.payload?.title} />}
          cursor={{ stroke: 'var(--muted-foreground)', strokeDasharray: 4, opacity: 0.5 }}
        />
        <Area
          dataKey="reviews"
          type="monotone"
          stroke="var(--color-reviews)"
          strokeWidth={2}
          fill="url(#reviewFill)"
          dot={rows.length <= 31 ? { r: 2.5, fill: 'var(--color-reviews)' } : false}
          activeDot={{ r: 4 }}
        />
      </AreaChart>
    </ChartContainer>
  )
}
