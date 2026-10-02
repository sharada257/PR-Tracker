import { useState } from 'react'
import { Navigate, useSearchParams } from 'react-router-dom'
import { useQueryClient } from '@tanstack/react-query'
import { Code2, GitPullRequest, ShieldCheck } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { api } from '@/lib/api'
import { useAuthConfig, useMe } from '@/lib/queries'

const ERRORS = {
  oauth_not_configured: 'GitHub sign-in is not configured on this server yet (set GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET).',
  access_denied: 'GitHub authorisation was cancelled.',
  invalid_state: 'The sign-in session expired. Please try again.',
  github_error: 'GitHub rejected the sign-in. Please try again.',
}

function GitHubMark(props) {
  return (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true" {...props}>
      <path d="M12 .5C5.65.5.5 5.65.5 12c0 5.08 3.29 9.38 7.86 10.9.58.1.79-.25.79-.56v-2c-3.2.7-3.87-1.37-3.87-1.37-.52-1.33-1.28-1.69-1.28-1.69-1.04-.71.08-.7.08-.7 1.15.08 1.76 1.18 1.76 1.18 1.03 1.76 2.69 1.25 3.35.96.1-.74.4-1.25.73-1.54-2.55-.29-5.24-1.28-5.24-5.68 0-1.25.45-2.28 1.18-3.08-.12-.29-.51-1.46.11-3.05 0 0 .97-.31 3.17 1.18a11 11 0 0 1 5.77 0c2.2-1.49 3.17-1.18 3.17-1.18.62 1.59.23 2.76.11 3.05.74.8 1.18 1.83 1.18 3.08 0 4.41-2.69 5.38-5.25 5.67.41.36.78 1.06.78 2.14v3.17c0 .31.21.67.8.56A11.51 11.51 0 0 0 23.5 12C23.5 5.65 18.35.5 12 .5Z" />
    </svg>
  )
}

export default function LoginPage() {
  const { data: me, isLoading } = useMe()
  const { data: config } = useAuthConfig()
  const [params] = useSearchParams()
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(ERRORS[params.get('error')] || '')

  if (!isLoading && me?.authenticated) return <Navigate to="/" replace />

  const signInWith = async (path, body) => {
    setBusy(true)
    setError('')
    try {
      await api(path, { method: 'POST', body })
      await client.invalidateQueries({ queryKey: ['me'] })
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex min-h-svh items-center justify-center bg-muted/30 p-6">
      <Card className="w-full max-w-md">
        <CardHeader className="items-center text-center">
          <div className="mb-2 flex size-12 items-center justify-center rounded-xl bg-primary text-primary-foreground">
            <GitPullRequest className="size-6" />
          </div>
          <CardTitle className="text-2xl">PR Tracker</CardTitle>
          <CardDescription>
            Engineering PR visibility &amp; analytics
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {error && (
            <Alert variant="destructive">
              <AlertTitle>Couldn&apos;t sign in</AlertTitle>
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <Button asChild size="lg" className="w-full" disabled={!config?.github_oauth}>
            <a href="/api/auth/github/login">
              <GitHubMark className="size-5" /> Continue with GitHub
            </a>
          </Button>
          {config && !config.github_oauth && (
            <p className="text-center text-xs text-muted-foreground">
              GitHub sign-in is not configured on the server yet. See the README.
            </p>
          )}
        </CardContent>
        {config?.demo_login && (
          <CardFooter className="flex-col gap-3">
            <div className="flex w-full items-center gap-3 text-xs text-muted-foreground">
              <span className="h-px flex-1 bg-border" /> Try the demo <span className="h-px flex-1 bg-border" />
            </div>
            <div className="grid w-full grid-cols-2 gap-3">
              <Button variant="secondary" className="h-auto flex-col gap-1 py-3" disabled={busy}
                onClick={() => signInWith('/auth/demo', { role: 'admin' })}>
                <span className="flex items-center gap-2 font-medium"><ShieldCheck /> Admin</span>
                <span className="text-xs font-normal text-muted-foreground">Whole organization &amp; members</span>
              </Button>
              <Button variant="secondary" className="h-auto flex-col gap-1 py-3" disabled={busy}
                onClick={() => signInWith('/auth/demo', { role: 'member' })}>
                <span className="flex items-center gap-2 font-medium"><Code2 /> Developer</span>
                <span className="text-xs font-normal text-muted-foreground">Only their own PRs &amp; reviews</span>
              </Button>
            </div>
          </CardFooter>
        )}
      </Card>
    </div>
  )
}
