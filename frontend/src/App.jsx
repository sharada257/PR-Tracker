import { lazy, Suspense } from 'react'
import { Navigate, Outlet, Route, Routes, useOutletContext } from 'react-router-dom'
import { useIsAdmin, useMe } from '@/lib/queries'
import { Skeleton } from '@/components/ui/skeleton'
import AppLayout from '@/components/AppLayout'
import LoginPage from '@/pages/LoginPage'
import NoAccessPage from '@/pages/NoAccessPage'
import ChooseOrganizationPage from '@/pages/ChooseOrganizationPage'
const PullRequestsPage = lazy(() => import('@/pages/PullRequestsPage'))
const PullRequestDetailPage = lazy(() => import('@/pages/PullRequestDetailPage'))
const AnalyticsPage = lazy(() => import('@/pages/AnalyticsPage'))
const RepositoriesPage = lazy(() => import('@/pages/RepositoriesPage'))
const ReviewersPage = lazy(() => import('@/pages/ReviewersPage'))
const MyReviewsPage = lazy(() => import('@/pages/MyReviewsPage'))
const SettingsPage = lazy(() => import('@/pages/SettingsPage'))
const OrganizationPage = lazy(() => import('@/pages/OrganizationPage'))
const MembersPage = lazy(() => import('@/pages/MembersPage'))
const RequestsPage = lazy(() => import('@/pages/RequestsPage'))
const SlackPage = lazy(() => import('@/pages/SlackPage'))

function FullPageSkeleton() {
  return (
    <div className="flex min-h-svh items-center justify-center p-8">
      <div className="w-full max-w-md space-y-3">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    </div>
  )
}

/** Gate: must be signed in, and belong to an organization the GitHub App is installed in. */
function RequireAuth() {
  const { data, isLoading } = useMe()
  if (isLoading) return <FullPageSkeleton />
  if (!data?.authenticated) return <Navigate to="/login" replace />
  if (!data.has_access) return <NoAccessPage />
  if (data.needs_organization_choice) return <ChooseOrganizationPage />
  return <Outlet />
}

/** Admin-only area; members are sent home. */
function RequireAdmin() {
  const isAdmin = useIsAdmin()
  const context = useOutletContext() // keep the layout's header slot available to the pages
  return isAdmin ? <Outlet context={context} /> : <Navigate to="/" replace />
}

export default function App() {
  return (
    <Suspense fallback={<FullPageSkeleton />}>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<RequireAuth />}>
          <Route element={<AppLayout />}>
            <Route index element={<AnalyticsPage />} />
            <Route path="/pull-requests" element={<PullRequestsPage />} />
            <Route path="/pull-requests/:id" element={<PullRequestDetailPage />} />
            <Route path="/analytics" element={<Navigate to="/" replace />} />
            <Route path="/repositories" element={<RepositoriesPage />} />
            <Route path="/my-reviews" element={<MyReviewsPage />} />
            {/* Hidden for now (not in the sidebar), still reachable by URL. */}
            <Route path="/reviewers" element={<ReviewersPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route element={<RequireAdmin />}>
              <Route path="/organization" element={<OrganizationPage />} />
              <Route path="/organization/members" element={<MembersPage />} />
              <Route path="/organization/requests" element={<RequestsPage />} />
              <Route path="/organization/slack" element={<SlackPage />} />
            </Route>
          </Route>
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Suspense>
  )
}
