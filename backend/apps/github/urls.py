from django.urls import path

from . import views, webhooks

urlpatterns = [
    path("webhooks/github", webhooks.github_webhook, name="github-webhook"),
    path("github/sync", views.SyncView.as_view(), name="github-sync"),
    path("github/webhook-info", views.IntegrationView.as_view(), name="github-webhook-info"),
    path("import/status", views.ImportStatusView.as_view(), name="import-status"),
]
