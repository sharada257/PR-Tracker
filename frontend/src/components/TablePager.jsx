import { useMemo, useState } from 'react'
import { ChevronLeft, ChevronRight, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

const SIZES = [10, 25, 50, 100]

/** Client-side search + pagination for a list that is already loaded (members are few). */
export function useSearchPaging(items, text, initialSize = 10) {
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(initialSize)
  const needle = search.trim().toLowerCase()
  const matches = useMemo(
    () => (needle ? items.filter((item) => text(item).toLowerCase().includes(needle)) : items),
    [items, needle], // eslint-disable-line react-hooks/exhaustive-deps -- ``text`` is a stable accessor
  )
  const pages = Math.max(1, Math.ceil(matches.length / pageSize))
  const current = Math.min(page, pages)
  return {
    search,
    // A new search or page size starts from the first page again.
    setSearch: (value) => {
      setSearch(value)
      setPage(1)
    },
    page: current,
    setPage,
    pageSize,
    setPageSize: (value) => {
      setPageSize(value)
      setPage(1)
    },
    pages,
    total: matches.length,
    rows: matches.slice((current - 1) * pageSize, current * pageSize),
  }
}

export function SearchBox({ value, onChange, placeholder = 'Search…' }) {
  return (
    <div className="relative max-w-xs">
      <Search className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        aria-label={placeholder.replace('…', '')} className="h-8 pl-8" />
    </div>
  )
}

/** Same footer as the Pull Requests table: rows per page, "Page x of y", previous / next. */
export function TableFooter({ paging, noun = 'members' }) {
  const { page, pages, pageSize, setPage, setPageSize, total } = paging
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm">
      <div className="flex items-center gap-2 text-muted-foreground">
        Rows per page
        <Select value={String(pageSize)} onValueChange={(v) => setPageSize(Number(v))}>
          <SelectTrigger size="sm" className="w-[72px]"><SelectValue /></SelectTrigger>
          <SelectContent>
            {SIZES.map((n) => <SelectItem key={n} value={String(n)}>{n}</SelectItem>)}
          </SelectContent>
        </Select>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-muted-foreground">{total} {total === 1 ? noun.replace(/s$/, '') : noun} · Page {page} of {pages}</span>
        <Button variant="outline" size="icon" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label="Previous page">
          <ChevronLeft />
        </Button>
        <Button variant="outline" size="icon" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label="Next page">
          <ChevronRight />
        </Button>
      </div>
    </div>
  )
}
