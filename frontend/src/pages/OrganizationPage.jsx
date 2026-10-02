import { createPortal } from 'react-dom'
import { Link, useNavigate, useOutletContext } from 'react-router-dom'
import { Clock, GitMerge, GitPullRequest, Hourglass } from 'lucide-react'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import FilterBar from '@/components/FilterBar'
import { SearchBox, TableFooter, useSearchPaging } from '@/components/TablePager'
import { ActivityChart } from '@/components/charts'
import { EmptyState, Section, StatCard, UserAvatar } from '@/components/common'
import { periodLabel } from '@/lib/format'
import { pickFilters, useOverview, useStatistics } from '@/lib/queries'
import { filtersToSearch, useFilters } from '@/lib/useFilters'

const memberText = (m) => `${m.display_name} ${m.github_username}`

/** Admin dashboard: the whole organization at a glance, and one row per member. */
export default function OrganizationPage() {
  const state = useFilters({ defaultRange: 'this_year' })
  const { filters, setDates } = state
  const navigate = useNavigate()
  const { headerSlot } = useOutletContext()
  // This page is always about everyone, whatever the URL says.
  const orgFilters = { ...filters, scope: 'all', member: '' }
  const stats = useStatistics(orgFilters)
  const overview = useOverview(filters)
  const data = stats.data
  const loading = stats.isLoading
  const listLink = (extra = {}) => `/pull-requests${filtersToSearch({ ...pickFilters(filters), scope: 'all', ...extra })}`
  const allMembers = overview.data?.members ?? []
  const paging = useSearchPaging(allMembers, memberText)
  const members = paging.rows

  return (
    <>
      {headerSlot &&
        createPortal(
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <FilterBar state={state} showReviewer={false} showStatus={false} inline />
          </div>,
          headerSlot,
        )}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard title="Pull Requests" icon={GitPullRequest} value={data?.counts.filtered} loading={loading}
          hint={`Created · ${periodLabel(filters)}`} onClick={() => navigate(listLink())} />
        <StatCard title="Open PRs" icon={Clock} value={data?.open_prs} loading={loading} hint="Not yet merged or closed"
          onClick={() => navigate(listLink({ status: 'open,review,changes_requested,approved' }))} />
        <StatCard title="Merged" icon={GitMerge} value={data?.merged_prs} loading={loading} hint="Merged PRs for these filters"
          onClick={() => navigate(listLink({ status: 'merged' }))} />
        <StatCard title="Waiting Review" icon={Hourglass} value={data?.waiting_review} loading={loading}
          hint="Review requested, in review or changes requested"
          onClick={() => navigate(listLink({ status: 'review,changes_requested' }))} />
      </div>

      {!loading && data?.counts.total === 0 ? (
        <EmptyState title="No pull requests yet" description="Once the first sync finishes, the organization's activity appears here.">
          <Link to="/settings" className="text-sm text-primary hover:underline">Check sync status</Link>
        </EmptyState>
      ) : (
        <>
          <Section title="PR activity" description={`Created vs merged across the organization · ${periodLabel(filters)}. Click a bar to zoom in.`}>
            {loading ? <Skeleton className="h-[260px] w-full" /> : <ActivityChart activity={data.activity} onSelectBucket={setDates} />}
          </Section>

          <Section title="Members" description={`PRs by creation date and reviews by submission date · ${periodLabel(filters)}. Click a name to see their dashboard.`}>
            <SearchBox value={paging.search} onChange={paging.setSearch} placeholder="Search members…" />
            <Card className="gap-0 overflow-hidden py-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Member</TableHead>
                    <TableHead className="text-right">PRs created</TableHead>
                    <TableHead className="text-right">Merged</TableHead>
                    <TableHead className="text-right">Open</TableHead>
                    <TableHead className="text-right">Reviews given</TableHead>
                    <TableHead className="text-right">PRs reviewed</TableHead>
                    <TableHead className="text-right">Changes requested</TableHead>
                    <TableHead className="text-right">Pending reviews</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {overview.isLoading &&
                    [0, 1, 2].map((i) => (
                      <TableRow key={i}>
                        {Array.from({ length: 8 }).map((_, j) => (
                          <TableCell key={j}><Skeleton className="h-5 w-full" /></TableCell>
                        ))}
                      </TableRow>
                    ))}
                  {members.map((m) => {
                    const dates = pickFilters({ date_from: filters.date_from, date_to: filters.date_to, repository: filters.repository })
                    return (
                      <TableRow key={m.id}>
                        <TableCell>
                          <Link to={`/${filtersToSearch({ ...dates, member: m.id })}`} className="flex items-center gap-2 hover:underline">
                            <UserAvatar login={m.github_username} src={m.avatar_url} className="size-6" />
                            <span className="font-medium">{m.display_name}</span>
                            {m.role === 'ADMIN' && <span className="text-xs text-muted-foreground">admin</span>}
                          </Link>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">
                          <Link className="hover:underline" to={`/pull-requests${filtersToSearch({ ...dates, member: m.id })}`}>{m.prs_created}</Link>
                        </TableCell>
                        <TableCell className="text-right tabular-nums">{m.prs_merged}</TableCell>
                        <TableCell className="text-right tabular-nums">{m.prs_open}</TableCell>
                        <TableCell className="text-right tabular-nums">{m.reviews_submitted}</TableCell>
                        <TableCell className="text-right tabular-nums">{m.prs_reviewed}</TableCell>
                        <TableCell className="text-right tabular-nums">{m.changes_requested}</TableCell>
                        <TableCell className="text-right tabular-nums">
                          <Link className="hover:underline" to={`/my-reviews${filtersToSearch({ member: m.id, range: 'all', status: 'pending' })}`}>
                            {m.pending_reviews}
                          </Link>
                        </TableCell>
                      </TableRow>
                    )
                  })}
                  {!overview.isLoading && members.length === 0 && (
                    <TableRow>
                      <TableCell colSpan={8} className="h-20 text-center text-muted-foreground">{allMembers.length ? `No members match “${paging.search.trim()}”.` : 'No members yet.'}</TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
              {!overview.isLoading && allMembers.length > 0 && <TableFooter paging={paging} />}
            </Card>
          </Section>
        </>
      )}
    </>
  )
}
