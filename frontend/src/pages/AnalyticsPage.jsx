import { createPortal } from 'react-dom'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { Clock, GitMerge, GitPullRequest, Hourglass } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import FilterBar from '@/components/FilterBar'
import PRTable from '@/components/PRTable'
import { ActivityChart, ReviewActivityChart, YearlyActivityChart } from '@/components/charts'
import { CalculatedBadge, Definition, EmptyState, Section, StatCard } from '@/components/common'
import { formatDuration, formatNumber, periodLabel } from '@/lib/format'
import { statusColor } from '@/lib/status'
import { cn } from '@/lib/utils'
import { pickFilters, useFilterOptions, usePullRequests, useStatistics } from '@/lib/queries'
import { filtersToSearch, useFilters } from '@/lib/useFilters'

function Big({ label, value, sub, definition }) {
  return (
    <Card className="gap-1 py-4">
      <CardContent className="space-y-1 px-4">
        <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
          {label}
          <Definition text={definition} />
        </div>
        <div className="text-2xl font-semibold tabular-nums">{value}</div>
        {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
      </CardContent>
    </Card>
  )
}

/** A titled list of counts (PR state, review status); each row links to the matching PR list. */
function BreakdownCard({ title, hint, rows, loading, className }) {
  return (
    <Card className={cn('gap-2 py-4', className)}>
      <CardContent className="space-y-3 px-4">
        <div>
          <div className="text-sm font-medium">{title}</div>
          <div className="text-xs text-muted-foreground">{hint}</div>
        </div>
        <ul className="space-y-1">
          {rows.map((row) => (
            <li key={row.label}>
              <button
                type="button"
                onClick={row.onClick}
                className={cn(
                  'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-muted',
                  row.sub && 'ml-5 w-[calc(100%-1.25rem)] text-muted-foreground',
                )}
              >
                <span className="size-2.5 shrink-0 rounded-full" style={{ backgroundColor: row.color }} />
                <span className="flex-1 text-left">{row.label}</span>
                {loading ? (
                  <Skeleton className="h-5 w-6" />
                ) : (
                  <span className={cn('font-semibold tabular-nums text-foreground', row.sub ? 'text-base' : 'text-lg')}>
                    {row.value ?? '—'}
                  </span>
                )}
              </button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  )
}

/** Home page: headline numbers, activity over time, review / merge metrics, repositories and recent PRs. */
export default function AnalyticsPage() {
  const state = useFilters({ defaultRange: 'this_year' })
  const { filters, setDates } = state
  const navigate = useNavigate()
  const stats = useStatistics(filters)
  const recent = usePullRequests({ ...pickFilters(filters), ordering: '-created_at', page_size: 8 })
  const { headerSlot } = useOutletContext()
  const options = useFilterOptions().data
  const viewedMember = filters.member ? options?.members?.find((m) => String(m.id) === filters.member) : null
  const data = stats.data
  const loading = stats.isLoading
  const defs = data?.definitions ?? {}
  const ttm = data?.merge_metrics.time_to_merge_hours
  const ttr = data?.review_metrics.time_to_first_review_hours
  const prState = data?.pr_state
  const reviewActivity = data?.my_review_activity
  const listLink = (extra = {}) => `/pull-requests${filtersToSearch({ ...pickFilters(filters), ...extra })}`

  return (
    <>
      {headerSlot &&
        createPortal(
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <FilterBar state={state} showReviewer={false} showStatus={false} showViewing inline />
          </div>,
          headerSlot,
        )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          title="Pull Requests"
          icon={GitPullRequest}
          value={data?.counts.filtered}
          loading={loading}
          hint={`Created · ${periodLabel(filters)}`}
          onClick={() => navigate(listLink())}
        />
        <StatCard
          title="Open PRs"
          icon={Clock}
          value={data?.open_prs}
          loading={loading}
          hint="Not yet merged or closed"
          onClick={() => navigate(listLink({ status: 'open,review,changes_requested,approved' }))}
        />
        <StatCard
          title="Merged"
          icon={GitMerge}
          value={data?.merged_prs}
          loading={loading}
          hint="Merged PRs for these filters"
          onClick={() => navigate(listLink({ status: 'merged' }))}
        />
        <StatCard
          title="Waiting Review"
          icon={Hourglass}
          value={data?.waiting_review}
          loading={loading}
          definition={defs.waiting_review}
          hint="Review requested, in review or changes requested"
          onClick={() => navigate(listLink({ status: 'review,changes_requested' }))}
        />
      </div>

      {!loading && data?.counts.total === 0 ? (
        <EmptyState
          title="No pull requests yet"
          description="Once your repositories finish importing, your PR history appears here. New PRs arrive automatically via GitHub webhooks."
        >
          <Button asChild variant="outline" size="sm">
            <Link to="/settings">Check import status</Link>
          </Button>
        </EmptyState>
      ) : (
        <>
          <div className="grid gap-4 lg:grid-cols-5">
            <Section
              className="lg:col-span-3"
              title="PR activity"
              description={`Created vs merged · ${periodLabel(filters)}. Click a bar to zoom in.`}
            >
              {loading ? (
                <Skeleton className="h-[260px] w-full" />
              ) : (
                <ActivityChart activity={data.activity} onSelectBucket={setDates} />
              )}
            </Section>
            <BreakdownCard
              className="lg:col-span-2"
              title="PR State"
              hint="Active PRs are the ones still open"
              loading={loading}
              rows={[
                { label: 'Active', value: prState?.open, color: statusColor('open'),
                  onClick: () => navigate(listLink({ status: 'open,review,changes_requested,approved' })) },
                { label: 'In review', value: prState?.in_review, color: statusColor('review'), sub: true,
                  onClick: () => navigate(listLink({ status: 'review,changes_requested' })) },
                { label: 'Approved', value: prState?.approved, color: statusColor('approved'), sub: true,
                  onClick: () => navigate(listLink({ status: 'approved' })) },
                // Only appears when some active PR has no reviewer yet, so the two rows above always add up.
                ...(prState?.no_review
                  ? [{ label: 'No review requested', value: prState.no_review, color: 'var(--muted-foreground)', sub: true,
                      onClick: () => navigate(listLink({ status: 'open' })) }]
                  : []),
                { label: 'Merged', value: prState?.merged, color: statusColor('merged'),
                  onClick: () => navigate(listLink({ status: 'merged' })) },
                { label: 'Closed', value: prState?.closed, color: statusColor('closed'),
                  onClick: () => navigate(listLink({ status: 'closed' })) },
              ]}
            />
          </div>

          <Section
            title={viewedMember ? `${viewedMember.display_name}'s Review Activity` : 'My Review Activity'}
            description={`Reviews submitted · ${periodLabel(filters)}`}
            actions={
              <Button asChild variant="ghost" size="sm">
                <Link to="/my-reviews">View My Reviews</Link>
              </Button>
            }
          >
            <div className="grid gap-6 lg:grid-cols-5">
              <div className="lg:col-span-3">
                {loading ? <Skeleton className="h-[200px] w-full" /> : <ReviewActivityChart activity={reviewActivity} />}
              </div>
              <dl className="grid grid-cols-2 content-center gap-4 lg:col-span-2">
                {[
                  ['Reviews submitted', reviewActivity?.totals.reviews],
                  ['PRs reviewed', reviewActivity?.totals.prs],
                  ['Approvals', reviewActivity?.totals.approvals],
                  ['Changes requested', reviewActivity?.totals.changes_requested],
                ].map(([label, value]) => (
                  <div key={label} className="space-y-1">
                    <dt className="text-xs text-muted-foreground">{label}</dt>
                    <dd className="text-2xl font-semibold tabular-nums">{loading ? '—' : (value ?? 0)}</dd>
                  </div>
                ))}
              </dl>
            </div>
          </Section>

          <div className="grid gap-4 lg:grid-cols-3">
            <Section title="Review time" description="How quickly your PRs get looked at." actions={<CalculatedBadge />}>
              <div className="grid grid-cols-2 gap-4">
                <Big label="Avg time to first review" value={formatDuration(ttr?.average)}
                  sub={`${ttr?.count ?? 0} PRs reviewed`} definition={defs.time_to_first_review} />
                <Big label="Median" value={formatDuration(ttr?.median)} />
                <Big label="Total reviews" value={data?.review_metrics.total_reviews ?? '—'}
                  sub={`across ${data?.review_metrics.prs_with_reviews ?? 0} PRs`} definition={defs.review_count} />
                <Big label="Avg review cycles" value={formatNumber(data?.review_metrics.average_review_cycles)}
                  sub={`${data?.review_metrics.prs_with_multiple_cycles ?? 0} PRs had 2+ cycles`}
                  definition={defs.review_cycles} />
              </div>
            </Section>
            <Section title="Merge time" description="From creation to merge." actions={<CalculatedBadge />}>
              <div className="grid grid-cols-2 gap-4">
                <Big label="Avg time to merge" value={formatDuration(ttm?.average)}
                  sub={`${ttm?.count ?? 0} merged PRs`} definition={defs.time_to_merge} />
                <Big label="Median time to merge" value={formatDuration(ttm?.median)} />
                <Big label="PRs merged" value={data?.merge_metrics.merged ?? '—'} />
                <Big label="Closed without merge" value={data?.merge_metrics.closed_without_merge ?? '—'} />
              </div>
            </Section>
            <Section title="Year over year" description="All years, ignoring the date filter.">
              {loading ? <Skeleton className="h-[220px]" /> : <YearlyActivityChart data={data.by_year} />}
            </Section>
          </div>

          <Section title="Repositories" description="Contribution per repository. Click one to see its PRs.">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Repository</TableHead>
                  <TableHead className="text-right">PRs</TableHead>
                  <TableHead className="text-right">Merged</TableHead>
                  <TableHead className="text-right">Open</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(data?.by_repository ?? []).map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">
                      <Link to={listLink({ repository: r.id })} className="hover:underline">
                        {r.full_name}
                      </Link>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{r.total}</TableCell>
                    <TableCell className="text-right tabular-nums">{r.merged}</TableCell>
                    <TableCell className="text-right tabular-nums">{r.open}</TableCell>
                  </TableRow>
                ))}
                {!loading && !data?.by_repository.length && (
                  <TableRow>
                    <TableCell colSpan={4} className="h-16 text-center text-muted-foreground">
                      No data for these filters.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </Section>

          <Section
            title="Recent PRs"
            actions={
              <Button asChild variant="ghost" size="sm">
                <Link to={listLink()}>View all</Link>
              </Button>
            }
          >
            <PRTable compact rows={recent.data?.results ?? []} loading={recent.isLoading} />
          </Section>
        </>
      )}
    </>
  )
}
