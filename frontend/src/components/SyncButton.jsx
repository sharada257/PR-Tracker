import { toast } from 'sonner'
import { RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { formatDateTime, timeAgo } from '@/lib/format'
import { useImportStatus, useSyncNow } from '@/lib/queries'

/** "Sync GitHub" with the time of the last sync. Available to every member of the organization. */
export default function SyncButton() {
  const { data } = useImportStatus()
  const sync = useSyncNow()
  const running = Boolean(data?.active) || sync.isPending
  const last = data?.last_synced_at
  const failed = data?.sync?.status === 'FAILED'

  return (
    <div className="flex items-center gap-2">
      <span
        className="hidden text-xs text-muted-foreground md:inline"
        title={last ? formatDateTime(last) : undefined}
      >
        {running
          ? `Syncing… ${data?.totals?.processed ?? 0} PRs`
          : failed
            ? 'Last sync failed'
            : last
              ? `Last synced ${timeAgo(last)}`
              : 'Not synced yet'}
      </span>
      <Button
        variant="outline"
        size="sm"
        disabled={running}
        onClick={() =>
          sync.mutate(undefined, {
            onSuccess: (r) =>
              r.already_running ? toast.info('A sync is already running') : toast.success('Sync started — fetching the latest from GitHub'),
            onError: (e) => toast.error(e.message),
          })
        }
      >
        <RefreshCw className={running ? 'animate-spin' : ''} /> Sync GitHub
      </Button>
    </div>
  )
}
