import { useState } from 'react'
import { toast } from 'sonner'
import { MoreHorizontal, UserCheck, UserX } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { UserAvatar } from '@/components/common'
import { SearchBox, TableFooter, useSearchPaging } from '@/components/TablePager'
import { formatDateTime, timeAgo } from '@/lib/format'
import { useMe, useMembers, useUpdateMember } from '@/lib/queries'

const memberText = (m) => `${m.display_name} ${m.github_username}`

/** What the confirmation popup says for each kind of change. */
function describe(pending) {
  const name = pending.member.display_name
  if (pending.kind === 'revoke') {
    return {
      title: `Remove ${name}'s access?`,
      body: `${name} will be signed out of PR Tracker and will no longer see this organization, even if they still have access on GitHub. You can restore their access later.`,
      action: 'Remove access',
      destructive: true,
      done: `${name} no longer has access`,
      payload: { active: false },
    }
  }
  if (pending.role === 'ADMIN') {
    return {
      title: `Make ${name} an admin?`,
      body: `Admins can see everyone's data, manage members and roles, choose which repositories are tracked, and re-import history.`,
      action: 'Make admin',
      destructive: false,
      done: `${name} is now an admin`,
      payload: { role: 'ADMIN' },
    }
  }
  return {
    title: `Make ${name} a member?`,
    body: `${name} will lose admin rights and will only see their own pull requests, reviews and analytics.`,
    action: 'Make member',
    destructive: true,
    done: `${name} is now a member`,
    payload: { role: 'MEMBER' },
  }
}

/** Admins: who is in the organization, their role, and whether they still have access. */
export default function MembersPage() {
  const { data: members, isLoading } = useMembers()
  const me = useMe().data?.user
  const update = useUpdateMember()
  const all = members ?? []
  const paging = useSearchPaging(all, memberText)
  // A change waits here until the admin confirms it in the popup.
  const [pending, setPending] = useState(null)
  const info = pending && describe(pending)

  const change = (member, body, message, onDone) =>
    update.mutate(
      { id: member.id, ...body },
      {
        onSuccess: () => toast.success(message),
        onError: (e) => toast.error(e.message),
        onSettled: onDone,
      },
    )
  const confirm = () => change(pending.member, info.payload, info.done, () => setPending(null))

  return (
    <>
      <p className="text-sm text-muted-foreground">
        People appear here when they first sign in with GitHub. The first person to sign in became an admin. Repository
        access always comes from GitHub: removing someone here only removes their access to PR Tracker.
      </p>
      <SearchBox value={paging.search} onChange={paging.setSearch} placeholder="Search members…" />
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
                <TableHead>Member</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Access</TableHead>
                <TableHead>Last sign-in</TableHead>
                <TableHead className="w-10"><span className="sr-only">Actions</span></TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {paging.rows.map((m) => {
                const active = m.status === 'ACTIVE'
                const self = m.id === me?.id
                return (
                  <TableRow key={m.id} className={active ? undefined : 'opacity-60'}>
                    <TableCell>
                      <div className="flex items-center gap-3">
                        <UserAvatar login={m.github_username} src={m.avatar_url} className="size-8" />
                        <div className="leading-tight">
                          <div className="font-medium">{m.display_name}{self && <span className="text-muted-foreground"> (you)</span>}</div>
                          <div className="text-xs text-muted-foreground">@{m.github_username}</div>
                        </div>
                      </div>
                    </TableCell>
                    <TableCell>
                      <Select
                        value={m.role}
                        disabled={!active}
                        onValueChange={(role) => role !== m.role && setPending({ kind: 'role', member: m, role })}
                      >
                        <SelectTrigger size="sm" className="w-[120px]" aria-label={`Role for ${m.github_username}`}>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="ADMIN">Admin</SelectItem>
                          <SelectItem value="MEMBER">Member</SelectItem>
                        </SelectContent>
                      </Select>
                    </TableCell>
                    <TableCell>
                      {active ? (
                        <Badge variant="outline">Active</Badge>
                      ) : (
                        <Badge variant="destructive" title={m.access_blocked ? 'Removed by an admin' : 'No longer has access on GitHub'}>
                          Revoked
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-muted-foreground" title={m.last_login ? formatDateTime(m.last_login) : undefined}>
                      {m.last_login ? timeAgo(m.last_login) : 'Never'}
                    </TableCell>
                    <TableCell>
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" aria-label={`Actions for ${m.github_username}`}>
                            <MoreHorizontal />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          {active ? (
                            <DropdownMenuItem
                              disabled={self}
                              className="text-destructive focus:text-destructive"
                              onClick={() => setPending({ kind: 'revoke', member: m })}
                            >
                              <UserX /> Remove access
                            </DropdownMenuItem>
                          ) : (
                            <DropdownMenuItem onClick={() => change(m, { active: true }, `${m.display_name} has access again`)}>
                              <UserCheck /> Restore access
                            </DropdownMenuItem>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </TableCell>
                  </TableRow>
                )
              })}
              {paging.total === 0 && (
                <TableRow>
                  <TableCell colSpan={5} className="h-20 text-center text-muted-foreground">
                    {all.length ? `No members match “${paging.search.trim()}”.` : 'No members yet.'}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        )}
        {!isLoading && all.length > 0 && <TableFooter paging={paging} />}
      </Card>

      <Dialog open={!!pending} onOpenChange={(open) => !open && !update.isPending && setPending(null)}>
        <DialogContent>
          {info && (
            <>
              <DialogHeader>
                <DialogTitle>{info.title}</DialogTitle>
                <DialogDescription>{info.body}</DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="outline" disabled={update.isPending} onClick={() => setPending(null)}>
                  Cancel
                </Button>
                <Button variant={info.destructive ? 'destructive' : 'default'} disabled={update.isPending} onClick={confirm}>
                  {update.isPending ? 'Saving…' : info.action}
                </Button>
              </DialogFooter>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}
