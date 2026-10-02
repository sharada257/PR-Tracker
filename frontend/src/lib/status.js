export const STATUSES = [
  { value: 'open', label: 'Open' },
  { value: 'review', label: 'In Review' },
  { value: 'changes_requested', label: 'Changes Requested' },
  { value: 'approved', label: 'Approved' },
  { value: 'merged', label: 'Merged' },
  { value: 'closed', label: 'Closed' },
]

export const STATUS_LABEL = Object.fromEntries(STATUSES.map((s) => [s.value, s.label]))

export const statusColor = (status) => `var(--status-${status})`

// GitHub's original review states are preserved; this is only for display.
export const REVIEW_STATE_LABEL = {
  APPROVED: 'Approved',
  CHANGES_REQUESTED: 'Changes requested',
  COMMENTED: 'Commented',
  DISMISSED: 'Dismissed',
}

// "My Reviews": the status of *your* review of someone else's PR (not the PR's own status).
export const REVIEW_STATUSES = [
  { value: 'pending', label: 'Pending' },
  { value: 'approved', label: 'Approved' },
  { value: 'changes_requested', label: 'Changes requested' },
  { value: 'commented', label: 'Commented' },
]
export const REVIEW_STATUS_LABEL = Object.fromEntries(REVIEW_STATUSES.map((s) => [s.value, s.label]))
