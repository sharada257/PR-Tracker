import { Link, useNavigate } from 'react-router-dom'
import { ArrowDown, ArrowUp, ArrowUpDown, ExternalLink } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { StatusBadge, UserAvatar } from '@/components/common'
import { formatDate, formatDateTime } from '@/lib/format'

export function Reviewers({ reviewers, max = 2 }) {
  if (!reviewers?.length) return <span className="text-muted-foreground">—</span>
  const shown = reviewers.slice(0, max)
  const extra = reviewers.length - shown.length
  return (
    <div className="flex items-center gap-2">
      {shown.map((r) => (
        <span key={r.username} className="flex items-center gap-1.5">
          <UserAvatar login={r.username} src={r.avatar_url} className="size-5" />
          <span className="max-w-[110px] truncate">{r.username}</span>
        </span>
      ))}
      {extra > 0 && (
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="cursor-default text-xs text-muted-foreground">+{extra}</span>
          </TooltipTrigger>
          <TooltipContent>{reviewers.slice(max).map((r) => r.username).join(', ')}</TooltipContent>
        </Tooltip>
      )}
    </div>
  )
}

function SortHead({ field, ordering, onSort, children, className }) {
  if (!onSort) return <TableHead className={className}>{children}</TableHead>
  const active = ordering?.replace('-', '') === field
  const desc = ordering?.startsWith('-')
  const Icon = !active ? ArrowUpDown : desc ? ArrowDown : ArrowUp
  return (
    <TableHead className={className}>
      <button
        type="button"
        className="-ml-2 inline-flex items-center gap-1 rounded px-2 py-1 hover:bg-muted"
        onClick={() => onSort(active && !desc ? `-${field}` : active && desc ? field : `-${field}`)}
      >
        {children}
        <Icon className={`size-3.5 ${active ? '' : 'opacity-40'}`} />
      </button>
    </TableHead>
  )
}

/**
 * ``compact`` is the Analytics-page variant (fewer columns); the full table matches the
 * spec: PR, number, repository, reviewer, status, created, merged and a
 * GitHub link (no Updated or Labels columns). ``showAuthor`` adds Author, used when browsing everyone's PRs.
 */
export default function PRTable({ rows, loading, ordering, onSort, compact = false, pageSize = 8, showAuthor = false }) {
  const navigate = useNavigate()
  const authorColumn = showAuthor && !compact
  const columns = (compact ? 6 : 8) + (authorColumn ? 1 : 0)

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <SortHead field="title" ordering={ordering} onSort={onSort}>PR</SortHead>
          {!compact && <SortHead field="number" ordering={ordering} onSort={onSort}>#</SortHead>}
          <SortHead field="repository" ordering={ordering} onSort={onSort}>Repository</SortHead>
          {authorColumn && <TableHead>Author</TableHead>}
          <TableHead>Reviewer</TableHead>
          <SortHead field="status" ordering={ordering} onSort={onSort}>Status</SortHead>
          <SortHead field="created_at" ordering={ordering} onSort={onSort}>Created</SortHead>
          {!compact && <SortHead field="merged_at" ordering={ordering} onSort={onSort}>Merged</SortHead>}
          <TableHead className="w-10 text-right">
            <span className="sr-only">GitHub</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {loading &&
          Array.from({ length: pageSize }).map((_, i) => (
            <TableRow key={i}>
              {Array.from({ length: columns }).map((__, j) => (
                <TableCell key={j}>
                  <Skeleton className="h-5 w-full min-w-10" />
                </TableCell>
              ))}
            </TableRow>
          ))}
        {!loading &&
          rows.map((pr) => (
            <TableRow
              key={pr.id}
              className="cursor-pointer"
              onClick={() => navigate(`/pull-requests/${pr.id}`)}
            >
              <TableCell className="max-w-[320px] font-medium">
                <Link
                  to={`/pull-requests/${pr.id}`}
                  className="line-clamp-2 hover:underline"
                  onClick={(e) => e.stopPropagation()}
                >
                  {pr.title}
                </Link>
                {pr.draft && <span className="ml-1 text-xs text-muted-foreground">(draft)</span>}
              </TableCell>
              {!compact && <TableCell className="tabular-nums text-muted-foreground">#{pr.number}</TableCell>}
              <TableCell className="whitespace-nowrap text-muted-foreground">
                {compact ? pr.repository.name : pr.repository.full_name}
              </TableCell>
              {authorColumn && (
                <TableCell>
                  <span className="flex items-center gap-1.5">
                    <UserAvatar login={pr.author.login} src={pr.author.avatar_url} className="size-5" />
                    <span className="max-w-[110px] truncate">{pr.author.login}</span>
                  </span>
                </TableCell>
              )}
              <TableCell>
                <Reviewers reviewers={pr.reviewers} max={compact ? 1 : 2} />
              </TableCell>
              <TableCell>
                <StatusBadge status={pr.status} />
              </TableCell>
              <TableCell className="whitespace-nowrap text-muted-foreground">
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span>{formatDate(pr.created_at)}</span>
                  </TooltipTrigger>
                  <TooltipContent>{formatDateTime(pr.created_at)}</TooltipContent>
                </Tooltip>
              </TableCell>
              {!compact && (
                <TableCell className="whitespace-nowrap text-muted-foreground">{formatDate(pr.merged_at)}</TableCell>
              )}
              <TableCell className="text-right">
                <Button asChild variant="ghost" size="icon" onClick={(e) => e.stopPropagation()}>
                  <a href={pr.url} target="_blank" rel="noreferrer" aria-label={`Open #${pr.number} on GitHub`}>
                    <ExternalLink className="size-4" />
                  </a>
                </Button>
              </TableCell>
            </TableRow>
          ))}
        {!loading && rows.length === 0 && (
          <TableRow>
            <TableCell colSpan={columns} className="h-24 text-center text-muted-foreground">
              No pull requests match these filters.
            </TableCell>
          </TableRow>
        )}
      </TableBody>
    </Table>
  )
}
