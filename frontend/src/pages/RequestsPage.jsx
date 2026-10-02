import { toast } from 'sonner'
import { Check, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { EmptyState, UserAvatar } from '@/components/common'
import { SearchBox, TableFooter, useSearchPaging } from '@/components/TablePager'
import { formatDateTime, timeAgo } from '@/lib/format'
import { useAccessRequests, useDecideRequest } from '@/lib/queries'

const requestText = (r) => `${r.display_name} ${r.github_username} ${r.message}`
const STATUS = { PENDING: ['outline', 'Pending'], APPROVED: ['secondary', 'Approved'], DENIED: ['destructive', 'Denied'] }

/** Admins: people who were removed from the organization and asked to come back. */
export default function RequestsPage() {
  const { data, isLoading } = useAccessRequests()
  const decide = useDecideRequest()
  const all = data?.results ?? []
  const paging = useSearchPaging(all, requestText)

  const act = (r, decision) =>
    decide.mutate(
      { id: r.id, decision },
      {
        onSuccess: () => toast.success(decision === 'approve' ? `${r.display_name} has access again` : `Request from ${r.display_name} denied`),
        onError: (e) => toast.error(e.message),
      },
    )

  return (
    <>
      <p className="text-sm text-muted-foreground">
        When you remove someone, they land on a page where they can ask to come back. Approving puts them back as a
        regular member; denying keeps them out (they can ask again).
      </p>
      {!isLoading && all.length === 0 ? (
        <EmptyState title="No requests" description="Nobody has asked for access." />
      ) : (
        <>
          <SearchBox value={paging.search} onChange={paging.setSearch} placeholder="Search requests…" />
          <Card className="gap-0 overflow-hidden py-0">
            {isLoading ? (
              <div className="space-y-2 p-4">
                {[0, 1, 2].map((i) => (
                  <Skeleton key={i} className="h-10" />
                ))}
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Person</TableHead>
                    <TableHead>Message</TableHead>
                    <TableHead>Requested</TableHead>
                    <TableHead>Status</TableHead>
                    <TableHead className="text-right">Decision</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {paging.rows.map((r) => {
                    const [variant, label] = STATUS[r.status] ?? STATUS.PENDING
                    return (
                      <TableRow key={r.id}>
                        <TableCell>
                          <div className="flex items-center gap-3">
                            <UserAvatar login={r.github_username} src={r.avatar_url} className="size-8" />
                            <div className="leading-tight">
                              <div className="font-medium">{r.display_name}</div>
                              <div className="text-xs text-muted-foreground">@{r.github_username}</div>
                            </div>
                          </div>
                        </TableCell>
                        <TableCell className="max-w-xs whitespace-normal text-muted-foreground">{r.message || '—'}</TableCell>
                        <TableCell className="text-muted-foreground" title={formatDateTime(r.created_at)}>{timeAgo(r.created_at)}</TableCell>
                        <TableCell>
                          <Badge variant={variant}>{label}</Badge>
                          {r.decided_by && <div className="mt-1 text-xs text-muted-foreground">by @{r.decided_by}</div>}
                        </TableCell>
                        <TableCell className="text-right">
                          {r.status === 'PENDING' && (
                            <div className="flex justify-end gap-2">
                              <Button size="sm" disabled={decide.isPending} onClick={() => act(r, 'approve')}>
                                <Check /> Approve
                              </Button>
                              <Button size="sm" variant="outline" disabled={decide.isPending} onClick={() => act(r, 'deny')}>
                                <X /> Deny
                              </Button>
                            </div>
                          )}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                  {paging.total === 0 && (
                    <TableRow>
                      <TableCell colSpan={5} className="h-20 text-center text-muted-foreground">
                        No requests match “{paging.search.trim()}”.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
            {!isLoading && all.length > 0 && <TableFooter paging={paging} noun="requests" />}
          </Card>
        </>
      )}
    </>
  )
}
