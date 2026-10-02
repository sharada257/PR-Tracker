import { useEffect } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import FilterBar from '@/components/FilterBar'
import PRTable from '@/components/PRTable'
import { pickFilters, usePullRequests } from '@/lib/queries'
import { useFilters } from '@/lib/useFilters'

export default function PullRequestsPage() {
  const state = useFilters({ defaultRange: 'this_year' })
  const { filters, searchParams, setSearchParams } = state
  const page = Number(searchParams.get('page') || 1)
  const ordering = searchParams.get('ordering') || '-created_at'
  const pageSize = Number(searchParams.get('page_size') || 25)

  const { data, isLoading, isFetching, isError, error } = usePullRequests({
    ...pickFilters(filters),
    page,
    page_size: pageSize,
    ordering,
  })

  const total = data?.count ?? 0
  const pages = Math.max(Math.ceil(total / pageSize), 1)

  const setParam = (patch) =>
    setSearchParams(
      (current) => {
        const next = new URLSearchParams(current)
        for (const [k, v] of Object.entries(patch)) {
          if (v === undefined || v === '') next.delete(k)
          else next.set(k, String(v))
        }
        return next
      },
      { replace: true },
    )

  // The API 404s when the requested page no longer exists (e.g. after filtering).
  useEffect(() => {
    if (isError && error?.status === 404 && page > 1) setParam({ page: 1 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isError, error, page])

  return (
    <>
      <FilterBar state={state} showSearch showViewing showAuthor={filters.scope === 'all'} inline />
      <Card className="gap-0 overflow-hidden py-0">
        <PRTable
          rows={data?.results ?? []}
          loading={isLoading}
          ordering={ordering}
          pageSize={10}
          showAuthor={filters.scope === 'all'}
          onSort={(value) => setParam({ ordering: value, page: 1 })}
        />
        <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm">
          <div className="flex items-center gap-2 text-muted-foreground">
            Rows per page
            <Select value={String(pageSize)} onValueChange={(v) => setParam({ page_size: v, page: 1 })}>
              <SelectTrigger size="sm" className="w-[72px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[10, 25, 50, 100].map((n) => (
                  <SelectItem key={n} value={String(n)}>
                    {n}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-muted-foreground">
              Page {Math.min(page, pages)} of {pages}
              {isFetching && !isLoading ? ' · updating…' : ''}
            </span>
            <Button variant="outline" size="icon" disabled={page <= 1} onClick={() => setParam({ page: page - 1 })}
              aria-label="Previous page">
              <ChevronLeft />
            </Button>
            <Button variant="outline" size="icon" disabled={page >= pages} onClick={() => setParam({ page: page + 1 })}
              aria-label="Next page">
              <ChevronRight />
            </Button>
          </div>
        </div>
      </Card>
    </>
  )
}
