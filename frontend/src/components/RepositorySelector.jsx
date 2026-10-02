import { useEffect, useState } from 'react'
import { toast } from 'sonner'
import { Download, Lock, RefreshCw, Search } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'
import {
  useAuthConfig,
  useRepositories,
  useSelectRepositories,
  useSyncNow,
} from '@/lib/queries'

export function ImportBadge({ repo }) {
  if (!repo.is_active) return null
  const pct = repo.import_total ? Math.min(Math.round((repo.import_processed / repo.import_total) * 100), 100) : 0
  switch (repo.import_status) {
    case 'done':
      return <Badge variant="secondary">Imported · {repo.pr_count ?? repo.import_processed} PRs</Badge>
    case 'failed':
      return <Badge variant="destructive" title={repo.import_error}>Failed</Badge>
    case 'queued':
    case 'running':
      return (
        <div className="flex w-36 items-center gap-2">
          <Progress value={pct} className="h-1.5" />
          <span className="text-xs tabular-nums text-muted-foreground">{repo.import_processed}</span>
        </div>
      )
    default:
      return <Badge variant="outline">Not imported</Badge>
  }
}

/**
 * "Import: All repositories / Selected repositories". Selecting and saving
 * marks repositories as tracked and starts the (resumable) historical import.
 */
export default function RepositorySelector({ onStarted, submitLabel = 'Import & track' }) {
  const [mode, setMode] = useState('selected')
  const [search, setSearch] = useState('')
  const [debounced, setDebounced] = useState('')
  const [page, setPage] = useState(1)
  const [selected, setSelected] = useState(() => new Set())
  const [seeded, setSeeded] = useState(false)

  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(search.trim())
      setPage(1)
    }, 250)
    return () => clearTimeout(t)
  }, [search])

  const config = useAuthConfig().data
  const active = useRepositories({ active: 'true', page_size: 200 })
  const list = useRepositories({ search: debounced, page, page_size: 50 }, { refetchInterval: 4000 })
  const select = useSelectRepositories()
  const sync = useSyncNow()

  // Start from what is already tracked.
  useEffect(() => {
    if (!seeded && active.data) {
      setSelected(new Set(active.data.results.map((r) => r.id)))
      setSeeded(true)
    }
  }, [active.data, seeded])

  const repos = list.data?.results ?? []
  const total = list.data?.count ?? 0
  const pages = Math.max(Math.ceil(total / 50), 1)

  const toggle = (id) =>
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const submit = () =>
    select.mutate(
      { mode, repository_ids: [...selected], start_import: true },
      {
        onSuccess: (result) => {
          toast.success(
            result.import_started
              ? `Tracking ${result.active} repositor${result.active === 1 ? 'y' : 'ies'} — import started`
              : `Tracking ${result.active} repositor${result.active === 1 ? 'y' : 'ies'}`,
          )
          onStarted?.(result)
        },
        onError: (e) => toast.error(e.message),
      },
    )

  const canSubmit = mode === 'all' ? total > 0 || repos.length > 0 : selected.size > 0

  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="Import scope">
        {[
          { value: 'all', title: 'All repositories', text: 'Track every repository the GitHub App can access.' },
          { value: 'selected', title: 'Selected repositories', text: 'Choose exactly which repositories to track.' },
        ].map((option) => (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={mode === option.value}
            onClick={() => setMode(option.value)}
            className={cn(
              'rounded-lg border p-4 text-left transition-colors hover:bg-muted/50',
              mode === option.value && 'border-primary bg-muted/50 ring-1 ring-primary',
            )}
          >
            <div className="font-medium">{option.title}</div>
            <div className="text-sm text-muted-foreground">{option.text}</div>
          </button>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div className="relative min-w-60 flex-1">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            className="pl-9"
            placeholder="Search repositories…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="Search repositories"
          />
        </div>
        <Button
          variant="outline"
          disabled={sync.isPending}
          onClick={() =>
            sync.mutate(undefined, {
              onSuccess: () => toast.success('Looking for repositories the GitHub App can access…'),
              onError: (e) => toast.error(e.message),
            })
          }
        >
          <RefreshCw className={sync.isPending ? 'animate-spin' : ''} /> Check GitHub for changes
        </Button>
      </div>

      <div className={cn('rounded-lg border', mode === 'all' && 'opacity-60')}>
        {list.isLoading ? (
          <div className="space-y-2 p-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-8" />
            ))}
          </div>
        ) : repos.length === 0 ? (
          <div className="space-y-3 p-8 text-center text-sm text-muted-foreground">
            <p>
              {debounced
                ? 'No repositories match your search.'
                : 'No repositories found yet. They appear after the first sync, once the GitHub App has access to some.'}
            </p>
            {!debounced && config?.install_url && (
              <Button asChild variant="outline" size="sm">
                <a href={config.install_url}>Give the GitHub App access to more repositories</a>
              </Button>
            )}
          </div>
        ) : (
          <ul className="max-h-[420px] divide-y overflow-auto">
            {repos.map((repo) => (
              <li key={repo.id}>
                <Label
                  htmlFor={`repo-${repo.id}`}
                  className="flex cursor-pointer items-center gap-3 px-4 py-2.5 hover:bg-muted/50"
                >
                  <Checkbox
                    id={`repo-${repo.id}`}
                    checked={mode === 'all' || selected.has(repo.id)}
                    disabled={mode === 'all'}
                    onCheckedChange={() => toggle(repo.id)}
                  />
                  <span className="flex-1 truncate font-medium">{repo.full_name}</span>
                  {repo.private && <Lock className="size-3.5 text-muted-foreground" />}
                  <ImportBadge repo={{ ...repo, is_active: repo.is_active }} />
                </Label>
              </li>
            ))}
          </ul>
        )}
        {pages > 1 && (
          <div className="flex items-center justify-between border-t px-4 py-2 text-sm">
            <span className="text-muted-foreground">
              {total} repositories · page {page} of {pages}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button>
              <Button variant="outline" size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button>
            </div>
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {mode === 'all' ? `All ${total} repositories will be tracked.` : `${selected.size} selected.`} History import is
          resumable and runs in the background.
        </p>
        <Button disabled={!canSubmit || select.isPending} onClick={submit}>
          <Download /> {submitLabel}
        </Button>
      </div>
    </div>
  )
}
