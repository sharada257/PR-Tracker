from django.contrib import admin
from django.urls import include, path

from apps.slack.views import SlackView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("apps.accounts.urls")),
    path("api/organization/", include("apps.accounts.org_urls")),
    path("api/slack", SlackView.as_view()),  # same view as api/slack/ so clients need no trailing slash
    path("api/slack/", include("apps.slack.urls")),
    path("api/", include("apps.github.urls")),
    path("api/", include("apps.tracker.urls")),
]
