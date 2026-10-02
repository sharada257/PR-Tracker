"""Turns a stored webhook delivery into database updates (runs in a worker)."""
from django.utils import timezone

from apps.tracker.models import Repository

from . import sync
from .models import GitHubInstallation, WebhookEvent

PULL_REQUEST_ACTIONS = {
    "opened",
    "reopened",
    "closed",
    "assigned",
    "unassigned",
    "review_requested",
    "review_request_removed",
    "synchronize",
    "edited",
    "ready_for_review",
    "converted_to_draft",
    "labeled",
    "unlabeled",
}
REVIEW_ACTIONS = {"submitted", "edited", "dismissed"}
INSTALLATION_ACTIONS = {"created", "deleted", "suspend", "unsuspend", "new_permissions_accepted"}
INSTALLATION_REPOSITORY_ACTIONS = {"added", "removed"}
SUPPORTED_EVENTS = {"pull_request", "pull_request_review", "installation", "installation_repositories"}


def is_supported(event_type, action):
    if event_type == "pull_request":
        return action in PULL_REQUEST_ACTIONS
    if event_type == "pull_request_review":
        return action in REVIEW_ACTIONS
    if event_type == "installation":
        return action in INSTALLATION_ACTIONS
    if event_type == "installation_repositories":
        return action in INSTALLATION_REPOSITORY_ACTIONS
    return False


def process_webhook(event):
    """Returns ``(status, follow_ups, org_ids)``: ``follow_ups`` are ``(repo_id, pr_number)`` pairs that
    need a full API refresh (e.g. a PR we had never seen) and ``org_ids`` are organizations that need a
    repository re-discovery / sync."""
    payload = event.payload
    action = payload.get("action", "")
    if not is_supported(event.event_type, action):
        return WebhookEvent.IGNORED, [], []
    if event.event_type == "installation":
        return _process_installation(payload, action)
    if event.event_type == "installation_repositories":
        return _process_installation_repositories(payload, action)
    return _process_pull_request_event(event, payload)


def _process_pull_request_event(event, payload):
    github_repo_id = (payload.get("repository") or {}).get("id")
    repos = Repository.objects.filter(github_repository_id=github_repo_id, is_active=True, is_accessible=True)
    installation_id = (payload.get("installation") or {}).get("id")
    if installation_id:
        repos = repos.filter(organization__installation__github_installation_id=installation_id)
    repos = list(repos.select_related("organization"))
    if not repos:
        return WebhookEvent.IGNORED, [], []

    follow_ups = []
    for repo in repos:
        if event.event_type == "pull_request":
            pr, created = sync.apply_pull_request_event(repo, payload, delivery_id=event.github_delivery_id)
        else:
            pr, created = sync.apply_review_event(repo, payload)
        if created:
            follow_ups.append((repo.id, pr.number))
    return WebhookEvent.PROCESSED, follow_ups, []


def _process_installation(payload, action):
    installation_data = payload.get("installation") or {}
    account = installation_data.get("account") or {}
    installation_id = installation_data.get("id")
    if not installation_id or not account.get("id"):
        return WebhookEvent.IGNORED, [], []
    installation = GitHubInstallation.objects.filter(github_installation_id=installation_id).first()

    if action == "created":
        organization, _ = sync.upsert_organization(
            account, installation_id, installation_data.get("repository_selection") or "",
            installed_by=(payload.get("sender") or {}).get("id"),  # whoever completed the setup on GitHub
        )
        return WebhookEvent.PROCESSED, [], [organization.id]
    if installation is None:
        return WebhookEvent.IGNORED, [], []

    organization = installation.organization
    if action == "deleted":
        installation.active = False
        installation.save(update_fields=["active", "updated_at"])
        # Nothing is accessible any more; keep the history but hide it.
        organization.repositories.update(is_accessible=False)
        return WebhookEvent.PROCESSED, [], []
    if action == "suspend":
        installation.suspended_at = timezone.now()
        installation.save(update_fields=["suspended_at", "updated_at"])
        return WebhookEvent.PROCESSED, [], []
    if action == "unsuspend":
        installation.suspended_at = None
        installation.active = True
        installation.save(update_fields=["suspended_at", "active", "updated_at"])
        return WebhookEvent.PROCESSED, [], [organization.id]
    # new_permissions_accepted: just refresh.
    return WebhookEvent.PROCESSED, [], [organization.id]


def _process_installation_repositories(payload, action):
    installation_id = (payload.get("installation") or {}).get("id")
    installation = GitHubInstallation.objects.filter(github_installation_id=installation_id).first()
    if installation is None:
        return WebhookEvent.IGNORED, [], []
    organization = installation.organization
    installation.repository_selection = payload.get("repository_selection") or installation.repository_selection
    installation.save(update_fields=["repository_selection", "updated_at"])
    if action == "removed":
        removed = [r["id"] for r in payload.get("repositories_removed") or []]
        organization.repositories.filter(github_repository_id__in=removed).update(is_accessible=False)
        return WebhookEvent.PROCESSED, [], []
    # added: the webhook payload is too thin to build a Repository, so re-discover through the API.
    return WebhookEvent.PROCESSED, [], [organization.id]
