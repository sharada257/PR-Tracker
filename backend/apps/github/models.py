import uuid

from django.db import models


class GitHubInstallation(models.Model):
    """The GitHub App installation of an organization.

    Only identifying metadata is stored. Installation access tokens are short lived (one hour):
    they are minted on demand from the App's private key and only ever cached, never persisted.
    """

    organization = models.OneToOneField("accounts.Organization", on_delete=models.CASCADE, related_name="installation")
    github_installation_id = models.BigIntegerField(unique=True)
    github_account_id = models.BigIntegerField()
    github_account_login = models.CharField(max_length=100)
    account_type = models.CharField(max_length=20, blank=True)
    repository_selection = models.CharField(max_length=20, blank=True)  # "all" or "selected"
    # GitHub user who installed the App. Only this person becomes the first admin (when known).
    installed_by_github_id = models.BigIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True)
    installed_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    suspended_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return "%s (%s)" % (self.github_account_login, self.github_installation_id)

    @property
    def usable(self):
        return self.active and self.suspended_at is None


class SyncJob(models.Model):
    """One unit of synchronisation work, so the UI can show what the last sync did.

    ``batch`` groups the jobs started by one "sync" (repository discovery plus one job per
    repository); the Settings page summarises the latest batch.
    """

    TYPE_REPOSITORIES = "repositories"  # discover repositories through the installation
    TYPE_INITIAL = "initial"  # historical import of one repository
    TYPE_INCREMENTAL = "incremental"  # re-check what changed since the last sync
    TYPE_CHOICES = [(TYPE_REPOSITORIES, "Repositories"), (TYPE_INITIAL, "Initial import"), (TYPE_INCREMENTAL, "Incremental")]

    TRIGGER_MANUAL = "manual"
    TRIGGER_SCHEDULED = "scheduled"
    TRIGGER_INSTALL = "install"
    TRIGGER_WEBHOOK = "webhook"

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    STATUS_CHOICES = [(s, s) for s in (PENDING, RUNNING, COMPLETED, FAILED, PARTIAL)]
    ACTIVE_STATUSES = (PENDING, RUNNING)

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="sync_jobs")
    repository = models.ForeignKey(
        "tracker.Repository", null=True, blank=True, on_delete=models.CASCADE, related_name="sync_jobs"
    )
    batch = models.UUIDField(default=uuid.uuid4, db_index=True)
    type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    trigger = models.CharField(max_length=20, default=TRIGGER_MANUAL)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    records_processed = models.IntegerField(default=0)
    records_created = models.IntegerField(default=0)
    records_updated = models.IntegerField(default=0)
    reviews_processed = models.IntegerField(default=0)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["organization", "-created_at"])]

    def __str__(self):
        return "%s %s (%s)" % (self.type, self.status, self.organization_id)


class WebhookEvent(models.Model):
    """Every webhook delivery we accepted. ``github_delivery_id`` gives idempotency."""

    RECEIVED = "received"
    PROCESSING = "processing"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"
    STATUS_CHOICES = [(s, s) for s in (RECEIVED, PROCESSING, PROCESSED, IGNORED, FAILED)]

    github_delivery_id = models.CharField(max_length=64, unique=True)
    event_type = models.CharField(max_length=64)
    action = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=RECEIVED, db_index=True)
    attempts = models.IntegerField(default=0)
    error = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self):
        return "%s/%s (%s)" % (self.event_type, self.action, self.status)
