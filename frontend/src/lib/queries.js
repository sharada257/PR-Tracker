import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'

/** Filter keys understood by /api/prs, /api/statistics, /api/my-reviews and /api/reviewers.
 *  ``scope`` is 'all' (admins: the whole organization) and ``member`` is a user id (admins: that
 *  person's data); without either you see your own. The server enforces both. */
export const FILTER_KEYS = ['date_from', 'date_to', 'repository', 'status', 'reviewer', 'author', 'scope', 'member', 'q']

export function pickFilters(filters = {}) {
  const out = {}
  for (const key of FILTER_KEYS) if (filters[key]) out[key] = filters[key]
  return out
}

export const useAuthConfig = () =>
  useQuery({ queryKey: ['auth-config'], queryFn: () => api('/auth/config'), staleTime: Infinity })

export const useMe = () =>
  useQuery({ queryKey: ['me'], queryFn: () => api('/auth/me'), staleTime: 60_000, retry: false })

export const usePullRequests = (params) =>
  useQuery({
    queryKey: ['prs', params],
    queryFn: () => api('/prs', { params }),
    placeholderData: keepPreviousData,
  })

export const usePullRequest = (id) =>
  useQuery({ queryKey: ['pr', id], queryFn: () => api(`/prs/${id}`) })

export const useStatistics = (filters) =>
  useQuery({
    queryKey: ['statistics', pickFilters(filters)],
    queryFn: () => api('/statistics', { params: pickFilters(filters) }),
    placeholderData: keepPreviousData,
  })

export const useReviewers = (filters) =>
  useQuery({
    queryKey: ['reviewers', pickFilters(filters)],
    queryFn: () => api('/reviewers', { params: pickFilters(filters) }),
    placeholderData: keepPreviousData,
  })

export const useMyReviews = (filters, params = {}) =>
  useQuery({
    queryKey: ['my-reviews', pickFilters(filters), params],
    queryFn: () => api('/my-reviews', { params: { ...pickFilters(filters), ...params } }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => (query.state.data?.importing ? 4000 : false),
  })

export const useFilterOptions = () =>
  useQuery({ queryKey: ['filter-options'], queryFn: () => api('/filters'), staleTime: 30_000 })

export const useRepositories = (params, options = {}) =>
  useQuery({
    queryKey: ['repositories', params],
    queryFn: () => api('/repositories', { params }),
    placeholderData: keepPreviousData,
    ...options,
  })

export const useImportStatus = () =>
  useQuery({
    queryKey: ['import-status'],
    queryFn: () => api('/import/status'),
    refetchInterval: (query) => (query.state.data?.active ? 2000 : 15000),
  })

export const useWebhookInfo = (options = {}) =>
  useQuery({ queryKey: ['webhook-info'], queryFn: () => api('/github/webhook-info'), ...options })

export const useMembers = () =>
  useQuery({ queryKey: ['members'], queryFn: () => api('/organization/members') })

export const useOverview = (filters) =>
  useQuery({
    queryKey: ['overview', pickFilters(filters)],
    queryFn: () => api('/organization/overview', { params: pickFilters(filters) }),
    placeholderData: keepPreviousData,
  })

export const useUpdateMember = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }) => api(`/organization/members/${id}`, { method: 'PATCH', body }),
    onSuccess: () => ['members', 'overview', 'filter-options'].forEach((key) => client.invalidateQueries({ queryKey: [key] })),
  })
}

/** Admins: people who were removed and asked to come back. Polled so the sidebar badge stays fresh. */
export const useAccessRequests = (options = {}) =>
  useQuery({ queryKey: ['access-requests'], queryFn: () => api('/organization/access-requests'), ...options })

export const useDecideRequest = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, decision }) =>
      api(`/organization/access-requests/${id}`, { method: 'PATCH', body: { decision } }),
    onSuccess: () => ['access-requests', 'members', 'overview', 'filter-options'].forEach((key) => client.invalidateQueries({ queryKey: [key] })),
  })
}

/** The removed person asks the admins to let them back in. */
export const useRequestAccess = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body) => api('/auth/access-request', { method: 'POST', body }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['me'] }),
  })
}

/* --- Slack (admin) --------------------------------------------------------------- */
export const useSlack = () => useQuery({ queryKey: ['slack'], queryFn: () => api('/slack') })

export const useUpdateSlack = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body) => api('/slack', { method: 'PATCH', body }),
    onSuccess: (data) => client.setQueryData(['slack'], data),
  })
}

export const useDisconnectSlack = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api('/slack', { method: 'DELETE' }),
    onSuccess: () => ['slack', 'slack-links'].forEach((key) => client.invalidateQueries({ queryKey: [key] })),
  })
}

export const useSlackLinks = (enabled) =>
  useQuery({ queryKey: ['slack-links'], queryFn: () => api('/slack/links'), enabled })

export const useSetSlackLink = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ userId, slack_user_id }) => api(`/slack/links/${userId}`, { method: 'PUT', body: { slack_user_id } }),
    onSuccess: () => ['slack', 'slack-links'].forEach((key) => client.invalidateQueries({ queryKey: [key] })),
  })
}

export const useSlackAutoMatch = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api('/slack/auto-match', { method: 'POST' }),
    onSuccess: () => ['slack', 'slack-links'].forEach((key) => client.invalidateQueries({ queryKey: [key] })),
  })
}

export const useSlackTest = () => useMutation({ mutationFn: () => api('/slack/test', { method: 'POST' }) })

export const useAuditLog = () => useQuery({ queryKey: ['audit'], queryFn: () => api('/auth/audit') })

function useInvalidatingMutation(fn, keys) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => keys.forEach((key) => client.invalidateQueries({ queryKey: [key] })),
  })
}

const DATA_KEYS = ['prs', 'pr', 'statistics', 'reviewers', 'my-reviews', 'filter-options', 'repositories', 'import-status', 'me']

export const useSelectRepositories = () =>
  useInvalidatingMutation((body) => api('/repositories/select', { method: 'POST', body }), DATA_KEYS)

export const useSyncNow = () =>
  useInvalidatingMutation((body) => api('/github/sync', { method: 'POST', body: body || {} }), DATA_KEYS)

export const useSwitchOrganization = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (organization_id) => api('/auth/organization', { method: 'POST', body: { organization_id } }),
    onSuccess: () => client.invalidateQueries(),
  })
}

/** True when the signed-in user administers the active organization. */
export const useIsAdmin = () => useMe().data?.organization?.role === 'ADMIN'

export const useLogout = () => {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSuccess: () => {
      client.clear()
      window.location.assign('/login')
    },
  })
}
