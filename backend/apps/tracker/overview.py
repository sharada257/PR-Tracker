"""Organization overview (admins): one row per member with their PR and review activity.

Uses the same filters as the rest of the app (date range, repository). PR numbers are by creation date,
review numbers by the date the review was submitted. A member's own PRs never count as their reviews.
"""
from django.db.models import Count, F, Q
from django.db.models.functions import Lower

from apps.accounts.models import OrganizationMembership

from .filters import apply_date_filters, apply_filters
from .models import PullRequest, PullRequestReview, PullRequestReviewer
from .scope import org_pull_requests


def build_overview(org, params):
    org_prs = org_pull_requests(org)
    in_range = apply_filters(org_prs, params, skip=("status", "reviewer", "author", "q"))

    authored = {
        row["login"]: row
        for row in in_range.annotate(login=Lower("author_login"))
        .values("login")
        .annotate(
            created=Count("id"),
            merged=Count("id", filter=Q(status=PullRequest.STATUS_MERGED)),
            open=Count("id", filter=Q(state=PullRequest.STATE_OPEN)),
        )
    }

    reviews = PullRequestReview.objects.filter(pull_request__in=org_prs.values("pk"), submitted_at__isnull=False).exclude(
        state=PullRequestReview.PENDING
    )
    reviews = reviews.exclude(reviewer__iexact=F("pull_request__author_login"))
    repositories = [int(v) for v in params.get("repository", []) if v.isdigit()]
    if repositories:
        reviews = reviews.filter(pull_request__repository_id__in=repositories)
    reviews = apply_date_filters(reviews, params, "submitted_at")
    reviewed = {
        row["login"]: row
        for row in reviews.annotate(login=Lower("reviewer"))
        .values("login")
        .annotate(
            reviews=Count("id"),
            prs=Count("pull_request", distinct=True),
            approvals=Count("id", filter=Q(state=PullRequestReview.APPROVED)),
            changes_requested=Count("id", filter=Q(state=PullRequestReview.CHANGES_REQUESTED)),
        )
    }

    waiting_prs = org_prs.filter(state=PullRequest.STATE_OPEN, draft=False)
    pending = {
        row["login"]: row["n"]
        for row in PullRequestReviewer.objects.filter(
            pull_request__in=waiting_prs.values("pk"), removed_at__isnull=True, is_team=False
        )
        .annotate(login=Lower("username"))
        .values("login")
        .annotate(n=Count("pull_request", distinct=True))
    }

    members = []
    for membership in OrganizationMembership.objects.filter(
        organization=org, status=OrganizationMembership.STATUS_ACTIVE
    ).select_related("user"):
        user = membership.user
        key = (user.github_username or "").lower()
        mine, theirs = authored.get(key, {}), reviewed.get(key, {})
        members.append(
            {
                "id": user.id,
                "github_username": user.github_username,
                "display_name": user.display_name or user.github_username,
                "avatar_url": user.avatar_url,
                "role": membership.role,
                "prs_created": mine.get("created", 0),
                "prs_merged": mine.get("merged", 0),
                "prs_open": mine.get("open", 0),
                "reviews_submitted": theirs.get("reviews", 0),
                "prs_reviewed": theirs.get("prs", 0),
                "approvals": theirs.get("approvals", 0),
                "changes_requested": theirs.get("changes_requested", 0),
                "pending_reviews": pending.get(key, 0),
            }
        )
    members.sort(key=lambda m: (-m["prs_created"] - m["reviews_submitted"], m["github_username"].lower()))
    return {"members": members}
