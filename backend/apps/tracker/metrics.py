"""Definitions of every *calculated* value in the application.

These are application metrics, not GitHub fields. They are recomputed from the
stored reviews / events whenever a pull request is synchronised, and the
definitions below are surfaced in the UI (``DEFINITIONS``) so they stay
consistent.
"""
from .models import PullRequest, PullRequestEvent, PullRequestReview

DEFINITIONS = {
    "status": (
        "Merged if merged; Closed if closed without merging. Otherwise, for open PRs: "
        "Changes Requested if any reviewer's latest decision is 'changes requested'; "
        "Approved if at least one reviewer approved and nobody requests changes; "
        "In Review if a reviewer is requested or someone has reviewed; else Open. "
        "Draft PRs stay Open."
    ),
    "waiting_review": (
        "Open PRs that are in the review process: a review was requested, someone is reviewing, "
        "or changes were requested. Approved PRs, drafts and PRs nobody has asked to review are not included."
    ),
    "time_to_first_review": "first submitted review by someone other than the author − PR created time.",
    "time_to_merge": "merged time − PR created time (merged PRs only).",
    "review_count": (
        "Number of submitted (non-pending) reviews by people other than the author. "
        "Total reviews adds this up across your PRs in the selected dates."
    ),
    "review_cycles": (
        "Number of times a reviewer requested changes, new commits were then pushed, "
        "and a reviewer reviewed again."
    ),
    "approved_at": "Time of the first approving review.",
}

_DECISIVE = ("APPROVED", "CHANGES_REQUESTED")


def submitted_reviews(pr, reviews):
    author = (pr.author_login or "").lower()
    items = [
        r
        for r in reviews
        if r.submitted_at is not None
        and r.state != PullRequestReview.PENDING
        and (r.reviewer or "").lower() != author
    ]
    items.sort(key=lambda r: (r.submitted_at, r.github_review_id))
    return items


def derive_status(pr, reviews, has_pending_request):
    if pr.merged_at is not None:
        return PullRequest.STATUS_MERGED
    if pr.state == PullRequest.STATE_CLOSED:
        return PullRequest.STATUS_CLOSED
    if pr.draft:
        return PullRequest.STATUS_OPEN

    latest = {}
    for review in reviews:
        key = review.reviewer.lower()
        if review.state in _DECISIVE:
            latest[key] = review.state
        elif review.state == PullRequestReview.DISMISSED:
            latest.pop(key, None)
    decisions = set(latest.values())
    if PullRequestReview.CHANGES_REQUESTED in decisions:
        return PullRequest.STATUS_CHANGES_REQUESTED
    if PullRequestReview.APPROVED in decisions:
        return PullRequest.STATUS_APPROVED
    if has_pending_request or reviews:
        return PullRequest.STATUS_REVIEW
    return PullRequest.STATUS_OPEN


def count_review_cycles(reviews, commit_times):
    """changes requested -> commits pushed -> reviewed again."""
    items = [(r.submitted_at, 0, r) for r in reviews] + [(t, 1, None) for t in commit_times]
    items.sort(key=lambda item: (item[0], item[1]))
    cycles = 0
    stage = 0  # 0 idle, 1 changes requested, 2 changes pushed
    for _, kind, review in items:
        if kind == 0:
            if stage == 2:
                cycles += 1
                stage = 0
            if review.state == PullRequestReview.CHANGES_REQUESTED:
                stage = 1
        elif stage == 1:
            stage = 2
    return cycles


def recompute_pull_request(pr):
    """Refresh every derived field on ``pr`` from stored reviews, reviewers and events."""
    reviews = submitted_reviews(pr, list(pr.reviews.all()))
    has_pending = pr.reviewers.filter(removed_at__isnull=True).exists()
    commit_times = list(
        pr.events.filter(event_type=PullRequestEvent.COMMIT, occurred_at__gt=pr.created_at_github).values_list(
            "occurred_at", flat=True
        )
    )
    approvals = [r.submitted_at for r in reviews if r.state == PullRequestReview.APPROVED]

    pr.status = derive_status(pr, reviews, has_pending)
    pr.first_review_at = reviews[0].submitted_at if reviews else None
    pr.approved_at = approvals[0] if approvals else None
    pr.review_count = len(reviews)
    pr.review_cycles = count_review_cycles(reviews, commit_times)
    pr.save(update_fields=["status", "first_review_at", "approved_at", "review_count", "review_cycles", "updated_at"])
    return pr
