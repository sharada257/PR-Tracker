import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { AlertTriangle, Link2, Plug, RefreshCw, Send, Unplug, Wand2 } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { UserAvatar } from '@/components/common'
import { SearchBox, TableFooter, useSearchPaging } from '@/components/TablePager'
import {
  useDisconnectSlack,
  useSetSlackLink,
  useSlack,
  useSlackAutoMatch,
  useSlackLinks,
  useSlackTest,
  useUpdateSlack,
} from '@/lib/queries'

const NONE = '__none__'
const ERRORS = {
  not_configured: 'Slack is not configured on the server yet.',
  cancelled: 'Slack authorisation was cancelled.',
  invalid_state: 'The Slack connection expired. Please try again.',
  slack_error: 'Slack rejected the connection. Please try again.',
}
const memberText = (m) => `${m.display_name} ${m.github_username}`

function Setting({ title, description, checked, onChange, disabled, children }) {
  return (
    <div className="flex items-start justify-between gap-4 py-3">
      <div className="space-y-1">
        <div className="text-sm font-medium">{title}</div>
        <div className="text-sm text-muted-foreground">{description}</div>
        {children}
      </div>
      <Switch checked={checked} onCheckedChange={onChange} disabled={disabled} aria-label={title} />
    </div>
  )
}

function Connection({ slack }) {
  const disconnect = useDisconnectSlack()
  const [confirm, setConfirm] = useState(false)

  if (!slack.configured) {
    return (
      <Alert>
        <AlertTriangle />
        <AlertTitle>Slack is not set up on this server</AlertTitle>
        <AlertDescription className="space-y-2">
          <p>
            Create a Slack App at api.slack.com/apps (From scratch), add the bot scopes <code>chat:write</code>,{' '}
            <code>im:write</code>, <code>users:read</code> and <code>users:read.email</code>, and set its Redirect URL to:
          </p>
          <code className="block break-all rounded bg-muted px-2 py-1 text-xs">{slack.redirect_uri}</code>
          <p>
            Then put the App&apos;s Client ID and Client Secret in <code>.env</code> as <code>SLACK_CLIENT_ID</code> and{' '}
            <code>SLACK_CLIENT_SECRET</code> and restart the backend. Slack requires an https Redirect URL, so use a
            tunnel such as ngrok and set <code>SLACK_REDIRECT_BASE</code> to it.
          </p>
        </AlertDescription>
      </Alert>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Connection
          {slack.connected && (slack.active ? <Badge variant="outline">Connected</Badge> : <Badge variant="destructive">Paused</Badge>)}
        </CardTitle>
        <CardDescription>
          {slack.connected
            ? `Messages are sent as the PR Tracker bot in ${slack.team_name || 'your Slack workspace'}.`
            : 'Connect your Slack workspace so PR Tracker can message reviewers directly.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {slack.connected && !slack.active && (
          <Alert variant="destructive">
            <AlertTriangle />
            <AlertTitle>Notifications are paused</AlertTitle>
            <AlertDescription>
              Slack rejected our access ({slack.last_error || 'unknown reason'}). Reconnect to resume.
            </AlertDescription>
          </Alert>
        )}
        <div className="flex flex-wrap gap-2">
          <Button asChild>
            <a href="/api/slack/connect">
              <Plug /> {slack.connected ? 'Reconnect' : 'Add to Slack'}
            </a>
          </Button>
          {slack.connected && (
            <Button variant="outline" onClick={() => setConfirm(true)}>
              <Unplug /> Disconnect
            </Button>
          )}
        </div>
      </CardContent>
      <Dialog open={confirm} onOpenChange={(open) => !disconnect.isPending && setConfirm(open)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Disconnect Slack?</DialogTitle>
            <DialogDescription>
              PR Tracker will stop sending messages and forget the connection and everyone&apos;s Slack link. You can
              connect again at any time.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" disabled={disconnect.isPending} onClick={() => setConfirm(false)}>Cancel</Button>
            <Button
              variant="destructive"
              disabled={disconnect.isPending}
              onClick={() =>
                disconnect.mutate(undefined, {
                  onSuccess: () => {
                    toast.success('Slack disconnected')
                    setConfirm(false)
                  },
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              {disconnect.isPending ? 'Disconnecting…' : 'Disconnect'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}

function Notifications({ slack }) {
  const update = useUpdateSlack()
  const s = slack.settings
  const save = (body) =>
    update.mutate(body, { onSuccess: () => toast.success('Saved'), onError: (e) => toast.error(e.message) })
  return (
    <Card>
      <CardHeader>
        <CardTitle>What gets sent</CardTitle>
        <CardDescription>Direct messages to the people involved. Only people linked to a Slack account below are messaged.</CardDescription>
      </CardHeader>
      <CardContent className="divide-y">
        <Setting
          title="Review requests"
          description="Message a reviewer as soon as someone asks them to review a pull request."
          checked={s.notify_review_requests}
          onChange={(v) => save({ notify_review_requests: v })}
        />
        <Setting
          title="Review outcomes"
          description="Tell the author when a reviewer approves or asks for changes, and tell a reviewer when the author pushes changes after their feedback."
          checked={s.notify_review_outcomes}
          onChange={(v) => save({ notify_review_outcomes: v })}
        />
        <Setting
          title="Reminders"
          description="Nudge a reviewer when a review they owe has been waiting."
          checked={s.send_reminders}
          onChange={(v) => save({ send_reminders: v })}
        >
          {s.send_reminders && (
            <div className="flex flex-wrap items-center gap-2 pt-2 text-sm text-muted-foreground">
              Remind after
              <Select value={String(s.reminder_after_hours)} onValueChange={(v) => save({ reminder_after_hours: Number(v) })}>
                <SelectTrigger size="sm" className="w-[110px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {[4, 8, 24, 48, 72].map((h) => (
                    <SelectItem key={h} value={String(h)}>{h < 24 ? `${h} hours` : `${h / 24} day${h > 24 ? 's' : ''}`}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              , at most
              <Select value={String(s.max_reminders)} onValueChange={(v) => save({ max_reminders: Number(v) })}>
                <SelectTrigger size="sm" className="w-[90px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {[1, 2, 3, 4, 5].map((n) => <SelectItem key={n} value={String(n)}>{n}×</SelectItem>)}
                </SelectContent>
              </Select>
              per review
            </div>
          )}
        </Setting>
        {s.send_reminders && (
          <Setting
            title="Weekdays, working hours only"
            description="Hold reminders until Monday to Friday, 09:00–18:00. Review requests are always sent right away."
            checked={s.business_hours_only}
            onChange={(v) => save({ business_hours_only: v })}
          />
        )}
      </CardContent>
    </Card>
  )
}

function People({ slack }) {
  const { data, isLoading, isError, error, isFetching, refetch } = useSlackLinks(slack.connected)
  const setLink = useSetSlackLink()
  const match = useSlackAutoMatch()
  const test = useSlackTest()
  const members = data?.members ?? []
  const people = data?.slack_users ?? []
  const paging = useSearchPaging(members, memberText)
  const unlinked = members.filter((m) => !m.slack_user_id).length

  const choose = (m, value) =>
    setLink.mutate(
      { userId: m.id, slack_user_id: value === NONE ? null : value },
      { onError: (e) => toast.error(e.message) },
    )

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2"><Link2 className="size-4" /> People</CardTitle>
        <CardDescription>
          Who is who on Slack. We match by email, then by username or full name; you can fix any of them by hand.
          {unlinked > 0 && ` ${unlinked} of ${members.length} still not linked, and they won't be messaged.`}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SearchBox value={paging.search} onChange={paging.setSearch} placeholder="Search people…" />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              disabled={match.isPending}
              onClick={() =>
                match.mutate(undefined, {
                  onSuccess: (r) => toast.success(r.linked ? `Linked ${r.linked} more` : 'Nobody new to link'),
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              <Wand2 /> Match automatically
            </Button>
            <Button variant="outline" size="sm" disabled={isFetching} onClick={() => refetch()}>
              <RefreshCw className={isFetching ? 'animate-spin' : undefined} /> Refresh
            </Button>
            <Button
              size="sm"
              disabled={test.isPending}
              onClick={() =>
                test.mutate(undefined, {
                  onSuccess: () => toast.success('Sent. Check your Slack DMs.'),
                  onError: (e) => toast.error(e.message),
                })
              }
            >
              <Send /> Send me a test
            </Button>
          </div>
        </div>
        {isError ? (
          <Alert variant="destructive"><AlertTriangle /><AlertTitle>Couldn&apos;t read your Slack workspace</AlertTitle><AlertDescription>{error.message}</AlertDescription></Alert>
        ) : (
          <div className="overflow-hidden rounded-lg border">
            {isLoading ? (
              <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10" />)}</div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Member</TableHead>
                    <TableHead>Slack account</TableHead>
                    <TableHead>Matched</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {paging.rows.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell>
                        <div className="flex items-center gap-3">
                          <UserAvatar login={m.github_username} src={m.avatar_url} className="size-8" />
                          <div className="leading-tight">
                            <div className="font-medium">{m.display_name}</div>
                            <div className="text-xs text-muted-foreground">@{m.github_username}</div>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Select value={m.slack_user_id ?? NONE} onValueChange={(v) => choose(m, v)}>
                          <SelectTrigger size="sm" className="w-[240px]" aria-label={`Slack account for ${m.github_username}`}>
                            <SelectValue />
                          </SelectTrigger>
                          <SelectContent>
                            <SelectItem value={NONE}>Not linked</SelectItem>
                            {people.map((p) => (
                              <SelectItem key={p.id} value={p.id}>
                                {p.real_name || p.name}{p.name && p.real_name ? ` (@${p.name})` : ''}
                              </SelectItem>
                            ))}
                          </SelectContent>
                        </Select>
                      </TableCell>
                      <TableCell>
                        {m.source ? <Badge variant="secondary">{m.source === 'auto' ? 'Automatic' : 'By hand'}</Badge> : <span className="text-muted-foreground">—</span>}
                      </TableCell>
                    </TableRow>
                  ))}
                  {paging.total === 0 && (
                    <TableRow>
                      <TableCell colSpan={3} className="h-20 text-center text-muted-foreground">
                        {members.length ? `No one matches “${paging.search.trim()}”.` : 'No members yet.'}
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            )}
            {!isLoading && members.length > 0 && <TableFooter paging={paging} noun="people" />}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** Admins: connect Slack, choose what is sent, and link people to their Slack accounts. */
export default function SlackPage() {
  const { data: slack, isLoading } = useSlack()
  const [params, setParams] = useSearchParams()

  useEffect(() => {
    if (params.get('connected')) toast.success('Slack connected')
    const error = ERRORS[params.get('error')]
    if (error) toast.error(error)
    if (params.get('connected') || params.get('error')) setParams({}, { replace: true })
  }, [params, setParams])

  if (isLoading || !slack) {
    return <div className="space-y-3">{[0, 1].map((i) => <Skeleton key={i} className="h-32" />)}</div>
  }
  return (
    <>
      <Connection slack={slack} />
      {slack.connected && slack.active && (
        <>
          <Notifications slack={slack} />
          <People slack={slack} />
        </>
      )}
    </>
  )
}
