import { Link, useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, ExternalLink, GitBranch } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import Timeline from '@/components/Timeline'
import { CalculatedBadge, EmptyState, LabelChip, Section, StatusBadge, UserAvatar } from '@/components/common'
import { formatDate, formatDateTime, formatDuration, timeAgo } from '@/lib/format'
import { REVIEW_STATE_LABEL } from '@/lib/status'
import { usePullRequest } from '@/lib/queries'

function Fact({ label, children }) {
  return (
    <div className="space-y-1">
      <div className="text-xs uppercase tracking-wide text-muted-foreground">{label}</div>
      <div className="text-sm font-medium">{children}</div>
    </div>
  )
}

export default function PullRequestDetailPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const { data: pr, isLoading, isError } = usePullRequest(id)

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-2/3" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }
  if (isError || !pr) {
    return (
      <EmptyState title="Pull request not found" description="It may belong to a repository you no longer track.">
        <Button variant="outline" onClick={() => navigate('/pull-requests')}>Back to pull requests</Button>
      </EmptyState>
    )
  }

  return (
    <>
      <div>
        <Button asChild variant="ghost" size="sm" className="-ml-2 mb-2">
          <Link to="/pull-requests">
            <ArrowLeft /> All pull requests
          </Link>
        </Button>
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-2">
            <h2 className="text-2xl font-semibold tracking-tight">{pr.title}</h2>
            <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
              <Link to={`/pull-requests?repository=${pr.repository.id}`} className="hover:underline">
                {pr.repository.full_name}
              </Link>
              <span>#{pr.number}</span>
              <StatusBadge status={pr.status} />
              {pr.draft && <Badge variant="secondary">Draft</Badge>}
              <span className="flex items-center gap-1">
                <GitBranch className="size-3.5" /> {pr.head_branch} → {pr.base_branch}
              </span>
            </div>
          </div>
          <Button asChild>
            <a href={pr.url} target="_blank" rel="noreferrer">
              <ExternalLink /> Open in GitHub
            </a>
          </Button>
        </div>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          <Section title="Timeline" description="Lifecycle of this PR, from creation to merge.">
            <Timeline items={pr.timeline} />
          </Section>

          <Section title={`Reviews (${pr.reviews.length})`} description="GitHub's original review states are preserved.">
            {pr.reviews.length === 0 ? (
              <p className="text-sm text-muted-foreground">No reviews submitted.</p>
            ) : (
              <ul className="divide-y">
                {pr.reviews.map((review) => (
                  <li key={review.id} className="flex items-start gap-3 py-3 first:pt-0 last:pb-0">
                    <UserAvatar login={review.reviewer} src={review.avatar_url} className="size-7" />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-medium">{review.reviewer}</span>
                        <Badge variant="outline" title={review.state}>
                          {REVIEW_STATE_LABEL[review.state] ?? review.state}
                        </Badge>
                        <span className="text-xs text-muted-foreground">{formatDateTime(review.submitted_at)}</span>
                      </div>
                      {review.body && (
                        <p className="mt-1 whitespace-pre-line text-sm text-muted-foreground">{review.body}</p>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>

          {pr.description && (
            <Section title="Description">
              <p className="max-h-72 overflow-auto whitespace-pre-wrap text-sm text-muted-foreground">
                {pr.description}
              </p>
            </Section>
          )}
        </div>

        <div className="space-y-6">
          <Section title="Details">
            <div className="grid grid-cols-2 gap-4">
              <Fact label="Created">{formatDate(pr.created_at)}</Fact>
              <Fact label="Updated">{timeAgo(pr.updated_at)}</Fact>
              <Fact label="Approved">{formatDate(pr.approved_at)}</Fact>
              <Fact label={pr.merged_at ? 'Merged' : 'Closed'}>{formatDate(pr.merged_at || pr.closed_at)}</Fact>
              <Fact label="Author">
                <span className="flex items-center gap-1.5">
                  <UserAvatar login={pr.author.login} src={pr.author.avatar_url} className="size-5" />
                  {pr.author.login}
                </span>
              </Fact>
              <Fact label="Changes">
                <span className="text-[var(--status-approved)]">+{pr.additions}</span>{' '}
                <span className="text-[var(--status-closed)]">−{pr.deletions}</span>
                <span className="text-muted-foreground"> · {pr.changed_files} files</span>
              </Fact>
            </div>
          </Section>

          <Section title="Metrics" actions={<CalculatedBadge />}>
            <div className="grid grid-cols-2 gap-4">
              <Fact label="First review">{formatDuration(pr.time_to_first_review_hours)}</Fact>
              <Fact label="Time to merge">{formatDuration(pr.time_to_merge_hours)}</Fact>
              <Fact label="Reviews">{pr.review_count}</Fact>
              <Fact label="Review cycles">{pr.review_cycles}</Fact>
            </div>
          </Section>

          <Section title="Reviewers" description="Includes assignment history.">
            {pr.reviewer_history.length === 0 && pr.reviews.length === 0 ? (
              <p className="text-sm text-muted-foreground">No reviewers assigned.</p>
            ) : (
              <ul className="space-y-3">
                {pr.reviewers.map((r) => {
                  const history = pr.reviewer_history.filter((h) => h.username.toLowerCase() === r.username.toLowerCase())
                  return (
                    <li key={r.username} className="flex items-start gap-3">
                      <UserAvatar login={r.username} src={r.avatar_url} className="size-7" />
                      <div className="text-sm">
                        <Link to={`/pull-requests?reviewer=${r.username}`} className="font-medium hover:underline">
                          {r.username}
                        </Link>
                        {history.map((h, i) => (
                          <div key={i} className="text-xs text-muted-foreground">
                            Assigned {formatDate(h.assigned_at)}
                            {h.removed_at ? ` · request closed ${formatDate(h.removed_at)}` : ''}
                          </div>
                        ))}
                        {history.length === 0 && (
                          <div className="text-xs text-muted-foreground">Reviewed without a formal request</div>
                        )}
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </Section>

          <Section title="Labels">
            {pr.labels.length ? (
              <div className="flex flex-wrap gap-1.5">
                {pr.labels.map((l) => (
                  <LabelChip key={l.name} label={l} />
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No labels.</p>
            )}
          </Section>
        </div>
      </div>
    </>
  )
}
