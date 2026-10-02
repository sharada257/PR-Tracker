"""Synchronisation engine: turns normalised GitHub data into local rows.

Every function here is idempotent - applying the same data twice leaves the
database unchanged - which is what makes webhook retries and periodic
reconciliation safe.
"""
import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.tracker.metrics import recompute_pull_request
from apps.tracker.models import (
    PullRequest,
    PullRequestEvent,
    PullRequestLabel,
    PullRequestReview,
    PullRequestReviewer,
    Repository,
)

from apps.accounts.models import Organization

from . import auth, normalize
from .client import GitHubNotFound
from .models import GitHubInstallation

logger = logging.getLogger(__name__)

CORE_FIELDS = [
    "number",
    "title",
    "description",
    "author_login",
    "author_github_id",
    "author_avatar_url",
    "state",
    "draft",
    "url",
    "base_branch",
    "head_branch",
    "merge_commit_sha",
    "created_at_github",
    "updated_at_github",
    "closed_at",
    "merged_at",
    "additions",
    "deletions",
    "changed_files",
    "commits_count",
    "comments_count",
]


# --------------------------------------------------------------------------
# Repositories
# --------------------------------------------------------------------------
@dataclass
class Stats:
    """What a sync did, accumulated across batches and stored on the SyncJob."""

    processed: int = 0
    created: int = 0
    updated: int = 0
    reviews: int = 0

    def record(self, result):
        """``result`` is what ``sync_pull_request`` returns: ``None`` or ``(pull_request, created)``."""
        if result is None:
            return
        pr, created = result
        self.processed += 1
        if created:
            self.created += 1
        else:
            self.updated += 1
        self.reviews += getattr(pr, "_review_total", 0)


def upsert_organization(account, installation_id, selection="", suspended_at=None, installed_by=None):
    """Create/refresh the Organization + GitHubInstallation for a GitHub App installation.

    ``account`` is the installation's ``account`` object from the GitHub API / webhook."""
    organization, _ = Organization.objects.update_or_create(
        github_organization_id=account["id"],
        defaults={
            "name": account.get("name") or account["login"],
            "github_organization_login": account["login"],
            "account_type": account.get("type") or "",
            "avatar_url": account.get("avatar_url") or "",
        },
    )
    installation, _ = GitHubInstallation.objects.update_or_create(
        github_installation_id=installation_id,
        defaults={
            "organization": organization,
            "github_account_id": account["id"],
            "github_account_login": account["login"],
            "account_type": account.get("type") or "",
            "repository_selection": selection or "",
            "active": True,
            "suspended_at": suspended_at,
        },
    )
    if installed_by and not installation.installed_by_github_id:
        installation.installed_by_github_id = installed_by
        installation.save(update_fields=["installed_by_github_id", "updated_at"])
    return organization, installation


def upsert_repository(organization, payload):
    """Returns ``(repository, created)``. Rediscovered repositories become accessible again but keep
    an admin's decision to stop tracking them."""
    data = normalize.normalize_repository(payload)
    repo, created = Repository.objects.get_or_create(
        organization=organization, github_repository_id=data["github_repository_id"], defaults=data
    )
    if not created:
        for field, value in data.items():
            setattr(repo, field, value)
        repo.is_accessible = True
        repo.save()
    return repo, created


def refresh_repositories(organization, client=None):
    """Discover the repositories the installation can access. Returns ``(created, updated)``
    lists of repositories. Repositories the installation no longer returns are hidden
    (``is_accessible=False``) rather than deleted, so history survives a temporary revoke."""
    client = client or auth.client_for_organization(organization)
    created, updated, seen = [], [], set()
    for payload in client.list_installation_repositories():
        repo, was_created = upsert_repository(organization, payload)
        seen.add(repo.github_repository_id)
        (created if was_created else updated).append(repo)
    Repository.objects.filter(organization=organization).exclude(github_repository_id__in=seen).update(
        is_accessible=False
    )
    return created, updated


# --------------------------------------------------------------------------
# Pull request building blocks
# --------------------------------------------------------------------------
def upsert_pull_request(repo, data, force=False):
    """Returns ``(pull_request, created, applied)``. Stale payloads are not applied
    unless ``force`` - webhook deliveries may arrive out of order."""
    existing = PullRequest.objects.filter(repository=repo, github_pr_id=data["github_pr_id"]).first()
    if existing is None:
        values = {field: data[field] for field in CORE_FIELDS}
        pr = PullRequest.objects.create(repository=repo, github_pr_id=data["github_pr_id"], **values)
        return pr, True, True
    if not force and data["updated_at_github"] < existing.updated_at_github:
        return existing, False, False
    for field in CORE_FIELDS:
        setattr(existing, field, data[field])
    existing.save()
    return existing, False, True


def sync_labels(pr, labels):
    wanted = {label["name"]: label["color"] for label in labels}
    pr.labels.exclude(name__in=list(wanted)).delete()
    existing = {label.name: label for label in pr.labels.all()}
    for name, color in wanted.items():
        label = existing.get(name)
        if label is None:
            PullRequestLabel.objects.create(pull_request=pr, name=name, color=color)
        elif label.color != color:
            label.color = color
            label.save(update_fields=["color"])


def _new_reviewer(pr, person, assigned_at, removed_at=None):
    return PullRequestReviewer.objects.create(
        pull_request=pr,
        github_user_id=person.get("github_id"),
        username=person["login"],
        avatar_url=person.get("avatar_url") or "",
        is_team=bool(person.get("is_team")),
        assigned_at=assigned_at,
        removed_at=removed_at,
    )


def reconcile_reviewers(pr, current, at):
    """Make the open review requests match GitHub's current ``requested_reviewers``.

    Closed rows are kept, so assignment history is preserved."""
    open_rows = {row.username.lower(): row for row in pr.reviewers.filter(removed_at__isnull=True)}
    wanted = set()
    for person in current:
        key = person["login"].lower()
        wanted.add(key)
        if key not in open_rows:
            _new_reviewer(pr, person, assigned_at=at)
    for key, row in open_rows.items():
        if key not in wanted:
            row.removed_at = at
            row.save(update_fields=["removed_at"])


def rebuild_reviewers(pr, reviewer_events, current, reviews):
    """Replay timeline history, then settle against GitHub's current state."""
    pr.reviewers.all().delete()
    open_rows = {}
    for event in reviewer_events:
        key = event["login"].lower()
        if event["type"] == "requested":
            if key not in open_rows:
                open_rows[key] = _new_reviewer(pr, event, assigned_at=event["at"])
        else:
            row = open_rows.pop(key, None)
            if row is not None:
                row.removed_at = event["at"]
                row.save(update_fields=["removed_at"])
    current_keys = set()
    for person in current:
        key = person["login"].lower()
        current_keys.add(key)
        if key not in open_rows:
            open_rows[key] = _new_reviewer(pr, person, assigned_at=pr.created_at_github)
    for key, row in open_rows.items():
        if key in current_keys:
            continue
        # GitHub drops a pending request once the person reviews, without a timeline event.
        reviewed_at = [
            r.submitted_at
            for r in reviews
            if r.reviewer.lower() == key and r.submitted_at and (not row.assigned_at or r.submitted_at >= row.assigned_at)
        ]
        row.removed_at = min(reviewed_at) if reviewed_at else pr.updated_at_github
        row.save(update_fields=["removed_at"])


def upsert_reviews(pr, reviews, prune=False):
    ids = []
    for data in reviews:
        ids.append(data["github_review_id"])
        values = {k: v for k, v in data.items() if k != "github_review_id"}
        PullRequestReview.objects.update_or_create(
            pull_request=pr, github_review_id=data["github_review_id"], defaults=values
        )
    if prune:
        pr.reviews.exclude(github_review_id__in=ids).delete()


def record_event(pr, event_type, key, at, actor="", data=None):
    PullRequestEvent.objects.get_or_create(
        pull_request=pr,
        dedupe_key=key,
        defaults={"event_type": event_type, "occurred_at": at, "actor": actor, "data": data or {}},
    )


def sync_commit_events(pr, commits, prune=False):
    keys = []
    for commit in commits:
        key = "commit:%s" % commit["sha"]
        keys.append(key)
        record_event(
            pr,
            PullRequestEvent.COMMIT,
            key,
            commit["committed_at"],
            actor=commit["author"],
            data={"sha": commit["sha"], "message": commit["message"]},
        )
    if prune:
        pr.events.filter(event_type=PullRequestEvent.COMMIT).exclude(dedupe_key__in=keys).delete()


# --------------------------------------------------------------------------
# Full refresh from the REST API (historical import, reconciliation)
# --------------------------------------------------------------------------
@transaction.atomic
def apply_full_pull_request(repo, pr_payload, reviews_payload, commits_payload, timeline_payload):
    """Returns ``(pull_request, created)``."""
    data = normalize.normalize_pull_request(pr_payload, owner=repo.owner)
    reviews = [r for r in (normalize.normalize_review(p) for p in reviews_payload) if r]
    pr, created, _ = upsert_pull_request(repo, data, force=True)
    sync_labels(pr, data["labels"])

    upsert_reviews(pr, reviews, prune=True)
    sync_commit_events(pr, normalize.normalize_commits(commits_payload), prune=True)

    timeline = normalize.normalize_timeline(timeline_payload, owner=repo.owner)
    pr.events.exclude(event_type=PullRequestEvent.COMMIT).delete()
    for event in timeline["events"]:
        record_event(pr, event["event_type"], event["key"], event["at"], actor=event["actor"])
    rebuild_reviewers(pr, timeline["reviewer_events"], data["requested_reviewers"], list(pr.reviews.all()))

    pr.last_synced_at = timezone.now()
    pr.save(update_fields=["last_synced_at"])
    pr = recompute_pull_request(pr) or pr
    pr._review_total = len(reviews)
    return pr, created


def sync_pull_request(repo, number, client=None):
    """Fetch one PR (with reviews, commits and timeline) and store it.
    Returns ``(pull_request, created)``, or ``None`` when the PR no longer exists."""
    client = client or auth.client_for_repository(repo)
    try:
        pr_payload = client.get_pull(repo.full_name, number)
    except GitHubNotFound:
        logger.info("PR %s#%s no longer exists on GitHub", repo.full_name, number)
        return None
    reviews = client.list_reviews(repo.full_name, number)
    commits = client.list_commits(repo.full_name, number)
    timeline = client.list_timeline(repo.full_name, number)
    return apply_full_pull_request(repo, pr_payload, reviews, commits, timeline)


# --------------------------------------------------------------------------
# Webhook application (payload only - no API calls required)
# --------------------------------------------------------------------------
def apply_pull_request_event(repo, payload, delivery_id=""):
    """Apply a ``pull_request`` webhook payload. Returns ``(pr, created)``."""
    action = payload.get("action", "")
    data = normalize.normalize_pull_request(payload["pull_request"], owner=repo.owner)
    at = data["updated_at_github"]
    with transaction.atomic():
        pr, created, applied = upsert_pull_request(repo, data)
        if applied:
            sync_labels(pr, data["labels"])
            reconcile_reviewers(pr, data["requested_reviewers"], at)
            if action == "synchronize" and payload.get("after"):
                record_event(
                    pr, PullRequestEvent.COMMIT, "commit:%s" % payload["after"], at,
                    actor=(payload.get("sender") or {}).get("login", ""),
                    data={"sha": payload["after"], "message": ""},
                )
            elif action in ("reopened", "ready_for_review", "converted_to_draft"):
                record_event(
                    pr, action, "%s:wh-%s" % (action, delivery_id or at.isoformat()), at,
                    actor=(payload.get("sender") or {}).get("login", ""),
                )
            pr.last_synced_at = timezone.now()
            pr.save(update_fields=["last_synced_at"])
            recompute_pull_request(pr)
    return pr, created


def apply_review_event(repo, payload):
    """Apply a ``pull_request_review`` webhook payload. Returns ``(pr, created)``."""
    data = normalize.normalize_pull_request(payload["pull_request"], owner=repo.owner)
    review = normalize.normalize_review(payload.get("review") or {})
    with transaction.atomic():
        pr, created, applied = upsert_pull_request(repo, data)
        if applied:
            sync_labels(pr, data["labels"])
            reconcile_reviewers(pr, data["requested_reviewers"], data["updated_at_github"])
        if review:
            upsert_reviews(pr, [review])
        pr.last_synced_at = timezone.now()
        pr.save(update_fields=["last_synced_at"])
        recompute_pull_request(pr)
    return pr, created


# --------------------------------------------------------------------------
# Historical import (resumable) and reconciliation
# --------------------------------------------------------------------------
def _search_time(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def import_step(repo, client=None, batch_size=None, stats=None):
    """Import one batch of the repository's PRs, oldest first. Returns True when finished.

    Progress is persisted in ``repo.import_cursor`` after every PR, so an
    interrupted import resumes where it stopped."""
    client = client or auth.client_for_repository(repo)
    stats = stats if stats is not None else Stats()
    batch_size = batch_size or settings.IMPORT_BATCH_SIZE
    if repo.import_started_at is None:
        repo.import_started_at = timezone.now()
        repo.save(update_fields=["import_started_at"])
    query = "repo:%s is:pr" % repo.full_name
    if repo.import_cursor:
        query += " created:>=%s" % _search_time(repo.import_cursor)
    result = client.search_issues(query, sort="created", order="asc", per_page=batch_size)
    items = result.get("items", [])
    repo.import_total = repo.import_processed + result.get("total_count", len(items))
    repo.save(update_fields=["import_total"])

    done_numbers = set(
        PullRequest.objects.filter(
            repository=repo, number__in=[i["number"] for i in items], last_synced_at__gte=repo.import_started_at
        ).values_list("number", flat=True)
    )
    fresh = [item for item in items if item["number"] not in done_numbers]
    for item in fresh:
        stats.record(sync_pull_request(repo, item["number"], client))
        repo.import_cursor = normalize.parse_dt(item["created_at"])
        repo.import_processed += 1
        repo.import_heartbeat = timezone.now()
        repo.save(update_fields=["import_cursor", "import_processed", "import_heartbeat"])
    return len(items) < batch_size or not fresh


def reconcile_repository(repo, client=None, max_pages=10, stats=None):
    """Incremental sync / safety net for missed webhooks: re-sync recently updated and still-open PRs."""
    client = client or auth.client_for_repository(repo)
    stats = stats if stats is not None else Stats()
    started = timezone.now()
    since = (repo.last_synced_at or repo.import_finished_at or started) - timedelta(
        hours=settings.RECONCILE_LOOKBACK_HOURS
    )
    query = "repo:%s is:pr updated:>=%s" % (repo.full_name, _search_time(since))
    remote = {}
    for page in range(1, max_pages + 1):
        result = client.search_issues(query, sort="updated", order="asc", per_page=100, page=page)
        for item in result.get("items", []):
            remote[item["number"]] = normalize.parse_dt(item["updated_at"])
        if len(result.get("items", [])) < 100:
            break

    local = dict(
        PullRequest.objects.filter(repository=repo, number__in=list(remote)).values_list("number", "updated_at_github")
    )
    to_sync = {
        number
        for number, updated in remote.items()
        if number not in local or abs((local[number] - updated).total_seconds()) >= 1
    }
    # Open PRs are always refreshed: they are few, and they are where missed events matter.
    to_sync.update(
        PullRequest.objects.filter(repository=repo, state=PullRequest.STATE_OPEN).values_list("number", flat=True)
    )
    for number in sorted(to_sync):
        stats.record(sync_pull_request(repo, number, client))
    repo.last_synced_at = started
    repo.save(update_fields=["last_synced_at"])
    return stats.processed
