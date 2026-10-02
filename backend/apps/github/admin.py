from django.contrib import admin

from .models import GitHubInstallation, SyncJob, WebhookEvent


@admin.register(GitHubInstallation)
class InstallationAdmin(admin.ModelAdmin):
    list_display = ("github_account_login", "github_installation_id", "organization", "active", "suspended_at")


@admin.register(SyncJob)
class SyncJobAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "repository", "type", "status", "records_processed")
    list_filter = ("status", "type")


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("received_at", "event_type", "action", "status")
    list_filter = ("status", "event_type")
