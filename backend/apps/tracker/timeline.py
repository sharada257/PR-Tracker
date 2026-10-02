"""Builds the ordered lifecycle timeline of a PR from the stored tables."""
from datetime import timedelta

from .models import PullRequestEvent, PullRequestReview

COMMIT_GROUP_GAP = timedelta(minutes=15)

REVIEW_TITLES = {
    PullRequestReview.APPROVED: ("review_approved", "Review approved"),
    PullRequestReview.CHANGES_REQUESTED: ("changes_requested", "Changes requested"),
    PullRequestReview.COMMENTED: ("review_commented", "Review submitted (comment)"),
    PullRequestReview.DISMISSED: ("review_dismissed", "Review dismissed"),
}

# Tie-breaker for entries sharing a timestamp.
ORDER = {
    "created": 0,
    "ready_for_review": 1,
    "converted_to_draft": 1,
    "reviewer_assigned": 2,
    "commits_pushed": 3,
    "reopened": 3,
    "review": 4,
    "reviewer_removed": 5,
    "closed": 6,
    "merged": 7,
}


def _item(at, kind, title, actor="", detail="", **extra):
    base_kind = kind if kind in ORDER else "review"
    return {
        "at": at,
        "kind": kind,
        "title": title,
        "actor": actor,
        "detail": detail,
        "_order": ORDER.get(base_kind, 4),
        **extra,
    }


def build_timeline(pr):
    items = [_item(pr.created_at_github, "created", "PR created", actor=pr.author_login)]

    reviews = list(pr.reviews.all())
    for row in pr.reviewers.all():
        who = row.username
        if row.assigned_at:
            items.append(_item(row.assigned_at, "reviewer_assigned", "Reviewer assigned: %s" % who, actor=who))
        reviewed_after = any(
            r.reviewer.lower() == who.lower() and r.submitted_at and (not row.assigned_at or r.submitted_at >= row.assigned_at)
            for r in reviews
        )
        # A request that disappears because the person reviewed is not a "removal".
        if row.removed_at and not reviewed_after:
            items.append(_item(row.removed_at, "reviewer_removed", "Reviewer removed: %s" % who, actor=who))

    for review in reviews:
        if not review.submitted_at or review.state == PullRequestReview.PENDING:
            continue
        kind, title = REVIEW_TITLES.get(review.state, ("review_commented", "Review submitted"))
        items.append(
            _item(
                review.submitted_at, kind, title, actor=review.reviewer, detail=(review.body or "")[:400],
                state=review.state,
            )
        )

    commits = []
    for event in pr.events.all():
        if event.event_type == PullRequestEvent.COMMIT:
            if event.occurred_at > pr.created_at_github:
                commits.append(event)
        else:
            titles = {
                PullRequestEvent.REOPENED: "PR reopened",
                PullRequestEvent.READY_FOR_REVIEW: "Marked ready for review",
                PullRequestEvent.CONVERTED_TO_DRAFT: "Converted to draft",
            }
            items.append(_item(event.occurred_at, event.event_type, titles.get(event.event_type, event.event_type), actor=event.actor))

    group = []
    for event in sorted(commits, key=lambda e: e.occurred_at):
        if group and event.occurred_at - group[-1].occurred_at > COMMIT_GROUP_GAP:
            items.append(_commit_item(group))
            group = []
        group.append(event)
    if group:
        items.append(_commit_item(group))

    if pr.merged_at:
        items.append(_item(pr.merged_at, "merged", "PR merged", detail=pr.merge_commit_sha[:7]))
    elif pr.closed_at and pr.state == "closed":
        items.append(_item(pr.closed_at, "closed", "PR closed without merging"))

    items.sort(key=lambda i: (i["at"], i["_order"]))
    for item in items:
        item.pop("_order")
    return items


def _commit_item(group):
    count = len(group)
    messages = [e.data.get("message", "") for e in group if e.data.get("message")]
    return _item(
        group[0].occurred_at,
        "commits_pushed",
        "New commit pushed" if count == 1 else "%d new commits pushed" % count,
        actor=group[-1].actor,
        detail="\n".join(messages[:5]),
        count=count,
    )
