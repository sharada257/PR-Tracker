"""Local, query-optimised copy of the GitHub data we care about.

GitHub remains the source of truth: every row here can be rebuilt by re-syncing
from the GitHub API. Internal field names intentionally do not mirror GitHub's
payloads (see ``apps.github.normalize`` for the translation layer).
"""
from django.db import models


class Repository(models.Model):
    IMPORT_IDLE = "idle"
    IMPORT_QUEUED = "queued"
    IMPORT_RUNNING = "running"
    IMPORT_DONE = "done"
    IMPORT_FAILED = "failed"
    IMPORT_STATUS_CHOICES = [
        (IMPORT_IDLE, "Not imported"),
        (IMPORT_QUEUED, "Queued"),
        (IMPORT_RUNNING, "Importing"),
        (IMPORT_DONE, "Imported"),
        (IMPORT_FAILED, "Failed"),
    ]

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="repositories")
    github_repository_id = models.BigIntegerField()
    owner = models.CharField(max_length=100)
    name = models.CharField(max_length=200)
    full_name = models.CharField(max_length=300, db_index=True)
    url = models.URLField(max_length=500)
    default_branch = models.CharField(max_length=255, blank=True)
    private = models.BooleanField(default=False)
    github_created_at = models.DateTimeField(null=True, blank=True)
    github_updated_at = models.DateTimeField(null=True, blank=True)
    # Tracked by PR Tracker. Newly discovered repositories start tracked (the organization already
    # chose them when installing the GitHub App); an admin can stop tracking one.
    is_active = models.BooleanField(default=True, db_index=True)
    # False once the installation stops returning the repository (access removed, deleted...).
    # Such repositories are never shown, whatever ``is_active`` says.
    is_accessible = models.BooleanField(default=True)

    # Resumable historical import state.
    import_status = models.CharField(max_length=10, choices=IMPORT_STATUS_CHOICES, default=IMPORT_IDLE)
    import_cursor = models.DateTimeField(null=True, blank=True)
    import_total = models.IntegerField(null=True, blank=True)
    import_processed = models.IntegerField(default=0)
    import_error = models.TextField(blank=True)
    import_heartbeat = models.DateTimeField(null=True, blank=True)
    import_started_at = models.DateTimeField(null=True, blank=True)
    import_finished_at = models.DateTimeField(null=True, blank=True)
    # When the repository was last brought up to date (end of the import, then every reconcile).
    last_synced_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "repositories"
        ordering = ["full_name"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "github_repository_id"], name="uniq_repo_per_org"),
        ]

    def __str__(self):
        return self.full_name


class PullRequest(models.Model):
    STATE_OPEN = "open"
    STATE_CLOSED = "closed"

    STATUS_OPEN = "open"
    STATUS_REVIEW = "review"
    STATUS_CHANGES_REQUESTED = "changes_requested"
    STATUS_APPROVED = "approved"
    STATUS_MERGED = "merged"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_REVIEW, "In Review"),
        (STATUS_CHANGES_REQUESTED, "Changes Requested"),
        (STATUS_APPROVED, "Approved"),
        (STATUS_MERGED, "Merged"),
        (STATUS_CLOSED, "Closed"),
    ]
    OPEN_STATUSES = (STATUS_OPEN, STATUS_REVIEW, STATUS_CHANGES_REQUESTED, STATUS_APPROVED)

    repository = models.ForeignKey(Repository, on_delete=models.CASCADE, related_name="pull_requests")
    github_pr_id = models.BigIntegerField()
    number = models.PositiveIntegerField()
    title = models.TextField()
    description = models.TextField(blank=True)
    author_login = models.CharField(max_length=100, db_index=True)
    author_github_id = models.BigIntegerField(null=True, blank=True)
    author_avatar_url = models.URLField(max_length=500, blank=True)
    # GitHub's raw lifecycle state ("open" / "closed") ...
    state = models.CharField(max_length=10, default=STATE_OPEN, db_index=True)
    draft = models.BooleanField(default=False)
    # ... and our derived, richer status (see ``metrics.derive_status``).
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)

    url = models.URLField(max_length=500)
    base_branch = models.CharField(max_length=255, blank=True)
    head_branch = models.CharField(max_length=255, blank=True)
    merge_commit_sha = models.CharField(max_length=64, blank=True)

    created_at_github = models.DateTimeField(db_index=True)
    updated_at_github = models.DateTimeField()
    closed_at = models.DateTimeField(null=True, blank=True)
    merged_at = models.DateTimeField(null=True, blank=True, db_index=True)

    additions = models.IntegerField(default=0)
    deletions = models.IntegerField(default=0)
    changed_files = models.IntegerField(default=0)
    commits_count = models.IntegerField(default=0)
    comments_count = models.IntegerField(default=0)

    # Calculated application metrics (NOT GitHub fields). Recomputed on every sync.
    first_review_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    review_count = models.IntegerField(default=0)
    review_cycles = models.IntegerField(default=0)

    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at_github"]
        constraints = [
            models.UniqueConstraint(fields=["repository", "github_pr_id"], name="uniq_pr_per_repo"),
            models.UniqueConstraint(fields=["repository", "number"], name="uniq_pr_number_per_repo"),
        ]
        indexes = [
            models.Index(fields=["repository", "-created_at_github"]),
            models.Index(fields=["status", "-created_at_github"]),
        ]

    def __str__(self):
        return "%s#%s" % (self.repository.full_name, self.number)

    @property
    def is_merged(self):
        return self.merged_at is not None


class PullRequestReviewer(models.Model):
    """A review request. Rows are never deleted so reviewer history is preserved."""

    pull_request = models.ForeignKey(PullRequest, on_delete=models.CASCADE, related_name="reviewers")
    github_user_id = models.BigIntegerField(null=True, blank=True)
    username = models.CharField(max_length=100, db_index=True)  # team slugs are stored as "org/team"
    avatar_url = models.URLField(max_length=500, blank=True)
    is_team = models.BooleanField(default=False)
    assigned_at = models.DateTimeField(null=True, blank=True)
    removed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["assigned_at", "id"]
        indexes = [models.Index(fields=["pull_request", "username"])]

    def __str__(self):
        return "%s -> %s" % (self.username, self.pull_request_id)


class PullRequestReview(models.Model):
    """A submitted review. ``state`` keeps GitHub's original value."""

    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    COMMENTED = "COMMENTED"
    DISMISSED = "DISMISSED"
    PENDING = "PENDING"

    pull_request = models.ForeignKey(PullRequest, on_delete=models.CASCADE, related_name="reviews")
    github_review_id = models.BigIntegerField()
    reviewer = models.CharField(max_length=100, db_index=True)
    reviewer_github_id = models.BigIntegerField(null=True, blank=True)
    reviewer_avatar_url = models.URLField(max_length=500, blank=True)
    state = models.CharField(max_length=30)
    body = models.TextField(blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    commit_id = models.CharField(max_length=64, blank=True)

    class Meta:
        ordering = ["submitted_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["pull_request", "github_review_id"], name="uniq_review_per_pr"),
        ]

    def __str__(self):
        return "%s %s" % (self.reviewer, self.state)


class PullRequestLabel(models.Model):
    pull_request = models.ForeignKey(PullRequest, on_delete=models.CASCADE, related_name="labels")
    name = models.CharField(max_length=200, db_index=True)
    color = models.CharField(max_length=10, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["pull_request", "name"], name="uniq_label_per_pr"),
        ]

    def __str__(self):
        return self.name


class PullRequestEvent(models.Model):
    """Timeline events that cannot be derived from the structured tables
    (pushed commits, reopen / draft transitions)."""

    COMMIT = "commit"
    REOPENED = "reopened"
    READY_FOR_REVIEW = "ready_for_review"
    CONVERTED_TO_DRAFT = "converted_to_draft"

    pull_request = models.ForeignKey(PullRequest, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=40)
    actor = models.CharField(max_length=100, blank=True)
    occurred_at = models.DateTimeField(db_index=True)
    # Natural key (e.g. "commit:<sha>") making event recording idempotent.
    dedupe_key = models.CharField(max_length=200)
    data = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["occurred_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["pull_request", "dedupe_key"], name="uniq_event_per_pr"),
        ]

    def __str__(self):
        return "%s @ %s" % (self.event_type, self.occurred_at)
