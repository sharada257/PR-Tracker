"""My Reviews: other people's PRs the signed-in user was asked to review or has reviewed.

Everything here is derived from the organization's PRs written by *other* people; the user's own PRs
never appear. As with the other statistics, the numbers are calculated application metrics.

Per PR, the user's *review status* is their latest decision, like GitHub shows it:
Approved / Changes requested, otherwise Commented. A PR is *pending* while the user's review is
requested and they have not reviewed it since (GitHub clears the request when they submit a review).
"""
from django.utils import timezone

from .filters import multi, to_date, to_int
from .models import PullRequest, PullRequestReview, PullRequestReviewer, Repository
from .scope import not_authored_by, org_pull_requests

PENDING = "pending"
APPROVED = "approved"
CHANGES_REQUESTED = "changes_requested"
COMMENTED = "commented"
STATUSES = (PENDING, APPROVED, CHANGES_REQUESTED, COMMENTED)

PENDING_PREVIEW = 5


def user_review_pull_requests(org, user):
    """PRs by other people in the organization's visible repositories (a superset of what the user reviewed)."""
    return not_authored_by(org_pull_requests(org), user)


def _my_review_status(reviews):
    """Latest decision from the user's reviews on one PR (ordered oldest -> newest)."""
    decision = None
    for review in reviews:
        if review["state"] in (PullRequestReview.APPROVED, PullRequestReview.CHANGES_REQUESTED):
            decision = review["state"]
        elif review["state"] == PullRequestReview.DISMISSED:
            decision = None
    if decision == PullRequestReview.APPROVED:
        return APPROVED
    if decision == PullRequestReview.CHANGES_REQUESTED:
        return CHANGES_REQUESTED
    return COMMENTED


def _hours(delta):
    return round(max(delta.total_seconds(), 0) / 3600.0, 2)


def _row(pr, status, reviewed_at=None, requested_at=None, now=None):
    waiting = None
    if status == PENDING:
        since = requested_at or pr.created_at_github
        waiting = _hours(now - since)
    return {
        "id": pr.id,
        "number": pr.number,
        "title": pr.title,
        "url": pr.url,
        "repository": {"id": pr.repository_id, "full_name": pr.repository.full_name},
        "author_login": pr.author_login,
        "author_avatar_url": pr.author_avatar_url,
        "pr_status": pr.status,
        "status": status,
        "reviewed_at": reviewed_at,
        "requested_at": requested_at,
        "waiting_hours": waiting,
    }


def _matches(row, terms):
    """Every search term must appear in the title, repository, author or PR number."""
    haystack = " ".join(
        [row["title"], row["repository"]["full_name"], row["author_login"] or "", f"#{row['number']}", str(row["number"])]
    ).lower()
    return all(term in haystack for term in terms)


def build_my_reviews(user, org, params):
    now = timezone.now()
    me = (user.github_username or "").lower()
    prs = user_review_pull_requests(org, user)

    repositories = multi(params, "repository")
    if repositories:
        ids = [int(v) for v in repositories if v.isdigit()]
        prs = prs.filter(repository_id__in=ids)
    prs = prs.select_related("repository")
    by_id = {pr.id: pr for pr in prs}

    # --- pending: the user's review is requested and still outstanding ------------------------------
    pending = {}
    for row in (
        PullRequestReviewer.objects.filter(
            pull_request_id__in=[pk for pk, pr in by_id.items() if pr.state == PullRequest.STATE_OPEN and not pr.draft],
            username__iexact=me,
            is_team=False,
            removed_at__isnull=True,
        )
        .order_by("assigned_at")
        .values("pull_request_id", "assigned_at")
    ):
        pending[row["pull_request_id"]] = row["assigned_at"]

    # --- reviewed: the user's own submitted reviews --------------------------------------------------
    mine = {}
    for review in (
        PullRequestReview.objects.filter(pull_request_id__in=list(by_id), reviewer__iexact=me, submitted_at__isnull=False)
        .exclude(state=PullRequestReview.PENDING)
        .order_by("submitted_at", "id")
        .values("pull_request_id", "state", "submitted_at")
    ):
        mine.setdefault(review["pull_request_id"], []).append(review)

    date_from, date_to = to_date(params_first(params, "date_from")), to_date(params_first(params, "date_to"))
    reviewed = []
    for pr_id, reviews in mine.items():
        status = _my_review_status(reviews)
        reviewed_at = reviews[-1]["submitted_at"]
        day = timezone.localtime(reviewed_at).date()
        if (date_from and day < date_from) or (date_to and day > date_to):
            continue
        reviewed.append((by_id[pr_id], status, reviewed_at))

    activity = {
        "reviewed": len(reviewed),
        "approvals": sum(1 for _, s, _ in reviewed if s == APPROVED),
        "changes_requested": sum(1 for _, s, _ in reviewed if s == CHANGES_REQUESTED),
        "commented": sum(1 for _, s, _ in reviewed if s == COMMENTED),
        "pending": len(pending),
    }

    pending_rows = [_row(by_id[pr_id], PENDING, requested_at=at, now=now) for pr_id, at in pending.items()]
    pending_rows.sort(key=lambda r: -(r["waiting_hours"] or 0))  # longest waiting first

    # --- the table ---------------------------------------------------------------------------------------
    wanted = [s for s in multi(params, "status") if s in STATUSES]
    rows = []
    if PENDING in wanted:
        rows.extend(pending_rows)  # pending reviews are "now": the date range does not apply
    reviewed_rows = [
        _row(pr, status, reviewed_at=at, now=now)
        for pr, status, at in sorted(reviewed, key=lambda item: item[2], reverse=True)
        if not wanted or status in wanted
    ]
    rows.extend(reviewed_rows)

    # Author options come from everything the user could see in the table; the filter then narrows it.
    author_options = sorted({r["author_login"] for r in rows + pending_rows if r["author_login"]}, key=str.lower)
    authors = {a.lower() for a in multi(params, "author")}
    if authors:
        rows = [row for row in rows if (row["author_login"] or "").lower() in authors]

    # Free-text search narrows the table only (the pending card and the activity numbers stay as they are).
    terms = (params_first(params, "q") or "").lower().split()
    if terms:
        rows = [row for row in rows if _matches(row, terms)]

    page_size = min(to_int(params_first(params, "page_size"), 1, 200) or 25, 200)
    pages = max((len(rows) + page_size - 1) // page_size, 1)
    page = min(to_int(params_first(params, "page"), 1) or 1, pages)
    start = (page - 1) * page_size

    importing = Repository.objects.filter(
        import_status__in=[Repository.IMPORT_QUEUED, Repository.IMPORT_RUNNING],
        organization=org,
        is_active=True,
        is_accessible=True,
    ).exists()

    return {
        "activity": activity,
        "pending": {"count": len(pending_rows), "items": pending_rows[:PENDING_PREVIEW]},
        "count": len(rows),
        "page": page,
        "pages": pages,
        "page_size": page_size,
        "results": rows[start:start + page_size],
        "importing": importing,
        "authors": author_options,
    }


def params_first(params, key, default=None):  # noqa: D103
    values = params.get(key) or []
    return values[0] if values else default
