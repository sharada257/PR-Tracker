import {
  CheckCircle2,
  CircleDot,
  FileEdit,
  GitCommit,
  GitMerge,
  GitPullRequest,
  MessageSquare,
  RotateCcw,
  UserMinus,
  UserPlus,
  XCircle,
} from 'lucide-react'
import { formatDateTime } from '@/lib/format'

const KIND = {
  created: { icon: GitPullRequest, color: 'text-chart-1', label: 'created' },
  reviewer_assigned: { icon: UserPlus, color: 'text-muted-foreground' },
  reviewer_removed: { icon: UserMinus, color: 'text-muted-foreground' },
  review_commented: { icon: MessageSquare, color: 'text-muted-foreground' },
  review_dismissed: { icon: XCircle, color: 'text-muted-foreground' },
  changes_requested: { icon: FileEdit, color: 'text-[var(--status-changes_requested)]' },
  review_approved: { icon: CheckCircle2, color: 'text-[var(--status-approved)]' },
  commits_pushed: { icon: GitCommit, color: 'text-chart-1' },
  reopened: { icon: RotateCcw, color: 'text-chart-1' },
  ready_for_review: { icon: CircleDot, color: 'text-chart-1' },
  converted_to_draft: { icon: CircleDot, color: 'text-muted-foreground' },
  merged: { icon: GitMerge, color: 'text-[var(--status-merged)]' },
  closed: { icon: XCircle, color: 'text-[var(--status-closed)]' },
}

export default function Timeline({ items }) {
  if (!items?.length) return <p className="text-sm text-muted-foreground">No timeline events yet.</p>
  return (
    <ol className="relative space-y-6 border-l pl-6">
      {items.map((item, index) => {
        const meta = KIND[item.kind] ?? { icon: CircleDot, color: 'text-muted-foreground' }
        const Icon = meta.icon
        return (
          <li key={`${item.kind}-${item.at}-${index}`} className="relative">
            <span className="absolute -left-[37px] flex size-6 items-center justify-center rounded-full border bg-background">
              <Icon className={`size-3.5 ${meta.color}`} />
            </span>
            <div className="text-xs text-muted-foreground">{formatDateTime(item.at)}</div>
            <div className="text-sm font-medium">
              {item.title}
              {item.actor && item.kind !== 'reviewer_assigned' && item.kind !== 'reviewer_removed' && (
                <span className="font-normal text-muted-foreground"> · {item.actor}</span>
              )}
            </div>
            {item.detail && (
              <p className="mt-1 max-w-prose whitespace-pre-line text-sm text-muted-foreground">{item.detail}</p>
            )}
          </li>
        )
      })}
    </ol>
  )
}
