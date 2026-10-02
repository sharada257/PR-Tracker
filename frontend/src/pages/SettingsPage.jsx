import { useState } from 'react'
import { toast } from 'sonner'
import { Check, Copy, RefreshCw, RotateCcw } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Progress } from '@/components/ui/progress'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import RepositorySelector from '@/components/RepositorySelector'
import { Section } from '@/components/common'
import { formatDateTime, timeAgo } from '@/lib/format'
import { useAuditLog, useImportStatus, useIsAdmin, useMe, useSyncNow, useWebhookInfo } from '@/lib/queries'

function CopyField({ value }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex gap-2">
      <Input readOnly value={value} className="font-mono text-xs" onFocus={(e) => e.target.select()} />
      <Button
        variant="outline"
        size="icon"
        aria-label="Copy"
        onClick={async () => {
          await navigator.clipboard.writeText(value)
          setCopied(true)
          setTimeout(() => setCopied(false), 1500)
        }}
      >
        {copied ? <Check /> : <Copy />}
      </Button>
    </div>
  )
}

const STATUS_VARIANT = { done: 'secondary', failed: 'destructive' }
const SYNC_VARIANT = { COMPLETED: 'secondary', FAILED: 'destructive', PARTIAL: 'outline', RUNNING: 'outline', PENDING: 'outline' }
const SYNC_LABEL = { COMPLETED: 'Completed', FAILED: 'Failed', PARTIAL: 'Partly completed', RUNNING: 'Running', PENDING: 'Waiting' }

/** What the latest sync did: repositories, pull requests and reviews updated. */
function SyncSummary({ sync }) {
  if (!sync?.status) {
    return <p className="mb-4 text-sm text-muted-foreground">No sync has run yet. Press Sync GitHub to start one.</p>
  }
  const numbers = [
    ['Repositories updated', sync.repositories_updated],
    ['Pull requests updated', sync.pull_requests_updated],
    ['Reviews updated', sync.reviews_updated],
  ]
  return (
    <div className="mb-4 space-y-3 rounded-lg border bg-muted/30 p-4">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <Badge variant={SYNC_VARIANT[sync.status] ?? 'outline'}>{SYNC_LABEL[sync.status] ?? sync.status}</Badge>
        <span className="text-muted-foreground">
          Last synced {sync.last_synced_at ? timeAgo(sync.last_synced_at) : 'never'}
          {sync.started_at && <> · latest run started {formatDateTime(sync.started_at)}</>}
        </span>
      </div>
      <dl className="grid grid-cols-3 gap-3">
        {numbers.map(([label, value]) => (
          <div key={label}>
            <dd className="text-2xl font-semibold tabular-nums">{value ?? 0}</dd>
            <dt className="text-xs text-muted-foreground">{label}</dt>
          </div>
        ))}
      </dl>
      {sync.error && <p className="text-xs text-destructive">{sync.error}</p>}
    </div>
  )
}

function SyncSection() {
  const { data } = useImportStatus()
  const sync = useSyncNow()
  const isAdmin = useIsAdmin()
  const [open, setOpen] = useState(false)
  const repos = data?.repositories ?? []

  return (
    <Section
      title="Sync"
      description="Webhooks keep things current in near real time. A scheduled sync re-checks recent PRs every 6 hours to catch anything missed."
      actions={
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={sync.isPending || data?.active}
            onClick={() =>
              sync.mutate(undefined, {
                onSuccess: (r) =>
                  r.already_running ? toast.info('A sync is already running') : toast.success('Sync started'),
                onError: (e) => toast.error(e.message),
              })
            }
          >
            <RefreshCw className={sync.isPending || data?.active ? 'animate-spin' : ''} /> Sync GitHub
          </Button>
          {isAdmin && (
          <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm">
                <RotateCcw /> Re-import all
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Re-import everything from GitHub?</DialogTitle>
                <DialogDescription>
                  This re-fetches the full history of every tracked repository. Existing data is updated in place
                  (no duplicates). It can take a while for large histories and uses GitHub API quota.
                </DialogDescription>
              </DialogHeader>
              <DialogFooter>
                <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                <Button
                  onClick={() =>
                    sync.mutate(
                      { full: true },
                      {
                        onSuccess: () => {
                          toast.success('Full re-import started')
                          setOpen(false)
                        },
                        onError: (e) => toast.error(e.message),
                      },
                    )
                  }
                >
                  Re-import
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
          )}
        </div>
      }
    >
      <SyncSummary sync={data?.sync} />
      {repos.length === 0 ? (
        <p className="text-sm text-muted-foreground">No tracked repositories yet.</p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Repository</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="w-56">Progress</TableHead>
              <TableHead>Last synced</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {repos.map((r) => {
              const pct = r.status === 'done' ? 100 : r.total ? Math.min((r.processed / r.total) * 100, 100) : 0
              return (
                <TableRow key={r.id}>
                  <TableCell className="font-medium">{r.full_name}</TableCell>
                  <TableCell>
                    <Badge variant={STATUS_VARIANT[r.status] ?? 'outline'}>{r.status}</Badge>
                    {r.error && <div className="mt-1 max-w-xs text-xs text-muted-foreground">{r.error}</div>}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <Progress value={pct} className="h-1.5" />
                      <span className="w-16 text-right text-xs tabular-nums text-muted-foreground">
                        {r.processed}
                        {r.total ? ` / ${r.total}` : ''}
                      </span>
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {r.last_synced_at ? timeAgo(r.last_synced_at) : '—'}
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      )}
    </Section>
  )
}

/** The organization's GitHub App connection and the webhook settings (admins only). */
function ConnectionSection() {
  const { data } = useWebhookInfo()
  if (!data) return null
  const inst = data.installation
  return (
    <Section
      title="GitHub connection"
      description="The GitHub App installed on your organization, and the webhook it uses for near real-time updates."
      actions={
        <Badge variant={data.secret_configured ? 'secondary' : 'destructive'}>
          {data.secret_configured ? 'Secret configured' : 'Secret missing'}
        </Badge>
      }
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          {inst ? (
            <>
              <Badge variant={inst.active ? 'secondary' : 'destructive'}>{inst.active ? 'App installed' : 'App inactive'}</Badge>
              <span>
                <span className="font-medium">{inst.account}</span>
                <span className="text-muted-foreground"> · {inst.repository_selection === 'all' ? 'all repositories' : 'selected repositories'}</span>
              </span>
            </>
          ) : (
            <Badge variant="destructive">App not installed</Badge>
          )}
          {data.github_app?.install_url && (
            <a className="text-primary hover:underline" href={data.github_app.install_url} target="_blank" rel="noreferrer">
              Manage repository access on GitHub
            </a>
          )}
        </div>
        <div className="space-y-1.5">
          <div className="text-sm font-medium">Payload URL</div>
          <CopyField value={data.url} />
          <p className="text-xs text-muted-foreground">
            Content type <code>application/json</code>. Subscribe to: {data.events.map((e) => <code key={e} className="mr-1">{e}</code>)}
            Set the same secret as <code>GITHUB_WEBHOOK_SECRET</code> on the server (it is never shown here).
          </p>
        </div>
        {!data.secret_configured && (
          <Alert variant="destructive">
            <AlertTitle>Webhooks are disabled</AlertTitle>
            <AlertDescription>
              Set <code>GITHUB_WEBHOOK_SECRET</code> on the server; unsigned deliveries are always rejected.
            </AlertDescription>
          </Alert>
        )}
        <div>
          <div className="mb-2 text-sm font-medium">Recent deliveries</div>
          {data.recent_deliveries.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nothing received yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Event</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Received</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.recent_deliveries.map((d) => (
                  <TableRow key={d.id}>
                    <TableCell className="font-mono text-xs">{d.event}.{d.action}</TableCell>
                    <TableCell>
                      <Badge variant={d.status === 'failed' ? 'destructive' : d.status === 'processed' ? 'secondary' : 'outline'}
                        title={d.error}>
                        {d.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(d.received_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      </div>
    </Section>
  )
}

function ActivitySection() {
  const { data } = useAuditLog()
  return (
    <Section title="Security activity" description="Your sign-ins and configuration changes.">
      {!data?.length ? (
        <p className="text-sm text-muted-foreground">No activity recorded.</p>
      ) : (
        <ul className="divide-y text-sm">
          {data.slice(0, 10).map((e) => (
            <li key={e.id} className="flex items-center justify-between py-2">
              <span className="font-mono text-xs">{e.action}</span>
              <span className="text-muted-foreground">{formatDateTime(e.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

export default function SettingsPage() {
  const isAdmin = useIsAdmin()
  const org = useMe().data?.organization
  return (
    <>
      {isAdmin && (
        <Section
          title="Tracked repositories"
          description={`Pull requests in these ${org?.name ?? ''} repositories are imported and kept in sync for everyone in the organization.`}
        >
          <RepositorySelector submitLabel="Save & import" />
        </Section>
      )}
      <SyncSection />
      {isAdmin && <ConnectionSection />}
      <ActivitySection />
    </>
  )
}
