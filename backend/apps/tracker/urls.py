from django.urls import include, path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter(trailing_slash=False)
router.register("prs", views.PullRequestViewSet, basename="pr")
router.register("repositories", views.RepositoryViewSet, basename="repository")

urlpatterns = [
    path("", include(router.urls)),
    path("timeline/<int:pr_id>", views.TimelineView.as_view(), name="timeline"),
    path("statistics", views.StatisticsView.as_view(), name="statistics"),
    path("reviewers", views.ReviewersView.as_view(), name="reviewers"),
    path("my-reviews", views.MyReviewsView.as_view(), name="my-reviews"),
    path("organization/overview", views.OrganizationOverviewView.as_view(), name="org-overview"),
    path("labels", views.LabelsView.as_view(), name="labels"),
    path("filters", views.FilterOptionsView.as_view(), name="filter-options"),
]
