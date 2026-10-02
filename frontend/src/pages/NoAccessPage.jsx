import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Clock, GitPullRequest, LogOut, Plus, RefreshCw, ShieldAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useAuthConfig, useLogout, useMe, useRequestAccess } from '@/lib/queries'

/** An admin removed this person: they can ask to come back, and see where that request stands. */
function RemovedNotice({ org }) {
  const [message, setMessage] = useState('')
  const ask = useRequestAccess()
  const client = useQueryClient()
  const request = org.request
  const pending = request?.status === 'PENDING'
  // Notice an admin's decision without making the person reload.
  useEffect(() => {
    if (!pending) return undefined
    const timer = setInterval(() => client.invalidateQueries({ queryKey: ['me'] }), 10_000)
    return () => clearInterval(timer)
  }, [pending, client])

  return (
    <div className="space-y-3 rounded-lg border p-4 text-left">
      <p className="text-sm font-medium">Your access to {org.name} was removed by an admin.</p>
      {pending ? (
        <p className="flex items-start gap-2 text-sm text-muted-foreground">
          <Clock className="mt-0.5 size-4 shrink-0" />
          Your request was sent. An admin will review it, and this page updates when they do.
        </p>
      ) : (
        <>
          {request?.status === 'DENIED' && (
            <p className="text-sm text-destructive">Your last request was declined. You can ask again.</p>
          )}
          <Textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            maxLength={500}
            rows={3}
            placeholder="Optional: tell the admins why you need access"
            aria-label="Message to the admins"
          />
          <Button
            size="sm"
            disabled={ask.isPending}
            onClick={() => ask.mutate({ organization_id: org.id, message }, { onSuccess: () => setMessage('') })}
          >
            {ask.isPending ? 'Sending…' : 'Request access'}
          </Button>
          {ask.isError && <p className="text-sm text-destructive">{ask.error.message}</p>}
        </>
      )}
    </div>
  )
}

/** Signed in with GitHub, but not part of any organization the GitHub App is installed in. */
export default function NoAccessPage() {
  const { data } = useMe()
  const logout = useLogout()
  const { data: config } = useAuthConfig()
  const removed = data?.blocked_organizations ?? []
  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/30 p-6">
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <div className="mb-2 flex size-12 items-center justify-center rounded-xl bg-muted text-muted-foreground">
            <ShieldAlert className="size-6" />
          </div>
          <CardTitle className="flex items-center gap-2 text-2xl">
            <GitPullRequest className="size-5" /> PR Tracker
          </CardTitle>
          <CardDescription>
            Signed in as <strong>@{data?.user?.github_username}</strong>
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5 text-center">
          {removed.length > 0 ? (
            removed.map((org) => <RemovedNotice key={org.id} org={org} />)
          ) : (
            <p className="text-sm">
              PRLens does not currently have access to the repositories available to you. Please contact your GitHub
              organization administrator.
            </p>
          )}
          {removed.length === 0 && config?.install_url && (
            <div className="space-y-2 rounded-lg border p-3 text-left">
              <p className="text-sm font-medium">Is your organization new to PRLens?</p>
              <p className="text-xs text-muted-foreground">
                PRLens has to be installed on a GitHub organization before it can read its pull requests. GitHub decides
                who is allowed to install it; whoever completes the installation becomes the organization&apos;s admin.
              </p>
              <Button asChild size="sm">
                <a href="/api/auth/github/install">
                  <Plus /> Set up PRLens
                </a>
              </Button>
            </div>
          )}
          <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
            <Button asChild variant="outline">
              <a href="/api/auth/github/login">
                <RefreshCw /> Try again
              </a>
            </Button>
            <Button variant="ghost" onClick={() => logout.mutate()}>
              <LogOut /> Sign out
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
