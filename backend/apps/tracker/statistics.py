"""Dashboard / analytics numbers. All values here are *calculated application
metrics*; see ``metrics.DEFINITIONS`` for how each one is defined."""
import statistics
from datetime import timedelta

from django.db.models import Count, F, Max, Min, Q
from django.db.models.functions import ExtractYear, TruncDay, TruncMonth, TruncWeek, TruncYear
from django.utils import timezone

from .filters import DATE_KEYS, apply_date_filters, apply_filters, first, multi, to_date
from .metrics import DEFINITIONS
from .models import PullRequest, PullRequestReview, PullRequestReviewer
from .scope import not_authored_by, org_pull_requests

HOURS = 3600.0
# "Waiting review" = open PRs in the review process (requested / being reviewed / changes requested).
WAITING_REVIEW_STATUSES = (PullRequest.STATUS_REVIEW, PullRequest.STATUS_CHANGES_REQUESTED)


def _hours(delta):
    return delta.total_seconds() / HOURS


def _summary(values):
    if not values:
        return {"average": None, "median": None, "count": 0}
    return {
        "average": round(sum(values) / len(values), 2),
        "median": round(statistics.median(values), 2),
        "count": len(values),
    }


def _mean(values):
    return round(sum(values) / len(values), 2) if values else None


def _status_counts(qs):
    counts = {key: 0 for key, _ in PullRequest.STATUS_CHOICES}
    for row in qs.values("status").annotate(n=Count("id")):
        counts[row["status"]] = row["n"]
    return counts


_TRUNC = {"day": TruncDay, "week": TruncWeek, "month": TruncMonth, "year": TruncYear}


def _granularity(start, end):
    """Pick a bucket size that keeps the chart readable for the selected range."""
    days = (end - start).days + 1
    if days <= 62:
        return "day"
    if days <= 210:
        return "week"
    if days <= 1100:
        return "month"
    return "year"


def _bucket_start(d, granularity):
    if granularity == "week":
        return d - timedelta(days=d.weekday())
    if granularity == "month":
        return d.replace(day=1)
    if granularity == "year":
        return d.replace(month=1, day=1)
    return d


def _next_bucket(d, granularity):
    if granularity == "day":
        return d + timedelta(days=1)
    if granularity == "week":
        return d + timedelta(days=7)
    if granularity == "month":
        return (d.replace(day=28) + timedelta(days=4)).replace(day=1)
    return d.replace(year=d.year + 1)


def _bucket_counts(qs, field, granularity):
    out = {}
    for row in qs.annotate(b=_TRUNC[granularity](field)).values("b").annotate(n=Count("id")):
        out[timezone.localtime(row["b"]).date()] = row["n"]
    return out


def _activity(base_qs, params):
    """Created vs merged PRs over time, bucketed to suit the selected date range."""
    created_qs = apply_filters(base_qs, params, date_field_override="created")
    merged_qs = apply_filters(base_qs, params, date_field_override="merged").filter(merged_at__isnull=False)

    start, end = to_date(first(params, "date_from")), to_date(first(params, "date_to"))
    if start is None:
        firsts = [
            timezone.localtime(v).date()
            for v in (
                created_qs.order_by("created_at_github").values_list("created_at_github", flat=True).first(),
                merged_qs.order_by("merged_at").values_list("merged_at", flat=True).first(),
            )
            if v
        ]
        if not firsts:
            return {"granularity": "day", "buckets": []}
        start = min(firsts)
    end = end or timezone.localdate()
    if end < start:
        return {"granularity": "day", "buckets": []}

    granularity = _granularity(start, end)
    created = _bucket_counts(created_qs, "created_at_github", granularity)
    merged = _bucket_counts(merged_qs, "merged_at", granularity)

    buckets = []
    cursor = _bucket_start(start, granularity)
    while cursor <= end:
        following = _next_bucket(cursor, granularity)
        buckets.append(
            {
                "start": max(cursor, start).isoformat(),
                "end": min(following - timedelta(days=1), end).isoformat(),
                "created": created.get(cursor, 0),
                "merged": merged.get(cursor, 0),
            }
        )
        cursor = following
    return {"granularity": granularity, "buckets": buckets}


def _yearly(scoped):
    created = {
        r["y"]: r["n"]
        for r in scoped.annotate(y=ExtractYear("created_at_github")).values("y").annotate(n=Count("id"))
    }
    merged = {
        r["y"]: r["n"]
        for r in scoped.filter(merged_at__isnull=False)
        .annotate(y=ExtractYear("merged_at"))
        .values("y")
        .annotate(n=Count("id"))
    }
    return [
        {"year": y, "created": created.get(y, 0), "merged": merged.get(y, 0)}
        for y in sorted(set(created) | set(merged))
    ]


def _my_review_activity(user, org, params):
    """The signed-in user's submitted reviews on other people's PRs, over the selected dates.

    Counts are review events (one per submitted review), so approvals + changes requested + comments
    add up to "reviews submitted"; "PRs reviewed" is the number of distinct PRs they touched.
    """
    empty = {
        "granularity": "day",
        "buckets": [],
        "totals": {"reviews": 0, "prs": 0, "approvals": 0, "changes_requested": 0, "commented": 0},
    }
    if user is None or org is None:
        return empty
    qs = (
        PullRequestReview.objects.filter(
            pull_request__in=not_authored_by(org_pull_requests(org), user).values("pk"),
            reviewer__iexact=user.github_username or "",
            submitted_at__isnull=False,
        )
        .exclude(state=PullRequestReview.PENDING)
    )
    repositories = [int(v) for v in multi(params, "repository") if v.isdigit()]
    if repositories:
        qs = qs.filter(pull_request__repository_id__in=repositories)
    qs = apply_date_filters(qs, params, "submitted_at")

    totals = qs.aggregate(
        reviews=Count("id"),
        prs=Count("pull_request", distinct=True),
        approvals=Count("id", filter=Q(state=PullRequestReview.APPROVED)),
        changes_requested=Count("id", filter=Q(state=PullRequestReview.CHANGES_REQUESTED)),
        commented=Count("id", filter=Q(state=PullRequestReview.COMMENTED)),
        first=Min("submitted_at"),
    )
    first_at = totals.pop("first")

    start, end = to_date(first(params, "date_from")), to_date(first(params, "date_to"))
    if start is None:
        if first_at is None:
            return {**empty, "totals": totals}
        start = timezone.localtime(first_at).date()
    end = end or timezone.localdate()
    if end < start:
        return {**empty, "totals": totals}

    granularity = _granularity(start, end)
    counts = _bucket_counts(qs, "submitted_at", granularity)
    buckets = []
    cursor = _bucket_start(start, granularity)
    while cursor <= end:
        following = _next_bucket(cursor, granularity)
        buckets.append(
            {
                "start": max(cursor, start).isoformat(),
                "end": min(following - timedelta(days=1), end).isoformat(),
                "reviews": counts.get(cursor, 0),
            }
        )
        cursor = following
    return {"granularity": granularity, "buckets": buckets, "totals": totals}


def build_statistics(base_qs, params, user=None, org=None):
    now = timezone.localtime()
    scoped = apply_filters(base_qs, params, skip=DATE_KEYS)  # every filter except the date ones
    filtered = apply_filters(base_qs, params)

    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    counts = {
        "total": scoped.count(),
        "this_year": scoped.filter(created_at_github__year=now.year).count(),
        "this_month": scoped.filter(created_at_github__year=now.year, created_at_github__month=now.month).count(),
        "this_week": scoped.filter(created_at_github__gte=week_start).count(),
        "filtered": filtered.count(),
    }

    rows = list(
        filtered.values(
            "state", "status", "draft", "created_at_github", "merged_at", "first_review_at", "review_count", "review_cycles"
        )
    )
    merged_rows = [r for r in rows if r["merged_at"]]
    open_rows = [r for r in rows if r["state"] == PullRequest.STATE_OPEN]
    waiting = [r for r in open_rows if r["status"] in WAITING_REVIEW_STATUSES]
    reviewed = [r for r in rows if r["first_review_at"]]

    # Period block: created vs merged in the selected period (all time when no dates are chosen).
    created_in = apply_filters(base_qs, params, skip=("status",), date_field_override="created")
    merged_in = apply_filters(base_qs, params, skip=("status",), date_field_override="merged").filter(
        merged_at__isnull=False
    )
    merge_hours = [
        _hours(r["merged_at"] - r["created_at_github"]) for r in merged_in.values("merged_at", "created_at_github")
    ]
    period = {
        "created": created_in.count(),
        "merged": merged_in.count(),
        "still_open": created_in.filter(state=PullRequest.STATE_OPEN).count(),
        "closed_without_merge": created_in.filter(status=PullRequest.STATUS_CLOSED).count(),
        "avg_time_to_merge_hours": _mean(merge_hours),
    }

    by_repository = [
        {
            "id": r["repository_id"],
            "full_name": r["repository__full_name"],
            "total": r["total"],
            "merged": r["merged"],
            "open": r["open"],
        }
        for r in filtered.values("repository_id", "repository__full_name")
        .annotate(
            total=Count("id"),
            merged=Count("id", filter=Q(status=PullRequest.STATUS_MERGED)),
            open=Count("id", filter=Q(state=PullRequest.STATE_OPEN)),
        )
        .order_by("-total", "repository__full_name")
    ]

    return {
        "counts": counts,
        "status": _status_counts(filtered),
        "open_prs": len(open_rows),
        "merged_prs": len(merged_rows),
        "waiting_review": len(waiting),
        # PR state: where each PR ended up.
        "pr_state": {
            "open": len(open_rows),  # shown as "Active"; the three below split it
            "in_review": len([r for r in open_rows if r["status"] in WAITING_REVIEW_STATUSES]),
            "approved": len([r for r in open_rows if r["status"] == PullRequest.STATUS_APPROVED]),
            "no_review": len([r for r in open_rows if r["status"] == PullRequest.STATUS_OPEN]),
            "merged": len(merged_rows),
            "closed": len([r for r in rows if r["status"] == PullRequest.STATUS_CLOSED]),
        },
        "my_review_activity": _my_review_activity(user, org, params),
        "review_metrics": {
            "time_to_first_review_hours": _summary(
                [_hours(r["first_review_at"] - r["created_at_github"]) for r in reviewed]
            ),
            "total_reviews": sum(r["review_count"] for r in rows),
            "prs_with_reviews": len([r for r in rows if r["review_count"] > 0]),
            "average_review_count": _mean([r["review_count"] for r in rows]),
            "average_review_cycles": _mean([r["review_cycles"] for r in rows]),
            "prs_with_multiple_cycles": len([r for r in rows if r["review_cycles"] >= 2]),
        },
        "merge_metrics": {
            "time_to_merge_hours": _summary([_hours(r["merged_at"] - r["created_at_github"]) for r in merged_rows]),
            "merged": len(merged_rows),
            "closed_without_merge": filtered.filter(status=PullRequest.STATUS_CLOSED).count(),
        },
        "period": period,
        "activity": _activity(base_qs, params),
        "by_year": _yearly(scoped),
        "by_repository": by_repository,
        "definitions": DEFINITIONS,
    }


def build_reviewers(base_qs, params):
    """Descriptive reviewer activity on the user's PRs (no quality scoring)."""
    prs = apply_filters(base_qs, params, skip=("reviewer",))
    ids = prs.values("id")

    reviews = (
        PullRequestReview.objects.filter(pull_request__in=ids)
        .exclude(state=PullRequestReview.PENDING)
        .exclude(reviewer__iexact=F("pull_request__author_login"))
        .values("reviewer")
        .annotate(
            reviews=Count("id"),
            prs=Count("pull_request", distinct=True),
            approvals=Count("id", filter=Q(state=PullRequestReview.APPROVED)),
            changes_requested=Count("id", filter=Q(state=PullRequestReview.CHANGES_REQUESTED)),
            last_activity=Max("submitted_at"),
            avatar=Max("reviewer_avatar_url"),
        )
    )
    requested = (
        PullRequestReviewer.objects.filter(pull_request__in=ids)
        .values("username", "is_team")
        .annotate(prs=Count("pull_request", distinct=True), avatar=Max("avatar_url"))
    )

    people = {}

    def entry(login):
        return people.setdefault(
            login.lower(),
            {
                "login": login,
                "avatar_url": "",
                "is_team": False,
                "prs_reviewed": 0,
                "prs_requested": 0,
                "reviews_submitted": 0,
                "approvals": 0,
                "changes_requested": 0,
                "last_activity": None,
            },
        )

    for row in reviews:
        person = entry(row["reviewer"])
        person.update(
            avatar_url=row["avatar"] or person["avatar_url"],
            prs_reviewed=row["prs"],
            reviews_submitted=row["reviews"],
            approvals=row["approvals"],
            changes_requested=row["changes_requested"],
            last_activity=row["last_activity"],
        )
    for row in requested:
        person = entry(row["username"])
        person["prs_requested"] = row["prs"]
        person["is_team"] = row["is_team"]
        person["avatar_url"] = person["avatar_url"] or row["avatar"]

    result = list(people.values())
    result.sort(key=lambda p: (-p["prs_reviewed"], -p["prs_requested"], p["login"].lower()))
    return result
