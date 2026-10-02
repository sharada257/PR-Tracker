from django.conf import settings
from django.db.models import Max
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import audit
from apps.accounts.permissions import IsOrgAdmin
from apps.tracker.models import PullRequest, Repository
from apps.tracker.scope import visible_repositories

from . import auth
from .models import GitHubInstallation, SyncJob, WebhookEvent
from .tasks import expire_stale_jobs, start_organization_sync

WEBHOOK_EVENTS = ["pull_request", "pull_request_review", "installation", "installation_repositories"]


def summarize_sync(org):
    """What the latest sync (one batch of SyncJobs) did, for the Settings page and the "Last synced" label."""
    expire_stale_jobs(org)
    latest = SyncJob.objects.filter(organization=org).first()
    last_synced = visible_repositories(org).aggregate(at=Max("last_synced_at"))["at"]
    if latest is None:
        return {"status": None, "last_synced_at": last_synced}
    jobs = list(SyncJob.objects.filter(organization=org, batch=latest.batch))
    statuses = {job.status for job in jobs}
    if statuses & set(SyncJob.ACTIVE_STATUSES):
        overall = SyncJob.RUNNING
    elif statuses == {SyncJob.COMPLETED}:
        overall = SyncJob.COMPLETED
    elif statuses <= {SyncJob.FAILED}:
        overall = SyncJob.FAILED
    else:
        overall = SyncJob.PARTIAL
    repo_jobs = [j for j in jobs if j.type != SyncJob.TYPE_REPOSITORIES]
    finished = [j.completed_at for j in jobs if j.completed_at]
    errors = [j.error_message for j in jobs if j.error_message]
    return {
        "status": overall,
        "trigger": latest.trigger,
        "started_at": min((j.started_at or j.created_at) for j in jobs),
        "completed_at": max(finished) if overall != SyncJob.RUNNING and finished else None,
        "repositories_updated": len(
            [j for j in repo_jobs if j.status in (SyncJob.COMPLETED, SyncJob.PARTIAL)]
        ),
        "repositories_failed": len([j for j in repo_jobs if j.status in (SyncJob.FAILED, SyncJob.PARTIAL)]),
        "pull_requests_updated": sum(j.records_processed for j in repo_jobs),
        "pull_requests_created": sum(j.records_created for j in repo_jobs),
        "reviews_updated": sum(j.reviews_processed for j in repo_jobs),
        "error": errors[0] if errors else "",
        "last_synced_at": last_synced,
    }


class SyncView(APIView):
    """POST /api/github/sync - "Sync GitHub". Any member can refresh; ``full`` (re-import everything) is admin-only."""

    def post(self, request):
        full = bool(request.data.get("full"))
        if full and not request.membership.is_admin:
            return Response({"detail": "Only organization admins can re-import."}, status=status.HTTP_403_FORBIDDEN)
        installation = getattr(request.org, "installation", None)
        if installation is None or not installation.usable:
            return Response({"detail": "The GitHub App is not installed for this organization."}, status=status.HTTP_409_CONFLICT)
        batch = start_organization_sync(request.org, trigger=SyncJob.TRIGGER_MANUAL, full=full)
        audit("sync.requested", user=request.user, request=request, full=full, started=batch is not None)
        # Already running is not an error: the caller just keeps watching the status.
        return Response(
            {"started": batch is not None, "already_running": batch is None},
            status=status.HTTP_202_ACCEPTED,
        )


class ImportStatusView(APIView):
    """GET /api/import/status - progress for the Settings page and the "Last synced" label."""

    def get(self, request):
        org = request.org
        repos = visible_repositories(org)
        items = [
            {
                "id": r.id,
                "full_name": r.full_name,
                "status": r.import_status,
                "processed": r.import_processed,
                "total": r.import_total,
                "error": r.import_error,
                "started_at": r.import_started_at,
                "finished_at": r.import_finished_at,
                "last_synced_at": r.last_synced_at,
            }
            for r in repos
        ]
        running = [i for i in items if i["status"] in (Repository.IMPORT_QUEUED, Repository.IMPORT_RUNNING)]
        summary = summarize_sync(org)
        return Response(
            {
                "active": bool(running) or summary["status"] == SyncJob.RUNNING,
                "repositories": items,
                "sync": summary,
                "last_synced_at": summary["last_synced_at"],
                "totals": {
                    "processed": sum(i["processed"] for i in items),
                    "pull_requests": PullRequest.objects.filter(repository__in=repos).count(),
                },
            }
        )


class IntegrationView(APIView):
    """GET /api/github/webhook-info (admins) - the organization's GitHub App connection and webhook setup.
    Never reveals secrets."""

    permission_classes = [IsOrgAdmin]

    def get(self, request):
        org = request.org
        installation = GitHubInstallation.objects.filter(organization=org).first()
        recent = WebhookEvent.objects.order_by("-received_at")
        if installation is not None:
            recent = recent.filter(payload__installation__id=installation.github_installation_id)
        return Response(
            {
                "url": settings.PUBLIC_URL + "/api/webhooks/github",
                "secret_configured": bool(settings.GITHUB_WEBHOOK_SECRET),
                "events": WEBHOOK_EVENTS,
                "installation": {
                    "account": installation.github_account_login,
                    "account_type": installation.account_type,
                    "repository_selection": installation.repository_selection,
                    "active": installation.usable,
                    "installed_at": installation.installed_at,
                }
                if installation
                else None,
                "recent_deliveries": [
                    {
                        "id": e.id,
                        "event": e.event_type,
                        "action": e.action,
                        "status": e.status,
                        "error": e.error,
                        "received_at": e.received_at,
                        "processed_at": e.processed_at,
                    }
                    for e in recent[:10]
                ],
                "github_app": {
                    "configured": auth.app_configured(),
                    "install_url": auth.install_url(),
                },
            }
        )
