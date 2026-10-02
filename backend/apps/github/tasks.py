"""Celery tasks: organization sync, import, reconciliation and webhook processing.

Every unit of work is recorded as a ``SyncJob`` so the UI can say what the last sync did.
"""
import logging
import uuid
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone

from apps.accounts.models import Organization
from apps.tracker.models import Repository

from . import processing, sync
from .client import GitHubAuthError, GitHubError, GitHubRateLimited
from .models import GitHubInstallation, SyncJob, WebhookEvent

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)
JOB_STALE_AFTER = timedelta(hours=1)


# --------------------------------------------------------------------------
# SyncJob bookkeeping
# --------------------------------------------------------------------------
def new_job(organization, job_type, trigger, batch=None, repository=None, status=SyncJob.PENDING):
    now = timezone.now()
    return SyncJob.objects.create(
        organization=organization,
        repository=repository,
        batch=batch or uuid.uuid4(),
        type=job_type,
        trigger=trigger,
        status=status,
        started_at=now if status == SyncJob.RUNNING else None,
    )


def expire_stale_jobs(organization):
    """Jobs that never finished (worker killed...) must not block new syncs forever."""
    return SyncJob.objects.filter(
        organization=organization,
        status__in=SyncJob.ACTIVE_STATUSES,
        created_at__lt=timezone.now() - JOB_STALE_AFTER,
    ).update(status=SyncJob.FAILED, completed_at=timezone.now(), error_message="Interrupted before it finished.")


def _job(job_id):
    return SyncJob.objects.filter(pk=job_id).first() if job_id else None


def _start(job):
    if job and job.status == SyncJob.PENDING:
        job.status = SyncJob.RUNNING
        job.started_at = timezone.now()
        job.save(update_fields=["status", "started_at"])


def _add_stats(job, stats):
    if job is None:
        return
    SyncJob.objects.filter(pk=job.pk).update(
        records_processed=F("records_processed") + stats.processed,
        records_created=F("records_created") + stats.created,
        records_updated=F("records_updated") + stats.updated,
        reviews_processed=F("reviews_processed") + stats.reviews,
    )


def _finish(job, status=SyncJob.COMPLETED, error=""):
    if job is None:
        return
    job.refresh_from_db()
    if status == SyncJob.FAILED and job.records_processed:
        status = SyncJob.PARTIAL  # some data made it in before the failure
    job.status = status
    job.error_message = error
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "error_message", "completed_at"])


# --------------------------------------------------------------------------
# Organization sync
# --------------------------------------------------------------------------
def start_organization_sync(organization, trigger=SyncJob.TRIGGER_MANUAL, full=False):
    """Queue a sync of the whole organization. Returns the batch id, or ``None`` when a sync is
    already running for it."""
    expire_stale_jobs(organization)
    if SyncJob.objects.filter(organization=organization, status__in=SyncJob.ACTIVE_STATUSES).exists():
        return None
    batch = uuid.uuid4()
    # Create the first job up front so the UI sees "running" immediately, even before a worker picks it up.
    job = new_job(organization, SyncJob.TYPE_REPOSITORIES, trigger, batch=batch)
    sync_organization.delay(organization.id, job.id, full)
    return batch


@shared_task
def sync_organization(org_id, job_id, full=False):
    """Discover the repositories the installation can see, then import/refresh each one."""
    job = _job(job_id)
    org = Organization.objects.select_related("installation").filter(pk=org_id).first()
    if job is None or org is None:
        return "skipped"
    _start(job)
    try:
        created, updated = sync.refresh_repositories(org)
    except GitHubError as exc:
        logger.warning("Repository discovery failed for %s: %s", org, exc)
        _finish(job, SyncJob.FAILED, "Could not list repositories: %s" % exc)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - recorded on the job, then surfaced in the UI
        logger.exception("Repository discovery failed for %s", org)
        _finish(job, SyncJob.FAILED, str(exc)[:500])
        return "failed"
    SyncJob.objects.filter(pk=job.pk).update(
        records_processed=len(created) + len(updated), records_created=len(created), records_updated=len(updated)
    )
    _finish(job)

    for repo in org.repositories.filter(is_active=True, is_accessible=True):
        if full or repo.import_status != Repository.IMPORT_DONE:
            start_import(repo, restart=full, trigger=job.trigger, batch=job.batch)
        else:
            incremental = new_job(org, SyncJob.TYPE_INCREMENTAL, job.trigger, batch=job.batch, repository=repo)
            reconcile_repository.delay(repo.id, incremental.id)
    return "ok"


# --------------------------------------------------------------------------
# Historical import
# --------------------------------------------------------------------------
def start_import(repo, restart=False, trigger=SyncJob.TRIGGER_MANUAL, batch=None):
    """Queue (or resume) the historical import for a repository. Returns the SyncJob, or ``None``
    when nothing needed starting."""
    now = timezone.now()
    if repo.import_status == Repository.IMPORT_RUNNING and repo.import_heartbeat and now - repo.import_heartbeat < STALE_AFTER:
        return None
    if repo.import_status == Repository.IMPORT_DONE and not restart:
        return None
    fresh = restart or repo.import_status in (Repository.IMPORT_IDLE, Repository.IMPORT_DONE) or repo.import_started_at is None
    if fresh:
        repo.import_cursor = None
        repo.import_processed = 0
        repo.import_total = None
        repo.import_started_at = now
        repo.import_finished_at = None
    repo.import_status = Repository.IMPORT_QUEUED
    repo.import_error = ""
    repo.save()
    job = new_job(repo.organization, SyncJob.TYPE_INITIAL, trigger, batch=batch, repository=repo)
    import_repository.delay(repo.id, job.id)
    return job


@shared_task
def import_repository(repo_id, job_id=None):
    """Process one batch, then re-queue itself until the history is exhausted."""
    now = timezone.now()
    claimed = (
        Repository.objects.filter(pk=repo_id, is_active=True)
        .filter(
            Q(import_status=Repository.IMPORT_QUEUED)
            | Q(import_status=Repository.IMPORT_RUNNING, import_heartbeat__lt=now - STALE_AFTER)
        )
        .update(import_status=Repository.IMPORT_RUNNING, import_heartbeat=now)
    )
    job = _job(job_id)
    if not claimed:
        if job is not None and job.status == SyncJob.PENDING:
            job.delete()
        return "skipped"
    _start(job)
    repo = Repository.objects.select_related("organization__installation").get(pk=repo_id)
    stats = sync.Stats()
    try:
        finished = sync.import_step(repo, stats=stats)
    except GitHubRateLimited as exc:
        _add_stats(job, stats)
        if settings.CELERY_TASK_ALWAYS_EAGER:
            _fail_import(repo, "Rate limited by GitHub: %s" % exc, job)
            return "failed"
        repo.import_status = Repository.IMPORT_QUEUED
        repo.import_error = "Rate limited by GitHub; resuming at %s" % exc.reset_at.strftime("%H:%M UTC")
        repo.save(update_fields=["import_status", "import_error", "updated_at"])
        import_repository.apply_async((repo_id, job_id), countdown=exc.retry_after_seconds)
        return "rate-limited"
    except GitHubAuthError as exc:
        _add_stats(job, stats)
        _fail_import(repo, "GitHub rejected the app's credentials: %s" % exc, job)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - recorded on the repo, then surfaced in the UI
        logger.exception("Import failed for %s", repo.full_name)
        _add_stats(job, stats)
        _fail_import(repo, str(exc)[:500], job)
        return "failed"

    _add_stats(job, stats)
    repo.refresh_from_db()
    if finished:
        repo.import_status = Repository.IMPORT_DONE
        repo.import_finished_at = timezone.now()
        repo.import_error = ""
        repo.last_synced_at = repo.import_started_at
        repo.save()
        _finish(job)
        return "done"
    repo.import_status = Repository.IMPORT_QUEUED
    repo.save(update_fields=["import_status", "updated_at"])
    import_repository.delay(repo_id, job_id)
    return "continued"


def _fail_import(repo, message, job=None):
    repo.import_status = Repository.IMPORT_FAILED
    repo.import_error = message
    repo.save(update_fields=["import_status", "import_error", "updated_at"])
    _finish(job, SyncJob.FAILED, message)


# --------------------------------------------------------------------------
# Reconciliation
# --------------------------------------------------------------------------
@shared_task
def reconcile_repository(repo_id, job_id=None):
    job = _job(job_id)
    repo = Repository.objects.select_related("organization__installation").filter(pk=repo_id).first()
    if repo is None or not repo.is_active or repo.import_status != Repository.IMPORT_DONE:
        if job is not None:
            job.delete()
        return "skipped"
    _start(job)
    stats = sync.Stats()
    try:
        sync.reconcile_repository(repo, stats=stats)
    except GitHubRateLimited as exc:
        _add_stats(job, stats)
        if not settings.CELERY_TASK_ALWAYS_EAGER:
            reconcile_repository.apply_async((repo_id, job_id), countdown=exc.retry_after_seconds)
            return "rate-limited"
        _finish(job, SyncJob.FAILED, "Rate limited by GitHub: %s" % exc)
        return "failed"
    except GitHubError as exc:
        logger.warning("Reconcile failed for %s: %s", repo.full_name, exc)
        _add_stats(job, stats)
        _finish(job, SyncJob.FAILED, str(exc)[:500])
        return "failed"
    except Exception as exc:  # noqa: BLE001
        logger.exception("Reconcile failed for %s", repo.full_name)
        _add_stats(job, stats)
        _finish(job, SyncJob.FAILED, str(exc)[:500])
        return "failed"
    _add_stats(job, stats)
    _finish(job)
    return "synced %d" % stats.processed


@shared_task
def reconcile_all():
    """Scheduled sync of every organization with a working installation."""
    count = 0
    for org in Organization.objects.select_related("installation").filter(
        installation__active=True, installation__suspended_at__isnull=True
    ):
        if start_organization_sync(org, trigger=SyncJob.TRIGGER_SCHEDULED):
            count += 1
    # Deliveries stored while the broker was down never got queued; pick them up.
    stuck = WebhookEvent.objects.filter(
        status=WebhookEvent.RECEIVED, received_at__lt=timezone.now() - timedelta(minutes=5)
    ).values_list("id", flat=True)
    for event_id in stuck:
        process_webhook_event.delay(event_id)
    return count


@shared_task(bind=True, max_retries=5)
def sync_pull_request(self, repo_id, number):
    repo = Repository.objects.select_related("organization__installation").filter(pk=repo_id).first()
    if repo is None or not repo.is_active:
        return "skipped"
    try:
        sync.sync_pull_request(repo, number)
    except GitHubRateLimited as exc:
        raise self.retry(exc=exc, countdown=exc.retry_after_seconds)
    except GitHubAuthError:
        logger.warning("Credentials rejected while syncing %s#%s", repo.full_name, number)
        return "auth-failed"
    except GitHubError as exc:
        raise self.retry(exc=exc, countdown=60 * 2**self.request.retries)
    return "ok"


# --------------------------------------------------------------------------
# Webhooks
# --------------------------------------------------------------------------
@shared_task(bind=True, max_retries=5)
def process_webhook_event(self, event_id):
    claimed = WebhookEvent.objects.filter(
        pk=event_id, status__in=[WebhookEvent.RECEIVED, WebhookEvent.FAILED]
    ).update(status=WebhookEvent.PROCESSING)
    if not claimed:  # already processed / being processed -> idempotent no-op
        return "skipped"
    event = WebhookEvent.objects.get(pk=event_id)
    event.attempts += 1
    try:
        status, follow_ups, org_ids = processing.process_webhook(event)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Webhook %s failed", event.github_delivery_id)
        event.status = WebhookEvent.FAILED
        event.error = str(exc)[:1000]
        event.save(update_fields=["status", "error", "attempts"])
        raise self.retry(exc=exc, countdown=30 * 2**self.request.retries)
    event.status = status
    event.error = ""
    event.processed_at = timezone.now()
    event.save(update_fields=["status", "error", "processed_at", "attempts"])
    for repo_id, number in follow_ups:
        sync_pull_request.delay(repo_id, number)
    for org in Organization.objects.filter(pk__in=org_ids):
        start_organization_sync(org, trigger=SyncJob.TRIGGER_WEBHOOK)
    _notify_slack(event)
    return status


def _notify_slack(event):
    """A review was requested or submitted: send the Slack message now instead of waiting for the next scan."""
    is_request = event.event_type == "pull_request" and event.action in ("review_requested", "synchronize")
    is_outcome = event.event_type == "pull_request_review" and event.action == "submitted"
    if not (is_request or is_outcome):
        return
    try:
        from apps.slack.tasks import dispatch_organization

        installation_id = (event.payload.get("installation") or {}).get("id")
        org_id = (
            GitHubInstallation.objects.filter(github_installation_id=installation_id)
            .values_list("organization_id", flat=True)
            .first()
        )
        if org_id:
            dispatch_organization.delay(org_id)
    except Exception:  # noqa: BLE001 - Slack must never break webhook processing
        logger.exception("Could not queue Slack notifications")


@shared_task
def prune_webhook_events():
    cutoff = timezone.now() - timedelta(days=settings.WEBHOOK_RETENTION_DAYS)
    deleted, _ = WebhookEvent.objects.filter(received_at__lt=cutoff, status__in=["processed", "ignored"]).delete()
    return deleted
