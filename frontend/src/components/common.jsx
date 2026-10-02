import { Info } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { STATUS_LABEL, statusColor } from '@/lib/status'

export function PageHeader({ title, description, actions }) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="space-y-1">
        <h2 className="text-2xl font-semibold tracking-tight">{title}</h2>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}

export function StatusBadge({ status, className }) {
  return (
    <Badge variant="outline" className={cn('gap-1.5 whitespace-nowrap font-medium', className)}>
      <span className="size-2 rounded-full" style={{ backgroundColor: statusColor(status) }} />
      {STATUS_LABEL[status] ?? status}
    </Badge>
  )
}

export function LabelChip({ label }) {
  return (
    <Badge variant="secondary" className="font-normal">
      {label.name}
    </Badge>
  )
}

export function UserAvatar({ login, src, className }) {
  return (
    <Avatar className={cn('size-6', className)}>
      <AvatarImage src={src} alt={login} />
      <AvatarFallback className="text-[10px]">{(login || '?').slice(0, 2).toUpperCase()}</AvatarFallback>
    </Avatar>
  )
}

/** Small "i" with the metric definition - metrics are calculated, not GitHub fields. */
export function Definition({ text }) {
  if (!text) return null
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button type="button" className="inline-flex text-muted-foreground hover:text-foreground" aria-label="Definition">
          <Info className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">{text}</TooltipContent>
    </Tooltip>
  )
}

export function StatCard({ title, value, hint, icon: Icon, loading, definition, onClick }) {
  return (
    <Card
      className={cn('gap-2 py-4', onClick && 'cursor-pointer transition-colors hover:bg-muted/50')}
      onClick={onClick}
    >
      <CardHeader className="flex flex-row items-center justify-between space-y-0 px-4 pb-0">
        <CardTitle className="flex items-center gap-1.5 text-sm font-medium text-muted-foreground">
          {title}
          <Definition text={definition} />
        </CardTitle>
        {Icon && <Icon className="size-4 text-muted-foreground" />}
      </CardHeader>
      <CardContent className="px-4">
        {loading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <div className="text-3xl font-semibold tabular-nums">{value ?? '—'}</div>
        )}
        {hint && <p className="mt-1 text-xs text-muted-foreground">{hint}</p>}
      </CardContent>
    </Card>
  )
}

export function Section({ title, description, actions, children, className }) {
  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-start justify-between gap-2">
        <div className="space-y-1">
          <CardTitle className="text-base">{title}</CardTitle>
          {description && <CardDescription>{description}</CardDescription>}
        </div>
        {actions}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export function EmptyState({ title, description, children }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed p-10 text-center">
      <p className="font-medium">{title}</p>
      {description && <p className="max-w-md text-sm text-muted-foreground">{description}</p>}
      {children}
    </div>
  )
}

export function CalculatedBadge() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Badge variant="outline" className="cursor-help text-[10px] font-normal uppercase tracking-wide">
          Calculated
        </Badge>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        Calculated by this app from stored GitHub activity - not a field provided by GitHub.
      </TooltipContent>
    </Tooltip>
  )
}
