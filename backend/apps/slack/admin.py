from django.contrib import admin

from .models import SlackIntegration, SlackNotification, SlackUserLink


@admin.register(SlackIntegration)
class SlackIntegrationAdmin(admin.ModelAdmin):
    list_display = ("organization", "team_name", "active", "connected_at")
    exclude = ("bot_token_encrypted",)


@admin.register(SlackUserLink)
class SlackUserLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "slack_user_id", "source")


@admin.register(SlackNotification)
class SlackNotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "kind", "status", "pull_request")
    list_filter = ("kind", "status")
