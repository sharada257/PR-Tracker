import { useNavigate } from 'react-router-dom'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Badge } from '@/components/ui/badge'
import FilterBar from '@/components/FilterBar'
import { EmptyState, PageHeader, UserAvatar } from '@/components/common'
import { periodLabel, timeAgo } from '@/lib/format'
import { pickFilters, useReviewers } from '@/lib/queries'
import { filtersToSearch, useFilters } from '@/lib/useFilters'

export default function ReviewersPage() {
  const state = useFilters({ defaultRange: 'this_year' })
  const { filters } = state
  const { data, isLoading } = useReviewers(filters)
  const navigate = useNavigate()
  const people = data ?? []

  return (
    <>
      <PageHeader
        title="Reviewers"
        description={`Who reviewed your PRs · ${periodLabel(filters)}. Descriptive activity only — no quality ratings.`}
      />
      <FilterBar state={state} />
      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : people.length === 0 ? (
        <EmptyState title="No reviewer activity" description="No reviews or review requests match these filters." />
      ) : (
        <div className="rounded-lg border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Reviewer</TableHead>
                <TableHead className="text-right">PRs reviewed</TableHead>
                <TableHead className="text-right">Requested</TableHead>
                <TableHead className="text-right">Reviews</TableHead>
                <TableHead className="text-right">Approvals</TableHead>
                <TableHead className="text-right">Changes requested</TableHead>
                <TableHead>Last activity</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {people.map((p) => (
                <TableRow
                  key={p.login}
                  className="cursor-pointer"
                  onClick={() =>
                    navigate(`/pull-requests${filtersToSearch({ ...pickFilters(filters), reviewer: p.login })}`)
                  }
                >
                  <TableCell>
                    <span className="flex items-center gap-2 font-medium">
                      <UserAvatar login={p.login} src={p.avatar_url} className="size-7" />
                      {p.login}
                      {p.is_team && <Badge variant="secondary">team</Badge>}
                    </span>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{p.prs_reviewed}</TableCell>
                  <TableCell className="text-right tabular-nums">{p.prs_requested}</TableCell>
                  <TableCell className="text-right tabular-nums">{p.reviews_submitted}</TableCell>
                  <TableCell className="text-right tabular-nums">{p.approvals}</TableCell>
                  <TableCell className="text-right tabular-nums">{p.changes_requested}</TableCell>
                  <TableCell className="text-muted-foreground">{p.last_activity ? timeAgo(p.last_activity) : '—'}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}
    </>
  )
}
