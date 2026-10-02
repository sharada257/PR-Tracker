import { createPortal } from 'react-dom'
import { Link, useOutletContext } from 'react-router-dom'
import { GitMerge, Lock, Settings } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { EmptyState } from '@/components/common'
import { timeAgo } from '@/lib/format'
import { useIsAdmin, useRepositories } from '@/lib/queries'

export default function RepositoriesPage() {
  const { data, isLoading } = useRepositories({ active: 'true', page_size: 200 })
  const repos = data?.results ?? []
  const { headerSlot } = useOutletContext()
  const isAdmin = useIsAdmin()

  return (
    <>
      {headerSlot &&
        isAdmin &&
        createPortal(
          <Button asChild variant="outline" size="sm" className="ml-auto">
            <Link to="/settings">
              <Settings /> Manage repositories
            </Link>
          </Button>,
          headerSlot,
        )}
      {isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-36" />
          ))}
        </div>
      ) : repos.length === 0 ? (
        <EmptyState title="No tracked repositories" description="Repositories appear here once the GitHub App has access to them." />
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {repos.map((repo) => (
            <Link key={repo.id} to={`/pull-requests?repository=${repo.id}`}>
              <Card className="h-full transition-colors hover:bg-muted/50">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <span className="truncate">{repo.full_name}</span>
                    {repo.private && <Lock className="size-3.5 text-muted-foreground" />}
                  </CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <div className="text-2xl font-semibold tabular-nums">{repo.pr_count}</div>
                      <div className="text-xs text-muted-foreground">PRs</div>
                    </div>
                    <div>
                      <div className="flex items-center gap-1 text-2xl font-semibold tabular-nums">
                        {repo.merged_count}
                        <GitMerge className="size-4 text-[var(--status-merged)]" />
                      </div>
                      <div className="text-xs text-muted-foreground">Merged</div>
                    </div>
                    <div>
                      <div className="text-2xl font-semibold tabular-nums">{repo.open_count}</div>
                      <div className="text-xs text-muted-foreground">Open</div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    {repo.import_status === 'done' ? (
                      <>Synced {repo.last_synced_at ? timeAgo(repo.last_synced_at) : 'recently'}</>
                    ) : (
                      <Badge variant="secondary">
                        {repo.import_status === 'failed' ? 'Import failed' : 'Importing…'}
                      </Badge>
                    )}
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </>
  )
}
