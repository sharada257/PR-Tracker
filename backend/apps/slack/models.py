from django.db import models


class SlackIntegration(models.Model):
    """One Slack workspace connected to an organization. The bot token is stored encrypted."""

    organization = models.OneToOneField("accounts.Organization", on_delete=models.CASCADE, related_name="slack")
    team_id = models.CharField(max_length=40)
    team_name = models.CharField(max_length=200, blank=True)
    bot_user_id = models.CharField(max_length=40, blank=True)
    bot_token_encrypted = models.TextField()
    connected_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    connected_at = models.DateTimeField(auto_now_add=True)
    # Set when Slack rejects the token (revoked / workspace removed the app): notifications pause.
    active = models.BooleanField(default=True)
    last_error = models.CharField(max_length=200, blank=True)

    notify_review_requests = models.BooleanField(default=True)
    # Tell the PR author when a reviewer approves or asks for changes.
    notify_review_outcomes = models.BooleanField(default=True)
    send_reminders = models.BooleanField(default=True)
    reminder_after_hours = models.PositiveIntegerField(default=24)
    max_reminders = models.PositiveIntegerField(default=2)
    business_hours_only = models.BooleanField(default=True)

    def __str__(self):
        return "%s <-> %s" % (self.organization, self.team_name or self.team_id)


class SlackUserLink(models.Model):
    """Which Slack person is this PR Tracker member (so we know whom to message)."""

    AUTO, MANUAL = "auto", "manual"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="slack_links")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="slack_links")
    slack_user_id = models.CharField(max_length=40)
    source = models.CharField(max_length=10, default=AUTO)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["organization", "user"], name="uniq_slack_link_per_org_user")]


class SlackNotification(models.Model):
    """What was already sent, so a webhook, a re-sync and the scheduler never send the same DM twice."""

    REVIEW_REQUESTED, REMINDER = "review_requested", "reminder"
    REVIEW_REREQUESTED = "review_rerequested"
    REVIEW_APPROVED, REVIEW_CHANGES = "review_approved", "review_changes"
    CHANGES_PUSHED = "changes_pushed"
    SENT, FAILED = "sent", "failed"

    organization = models.ForeignKey("accounts.Organization", on_delete=models.CASCADE, related_name="slack_notifications")
    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="slack_notifications")
    pull_request = models.ForeignKey("tracker.PullRequest", on_delete=models.CASCADE, related_name="slack_notifications")
    kind = models.CharField(max_length=20)
    key = models.CharField(max_length=80)
    status = models.CharField(max_length=10, default=SENT)
    error = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["kind", "pull_request", "user", "key"], name="uniq_slack_notification")
        ]
