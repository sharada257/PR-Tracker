import { ChevronRight, GitPullRequest, LogOut, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { UserAvatar } from '@/components/common'
import { useAuthConfig, useLogout, useMe, useSwitchOrganization } from '@/lib/queries'

/** Shown after sign-in when the user belongs to several organizations that have PRLens set up. */
export default function ChooseOrganizationPage() {
  const { data } = useMe()
  const { data: config } = useAuthConfig()
  const switchOrg = useSwitchOrganization()
  const logout = useLogout()
  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/30 p-6">
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <div className="mb-2 flex size-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <GitPullRequest className="size-6" />
          </div>
          <CardTitle className="text-2xl">Welcome back, {data?.user?.display_name}</CardTitle>
          <CardDescription>Choose an organization</CardDescription>
        </CardHeader>
        <CardContent className="space-y-2">
          {(data?.organizations ?? []).map((org) => (
            <Button
              key={org.id}
              variant="outline"
              className="h-auto w-full justify-start gap-3 py-3"
              disabled={switchOrg.isPending}
              onClick={() => switchOrg.mutate(org.id)}
            >
              <UserAvatar login={org.login} src={org.avatar_url} className="size-8" />
              <span className="flex-1 text-left">
                <span className="block font-medium">{org.name}</span>
                <span className="block text-xs font-normal text-muted-foreground">
                  {org.role === 'ADMIN' ? 'Admin' : 'Member'}
                </span>
              </span>
              <ChevronRight className="text-muted-foreground" />
            </Button>
          ))}
          <div className="flex justify-between pt-3">
            {config?.install_url ? (
              <Button asChild variant="ghost" size="sm">
                <a href="/api/auth/github/install">
                  <Plus /> Set up PRLens for another organization
                </a>
              </Button>
            ) : (
              <span />
            )}
            <Button variant="ghost" size="sm" onClick={() => logout.mutate()}>
              <LogOut /> Sign out
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
