import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useTheme } from 'next-themes'
import {
  BarChart3,
  Building2,
  MessageSquare,
  UserPlus,
  Check,
  ClipboardCheck,
  ChevronsUpDown,
  FolderGit2,
  GitPullRequest,
  LogOut,
  Monitor,
  Moon,
  Settings,
  Sun,
  Users,
} from 'lucide-react'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarInset,
  SidebarMenu,
  SidebarMenuBadge,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarProvider,
  SidebarRail,
  SidebarTrigger,
} from '@/components/ui/sidebar'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import SyncButton from '@/components/SyncButton'
import { useAccessRequests, useIsAdmin, useLogout, useMe, useSwitchOrganization } from '@/lib/queries'

const ADMIN_NAV = [
  { to: '/organization', label: 'Organization', icon: Building2, end: true },
  { to: '/organization/members', label: 'Members', icon: Users },
  { to: '/organization/requests', label: 'Requests', icon: UserPlus, badge: true },
  { to: '/organization/slack', label: 'Slack', icon: MessageSquare },
]

const NAV = [
  { to: '/', label: 'Analytics', icon: BarChart3, end: true },
  { to: '/pull-requests', label: 'Pull Requests', icon: GitPullRequest },
  { to: '/repositories', label: 'Repositories', icon: FolderGit2 },
  { to: '/my-reviews', label: 'My Reviews', icon: ClipboardCheck },
]

function titleFor(pathname) {
  if (pathname === '/') return 'Analytics'
  if (pathname.startsWith('/pull-requests/')) return 'Pull request'
  const item = [...NAV, ...[...ADMIN_NAV].reverse(), { to: '/settings', label: 'Settings' }, { to: '/reviewers', label: 'Reviewers' }].find((n) => n.to !== '/' && pathname.startsWith(n.to))
  return item?.label ?? ''
}

// Small text under the title in the top bar, for pages that don't repeat a heading in their body.
const DESCRIPTIONS = {
  admin: {
    '/settings': 'Choose repositories, monitor syncs, and check the GitHub connection.',
    '/organization': 'Everyone\'s pull requests and reviews across the organization.',
    '/organization/members': 'Who has access, and what they can do.',
    '/organization/requests': 'People who were removed and are asking to come back.',
    '/organization/slack': 'Send review requests and reminders to people on Slack.',
  },
  member: { '/settings': 'Sync status for your organization.' },
}

function ThemeMenu() {
  const { setTheme } = useTheme()
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Change theme">
          <Sun className="size-4 dark:hidden" />
          <Moon className="hidden size-4 dark:block" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="end">
        <DropdownMenuItem onClick={() => setTheme('light')}>
          <Sun /> Light
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme('dark')}>
          <Moon /> Dark
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => setTheme('system')}>
          <Monitor /> System
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

const ROLE_LABEL = { ADMIN: 'Admin', MEMBER: 'Member' }

/** Brand + active organization. With several organizations it doubles as a switcher. */
function OrgSwitcher() {
  const { data } = useMe()
  const switchOrg = useSwitchOrganization()
  const org = data?.organization
  const orgs = data?.organizations ?? []
  const brand = (
    <>
      <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <GitPullRequest className="size-4" />
      </div>
      <div className="grid flex-1 text-left text-sm leading-tight">
        <span className="truncate font-semibold">PR Tracker</span>
        <span className="truncate text-xs text-muted-foreground">
          {org ? `${org.name} · ${ROLE_LABEL[org.role] ?? org.role}` : 'GitHub analytics'}
        </span>
      </div>
    </>
  )
  if (orgs.length < 2) {
    return (
      <SidebarMenuButton size="lg" asChild>
        <NavLink to="/">{brand}</NavLink>
      </SidebarMenuButton>
    )
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
          {brand}
          <ChevronsUpDown className="ml-auto size-4" />
        </SidebarMenuButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="bottom" align="start" className="w-56">
        <DropdownMenuLabel>Organizations</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {orgs.map((o) => (
          <DropdownMenuItem key={o.id} onClick={() => o.id !== org?.id && switchOrg.mutate(o.id)}>
            <span className="flex-1 truncate">{o.name}</span>
            {o.id === org?.id && <Check className="size-4" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function UserMenu() {
  const { data } = useMe()
  const logout = useLogout()
  const user = data?.user
  if (!user) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <SidebarMenuButton size="lg" className="data-[state=open]:bg-sidebar-accent">
          <Avatar className="size-8">
            <AvatarImage src={user.avatar_url} alt={user.github_username} />
            <AvatarFallback>{user.github_username.slice(0, 2).toUpperCase()}</AvatarFallback>
          </Avatar>
          <div className="grid flex-1 text-left text-sm leading-tight">
            <span className="truncate font-medium">{user.display_name}</span>
            <span className="truncate text-xs text-muted-foreground">@{user.github_username}</span>
          </div>
          <ChevronsUpDown className="ml-auto size-4" />
        </SidebarMenuButton>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-56">
        <DropdownMenuLabel>Signed in with GitHub</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => logout.mutate()}>
          <LogOut /> Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export default function AppLayout() {
  const { pathname } = useLocation()
  const isAdmin = useIsAdmin()
  const pendingRequests = useAccessRequests({ enabled: isAdmin, refetchInterval: 30_000 }).data?.pending_count ?? 0
  const description = DESCRIPTIONS[isAdmin ? 'admin' : 'member'][pathname]
  // Pages can portal controls (filters, actions) into the top bar via this element.
  const [headerSlot, setHeaderSlot] = useState(null)
  return (
    <SidebarProvider>
      <Sidebar collapsible="icon">
        <SidebarHeader>
          <SidebarMenu>
            <SidebarMenuItem>
              <OrgSwitcher />
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarHeader>
        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Overview</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV.map((item) => (
                  <SidebarMenuItem key={item.to}>
                    <SidebarMenuButton
                      asChild
                      tooltip={item.label}
                      isActive={item.end ? pathname === '/' : pathname.startsWith(item.to)}
                    >
                      <NavLink to={item.to} end={item.end}>
                        <item.icon />
                        <span>{item.label}</span>
                      </NavLink>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
          {isAdmin && (
            <SidebarGroup>
              <SidebarGroupLabel>Admin</SidebarGroupLabel>
              <SidebarGroupContent>
                <SidebarMenu>
                  {ADMIN_NAV.map((item) => (
                    <SidebarMenuItem key={item.to}>
                      <SidebarMenuButton
                        asChild
                        tooltip={item.label}
                        isActive={item.end ? pathname === item.to : pathname.startsWith(item.to)}
                      >
                        <NavLink to={item.to} end={item.end}>
                          <item.icon />
                          <span>{item.label}</span>
                        </NavLink>
                      </SidebarMenuButton>
                      {item.badge && pendingRequests > 0 && <SidebarMenuBadge>{pendingRequests}</SidebarMenuBadge>}
                    </SidebarMenuItem>
                  ))}
                </SidebarMenu>
              </SidebarGroupContent>
            </SidebarGroup>
          )}
          <SidebarGroup>
            <SidebarGroupLabel>Manage</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                <SidebarMenuItem>
                  <SidebarMenuButton asChild tooltip="Settings" isActive={pathname.startsWith('/settings')}>
                    <NavLink to="/settings">
                      <Settings />
                      <span>Settings</span>
                    </NavLink>
                  </SidebarMenuButton>
                </SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>
        <SidebarFooter>
          <SidebarMenu>
            <SidebarMenuItem className="flex items-center gap-1 group-data-[collapsible=icon]:flex-col">
              <div className="min-w-0 flex-1">
                <UserMenu />
              </div>
              <ThemeMenu />
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
      <SidebarInset>
        <header className="sticky top-0 z-10 flex min-h-14 shrink-0 flex-wrap items-center gap-x-2 gap-y-2 border-b bg-background/80 px-4 py-2 backdrop-blur">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 h-4" />
          <div className="min-w-0">
            <h1 className="text-lg font-semibold leading-tight">{titleFor(pathname)}</h1>
            {description && <p className="truncate text-xs text-muted-foreground">{description}</p>}
          </div>
          <div ref={setHeaderSlot} className="ml-2 flex min-w-0 flex-1 flex-wrap items-center gap-2" />
          <SyncButton />
        </header>
        <main className="flex-1 p-4 md:p-6">
          <div className="mx-auto w-full max-w-7xl space-y-6">
            <Outlet context={{ headerSlot }} />
          </div>
        </main>
      </SidebarInset>
    </SidebarProvider>
  )
}
