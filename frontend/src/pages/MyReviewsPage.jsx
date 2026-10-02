import {
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  ClipboardCheck,
  ExternalLink,
  Hourglass,
  Loader2,
  MessageSquare,
  ThumbsUp,
} from 'lucide-react'
import { format, parseISO } from 'date-fns'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import FilterBar from '@/components/FilterBar'
import { EmptyState, StatCard, UserAvatar } from '@/components/common'
import { formatDuration, periodLabel } from '@/lib/format'
import { REVIEW_STATUSES, REVIEW_STATUS_LABEL } from '@/lib/status'
import { useMyReviews } from '@/lib/queries'
import { useFilters } from '@/lib/useFilters'

function ReviewStatusBadge({ status }) {
  return (
    <Badge variant="outline" className="gap-1.5 whitespace-nowrap font-medium">
      <span className="size-2 rounded-full" style={{ backgroundColor: `var(--status-${status})` }} />
      {REVIEW_STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

/** "Sep 28" this year, "Dec 1, 2025" otherwise. */
function shortDate(value) {
  if (!value) return '—'
  const date = parseISO(value)
  return format(date, date.getFullYear() === new Date().getFullYear() ? 'MMM d' : 'MMM d, yyyy')
}

function PendingCard({ pending, loading, onView }) {
  const count = pending?.count ?? 0
  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4 pb-0">
        <CardTitle className="flex items-center gap-2 text-base">
          {loading ? (
            <Skeleton className="h-6 w-48" />
          ) : count > 0 ? (
            <>
              <span className="size-2.5 rounded-full" style={{ backgroundColor: 'var(--status-pending)' }} />
              {count} Pending Review{count === 1 ? '' : 's'}
            </>
          ) : (
            <>
              <CircleCheck className="size-5 text-[var(--status-approved)]" />
              Nothing waiting on you
            </>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 px-4">
        {loading && <Skeleton className="h-16 w-full" />}
        {!loading && count === 0 && (
          <p className="text-sm text-muted-foreground">
            When someone asks you to review a pull request, it shows up here with how long it has been waiting.
          </p>
        )}
        {!loading && count > 0 && (
          <>
            <ul className="divide-y rounded-md border">
              {pending.items.map((item) => (
                <li key={item.id}>
                  <a
                    href={item.url}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-3 px-3 py-2 text-sm hover:bg-muted/50"
                  >
                    <span className="font-mono text-muted-foreground">#{item.number}</span>
                    <span className="min-w-0 flex-1 truncate font-medium">{item.title}</span>
                    <span className="hidden text-xs text-muted-foreground sm:inline">{item.repository.full_name}</span>
                    <span className="w-12 text-right font-medium tabular-nums" title="Waiting for your review">
                      {formatDuration(item.waiting_hours)}
                    </span>
                  </a>
                </li>
              ))}
            </ul>
            {count > pending.items.length && (
              <p className="text-xs text-muted-foreground">
                Showing the {pending.items.length} that have waited longest.
              </p>
            )}
            <Button variant="outline" size="sm" onClick={onView}>
              View pending reviews
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  )
}

export default function MyReviewsPage() {
  const state = useFilters({ defaultRange: 'this_year' })
  const { filters, update, searchParams, setSearchParams } = state
  const page = Number(searchParams.get('page') || 1)
  const { data, isLoading, isFetching } = useMyReviews(filters, { page, page_size: 15 })
  const activity = data?.activity
  const rows = data?.results ?? []
  const filterByStatus = (status) => update({ status: filters.status === status ? '' : status })
  const setPage = (next) =>
    setSearchParams(
      (current) => {
        const params = new URLSearchParams(current)
        params.set('page', String(next))
        return params
      },
      { replace: true },
    )

  return (
    <>
      {data?.importing && (
        <div className="flex items-center gap-2 rounded-lg border bg-muted/40 px-4 py-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Importing the pull requests you review from GitHub — these numbers will fill in as it goes.
        </div>
      )}

      <PendingCard pending={data?.pending} loading={isLoading} onView={() => update({ status: 'pending' })} />

      <div className="space-y-3">
        <div>
          <h3 className="text-base font-semibold">Review activity</h3>
          <p className="text-sm text-muted-foreground">
            Pull requests by others that you reviewed · {periodLabel(filters)}
          </p>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard title="PRs reviewed" icon={ClipboardCheck} value={activity?.reviewed} loading={isLoading}
            hint="Reviews you submitted" onClick={() => update({ status: '' })} />
          <StatCard title="Approvals" icon={ThumbsUp} value={activity?.approvals} loading={isLoading}
            hint="Your latest review approved" onClick={() => filterByStatus('approved')} />
          <StatCard title="Changes requested" icon={MessageSquare} value={activity?.changes_requested}
            loading={isLoading} hint="You asked for changes" onClick={() => filterByStatus('changes_requested')} />
          <StatCard title="Pending" icon={Hourglass} value={activity?.pending} loading={isLoading}
            hint="Waiting for your review right now" onClick={() => filterByStatus('pending')} />
        </div>
      </div>

      <FilterBar state={state} showSearch showReviewer={false} showAuthor authorOptions={data?.authors ?? []}
        showViewing allowEveryone={false} statuses={REVIEW_STATUSES} inline />

      <Card className="gap-0 overflow-hidden py-0">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>PR</TableHead>
              <TableHead>Repository</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Reviewed</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {isLoading &&
              Array.from({ length: 5 }).map((_, i) => (
                <TableRow key={i}>
                  <TableCell colSpan={4}>
                    <Skeleton className="h-6 w-full" />
                  </TableCell>
                </TableRow>
              ))}
            {rows.map((row) => (
              <TableRow key={`${row.id}-${row.status}`}>
                <TableCell className="max-w-[420px]">
                  <a href={row.url} target="_blank" rel="noreferrer" className="group flex items-center gap-3">
                    <UserAvatar login={row.author_login} src={row.author_avatar_url} />
                    <span className="min-w-0">
                      <span className="flex items-center gap-1.5">
                        <span className="font-mono text-muted-foreground">#{row.number}</span>
                        <span className="truncate font-medium group-hover:underline">{row.title}</span>
                        <ExternalLink className="size-3.5 shrink-0 text-muted-foreground opacity-0 group-hover:opacity-100" />
                      </span>
                      <span className="block text-xs text-muted-foreground">by {row.author_login}</span>
                    </span>
                  </a>
                </TableCell>
                <TableCell className="text-muted-foreground">{row.repository.full_name}</TableCell>
                <TableCell>
                  <ReviewStatusBadge status={row.status} />
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {row.status === 'pending' ? `Waiting ${formatDuration(row.waiting_hours)}` : shortDate(row.reviewed_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {!isLoading && rows.length === 0 && (
          <div className="p-6">
            <EmptyState
              title="No reviews match these filters"
              description="Reviews appear here once you review someone else's pull request. Try a wider date range, or All Time."
            />
          </div>
        )}

        {data && data.pages > 1 && (
          <div className="flex items-center justify-end gap-2 border-t px-4 py-3 text-sm">
            <span className="text-muted-foreground">
              Page {data.page} of {data.pages} · {data.count} total{isFetching ? ' · updating…' : ''}
            </span>
            <Button variant="outline" size="icon" disabled={data.page <= 1} onClick={() => setPage(data.page - 1)}
              aria-label="Previous page">
              <ChevronLeft />
            </Button>
            <Button variant="outline" size="icon" disabled={data.page >= data.pages} onClick={() => setPage(data.page + 1)}
              aria-label="Next page">
              <ChevronRight />
            </Button>
          </div>
        )}
      </Card>
    </>
  )
}
