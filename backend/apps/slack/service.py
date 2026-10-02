"""Who to message and when: matching people, and sending review-request / reminder DMs."""
import logging
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.tracker.models import PullRequest, PullRequestEvent, PullRequestReview, PullRequestReviewer
from apps.tracker.scope import org_pull_requests

from . import crypto
from .client import SlackClient, SlackError, SlackRateLimited
from .models import SlackIntegration, SlackNotification, SlackUserLink

logger = logging.getLogger(__name__)

# A review request older than this when we first look at it is not news any more.
REQUEST_MAX_AGE = timedelta(hours=48)
# Reviewers requested in one action get timestamps this close together.
SAME_ACTION = timedelta(seconds=2)
# Slack errors that mean "this one message can never be delivered" (record it and move on).
PERMANENT = {"user_not_found", "user_disabled", "cannot_dm_bot", "users_not_found", "channel_not_found", "is_archived"}


def client_for(integration):
    return SlackClient(crypto.decrypt(integration.bot_token_encrypted))


# --------------------------------------------------------------------------
# Matching PR Tracker members to Slack people
# --------------------------------------------------------------------------
def auto_match(integration, slack_users):
    """Link members to Slack people by email, then by identical username / full name. Manual links are
    never touched. Returns how many members were newly linked."""
    org = integration.organization
    by_email = {u["email"]: u for u in slack_users if u["email"]}
    by_handle = {}
    for u in slack_users:
        for handle in (u["name"], u["display_name"]):
            if handle:
                by_handle.setdefault(handle.lower(), []).append(u)
    by_real = {}
    for u in slack_users:
        if u["real_name"]:
            by_real.setdefault(u["real_name"].lower(), []).append(u)

    linked = 0
    taken = set(SlackUserLink.objects.filter(organization=org).values_list("slack_user_id", flat=True))
    existing = {l.user_id: l for l in SlackUserLink.objects.filter(organization=org)}
    memberships = OrganizationMembership.objects.filter(
        organization=org, status=OrganizationMembership.STATUS_ACTIVE
    ).select_related("user")
    for membership in memberships:
        user = membership.user
        link = existing.get(user.id)
        if link is not None and link.source == SlackUserLink.MANUAL:
            continue
        found = by_email.get((user.email or "").lower()) if user.email else None
        if found is None:
            options = by_handle.get((user.github_username or "").lower(), [])
            if len(options) == 1:
                found = options[0]
        if found is None and user.display_name:
            options = by_real.get(user.display_name.lower(), [])
            if len(options) == 1:
                found = options[0]
        if found is None or (link is None and found["id"] in taken):
            continue
        if link is None:
            SlackUserLink.objects.create(organization=org, user=user, slack_user_id=found["id"], source=SlackUserLink.AUTO)
            taken.add(found["id"])
            linked += 1
        elif link.slack_user_id != found["id"]:
            link.slack_user_id = found["id"]
            link.save(update_fields=["slack_user_id", "updated_at"])
    return linked


# --------------------------------------------------------------------------
# Messages
#
#   <icon> Event
#
#   #142 - Pull request title
#   repository · actor
#
#   Short description.
#
#   [ View PR ]
# --------------------------------------------------------------------------
def _clean(value):
    return (value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _message(icon, event, pr, actor, description, extra="", button="View PR"):
    title = _clean(pr.title)
    repo = pr.repository.full_name
    body = "*<%s|#%s — %s>*\n%s · %s\n\n%s" % (pr.url, pr.number, title, repo, actor, description)
    if extra:
        body += "\n" + extra
    text = "%s: #%s — %s (%s)" % (event, pr.number, pr.title, repo)
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "%s *%s*" % (icon, event)}},
        {"type": "section", "text": {"type": "mrkdwn", "text": body}},
        {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": button}, "url": pr.url}]},
    ]
    return text, blocks


def review_requested_message(pr, reviewers=()):
    """To a reviewer who was just asked. ``reviewers`` lists everyone asked in the same action."""
    extra = "Reviewers: %s" % ", ".join(reviewers) if len(reviewers) > 1 else ""
    return _message("🔍", "Review requested", pr, pr.author_login, "%s requested your review." % pr.author_login, extra)


def review_rerequested_message(pr, previous_state, changed):
    """To a reviewer who already reviewed this PR and is asked again. ``changed``: new commits since that review."""
    who = pr.author_login
    if previous_state == PullRequestReview.APPROVED and changed:
        description = (
            "%s made additional changes and requested your review again.\n"
            "Your previous approval may no longer represent the latest changes." % who
        )
    elif previous_state == PullRequestReview.CHANGES_REQUESTED and changed:
        description = "%s requested your review again after making changes." % who
    else:
        description = "%s requested your review again." % who
    return _message("🔁", "Review requested again", pr, who, description, button="Review PR")


def changes_requested_message(pr, review):
    return _message("🔴", "Changes requested", pr, review.reviewer, "%s requested changes on this PR." % review.reviewer)


def approved_message(pr, review):
    return _message("✅", "PR approved", pr, review.reviewer, "%s approved this PR." % review.reviewer)


def review_outcome_message(pr, review):
    if review.state == PullRequestReview.APPROVED:
        return approved_message(pr, review)
    return changes_requested_message(pr, review)


def changes_pushed_message(pr):
    return _message(
        "🔄", "Changes pushed", pr, pr.author_login,
        "%s pushed new changes after review feedback." % pr.author_login, button="Review again",
    )


def reminder_message(pr, waiting_hours):
    days, hours = divmod(int(waiting_hours), 24)
    waited = ("%dd %dh" % (days, hours)) if days else ("%dh" % hours)
    return _message(
        "⏰", "Review reminder", pr, pr.author_login, "Your review has been waiting %s." % waited, button="Review now"
    )


def test_message(org):
    text = "PR Tracker is connected to Slack for %s." % org.name
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": ":white_check_mark: *PR Tracker* is connected. You will get review requests and reminders here."}}]
    return text, blocks


# --------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------
def _is_business_hours(now):
    local = timezone.localtime(now)
    return local.weekday() < 5 and 9 <= local.hour < 18


def _deliver(integration, client, link, pr, kind, key, text, blocks):
    """Send once. The notification row is the lock: whoever creates it sends; transient failures release it
    so the next scan tries again."""
    try:
        with transaction.atomic():
            note = SlackNotification.objects.create(
                organization=integration.organization, user=link.user, pull_request=pr, kind=kind, key=key
            )
    except IntegrityError:
        return False
    try:
        client.post(link.slack_user_id, text, blocks)
    except SlackRateLimited:
        note.delete()
        raise
    except SlackError as exc:
        if exc.is_auth_error:
            note.delete()
            raise
        if exc.code in PERMANENT:
            SlackNotification.objects.filter(pk=note.pk).update(status=SlackNotification.FAILED, error=exc.code)
            return False
        note.delete()
        logger.warning("Slack message to %s failed: %s", link.slack_user_id, exc.code)
        return False
    return True


def _dispatch_review_outcomes(integration, client, links, org, now):
    """Tell the author of a pull request when a reviewer approved it or asked for changes."""
    since = max(integration.connected_at, now - REQUEST_MAX_AGE)
    reviews = (
        PullRequestReview.objects.filter(
            pull_request__in=org_pull_requests(org),
            state__in=(PullRequestReview.APPROVED, PullRequestReview.CHANGES_REQUESTED),
            submitted_at__gte=since,
        )
        .select_related("pull_request__repository")
        .order_by("submitted_at")
    )
    sent = 0
    for review in reviews:
        pr = review.pull_request
        link = links.get((pr.author_login or "").lower())
        if link is None or review.reviewer.lower() == (pr.author_login or "").lower():
            continue
        kind = (
            SlackNotification.REVIEW_APPROVED
            if review.state == PullRequestReview.APPROVED
            else SlackNotification.REVIEW_CHANGES
        )
        text, blocks = review_outcome_message(pr, review)
        sent += _deliver(integration, client, link, pr, kind, str(review.github_review_id), text, blocks)
    return sent


def _dispatch_changes_pushed(integration, client, links, open_prs, now):
    """Tell a reviewer who asked for changes that the author has since pushed new commits (once per review)."""
    since = max(integration.connected_at, now - REQUEST_MAX_AGE)
    commits = defaultdict(list)
    for pr_id, at in PullRequestEvent.objects.filter(
        pull_request__in=open_prs, event_type=PullRequestEvent.COMMIT, occurred_at__gte=since
    ).values_list("pull_request_id", "occurred_at"):
        commits[pr_id].append(at)
    if not commits:
        return 0
    # A reviewer's latest verdict (comments do not change it in GitHub).
    latest = {}
    for review in (
        PullRequestReview.objects.filter(
            pull_request_id__in=list(commits),
            state__in=(PullRequestReview.APPROVED, PullRequestReview.CHANGES_REQUESTED, PullRequestReview.DISMISSED),
            submitted_at__isnull=False,
        )
        .select_related("pull_request__repository")
        .order_by("submitted_at", "id")
    ):
        latest[(review.pull_request_id, review.reviewer.lower())] = review
    # A reviewer who was asked again gets the "review requested again" message instead.
    asked_again = defaultdict(list)
    for pr_id, username, assigned in PullRequestReviewer.objects.filter(
        pull_request_id__in=list(commits), removed_at__isnull=True, assigned_at__isnull=False
    ).values_list("pull_request_id", "username", "assigned_at"):
        asked_again[(pr_id, username.lower())].append(assigned)

    sent = 0
    for (pr_id, reviewer), review in latest.items():
        if review.state != PullRequestReview.CHANGES_REQUESTED:
            continue
        pr = review.pull_request
        link = links.get(reviewer)
        if link is None or reviewer == (pr.author_login or "").lower():
            continue
        if any(at > review.submitted_at for at in asked_again.get((pr_id, reviewer), [])):
            continue
        if not any(at > review.submitted_at for at in commits[pr_id]):
            continue
        text, blocks = changes_pushed_message(pr)
        sent += _deliver(integration, client, link, pr, SlackNotification.CHANGES_PUSHED, str(review.github_review_id), text, blocks)
    return sent


def _dispatch_review_requests(integration, client, links, rows, now):
    """Review requests (first time or asked again) and reminders for reviews still owed."""
    if not rows:
        return 0
    pr_ids = {row.pull_request_id for row in rows}
    asked_together = defaultdict(list)
    for row in rows:
        asked_together[row.pull_request_id].append(row)
    past_reviews = defaultdict(list)
    for review in PullRequestReview.objects.filter(
        pull_request_id__in=pr_ids,
        state__in=(PullRequestReview.APPROVED, PullRequestReview.CHANGES_REQUESTED, PullRequestReview.COMMENTED),
        submitted_at__isnull=False,
    ):
        past_reviews[(review.pull_request_id, review.reviewer.lower())].append(review)
    commits = defaultdict(list)
    for pr_id, at in PullRequestEvent.objects.filter(
        pull_request_id__in=pr_ids, event_type=PullRequestEvent.COMMIT
    ).values_list("pull_request_id", "occurred_at"):
        commits[pr_id].append(at)

    sent = 0
    for row in rows:
        link = links.get(row.username.lower())
        if link is None:
            continue
        pr, assigned = row.pull_request, row.assigned_at
        stamp = assigned.isoformat()
        if (
            integration.notify_review_requests
            and assigned >= integration.connected_at
            and now - assigned <= REQUEST_MAX_AGE
        ):
            earlier = [r for r in past_reviews.get((pr.id, row.username.lower()), []) if r.submitted_at < assigned]
            if earlier:
                last = max(earlier, key=lambda r: r.submitted_at)
                changed = any(at > last.submitted_at for at in commits.get(pr.id, []))
                kind = SlackNotification.REVIEW_REREQUESTED
                text, blocks = review_rerequested_message(pr, last.state, changed)
            else:
                together = sorted(
                    {r.username for r in asked_together[pr.id] if abs(r.assigned_at - assigned) <= SAME_ACTION},
                    key=str.lower,
                )
                kind = SlackNotification.REVIEW_REQUESTED
                text, blocks = review_requested_message(pr, together)
            sent += _deliver(integration, client, link, pr, kind, stamp, text, blocks)
        if integration.send_reminders and integration.reminder_after_hours and integration.max_reminders:
            # Waiting is counted from the request, but never from before Slack was connected.
            start = max(assigned, integration.connected_at)
            waited = now - start
            due = min(int(waited / timedelta(hours=integration.reminder_after_hours)), integration.max_reminders)
            if due >= 1 and (not integration.business_hours_only or _is_business_hours(now)):
                text, blocks = reminder_message(pr, (now - assigned).total_seconds() / 3600)
                sent += _deliver(integration, client, link, pr, SlackNotification.REMINDER, "%s#%d" % (stamp, due), text, blocks)
    return sent


def dispatch(integration, now=None):
    """Send every message that is due for this organization. Returns the number sent."""
    now = now or timezone.now()
    if not integration.active or not (
        integration.notify_review_requests or integration.send_reminders or integration.notify_review_outcomes
    ):
        return 0
    org = integration.organization
    links = {
        l.user.github_username.lower(): l
        for l in SlackUserLink.objects.filter(organization=org, user__memberships__organization=org,
                                              user__memberships__status=OrganizationMembership.STATUS_ACTIVE)
        .select_related("user").distinct()
        if l.user.github_username
    }
    if not links:
        return 0
    open_prs = org_pull_requests(org).filter(state=PullRequest.STATE_OPEN, draft=False)
    rows = []
    if integration.notify_review_requests or integration.send_reminders:
        rows = list(
            PullRequestReviewer.objects.filter(
                pull_request__in=open_prs, is_team=False, removed_at__isnull=True, assigned_at__isnull=False
            )
            .select_related("pull_request__repository")
            .order_by("assigned_at")
        )
    client = client_for(integration)
    sent = 0
    try:
        if integration.notify_review_outcomes:
            sent += _dispatch_review_outcomes(integration, client, links, org, now)
            sent += _dispatch_changes_pushed(integration, client, links, open_prs, now)
        sent += _dispatch_review_requests(integration, client, links, rows, now)
    except SlackRateLimited as exc:
        logger.info("Slack rate limited for %s; resuming next run (%ss)", org, exc.retry_after)
    except SlackError as exc:
        if exc.is_auth_error:
            SlackIntegration.objects.filter(pk=integration.pk).update(active=False, last_error=exc.code)
            logger.warning("Slack token for %s was rejected (%s); notifications paused", org, exc.code)
        else:
            raise
    return sent
