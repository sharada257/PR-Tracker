from django.db.models import Count, F, Q
from django.db.models.functions import ExtractYear
from django.shortcuts import get_object_or_404
from rest_framework import status as http
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrganizationMembership, audit
from apps.accounts.permissions import HasOrganization, IsOrgAdmin
from apps.github.tasks import start_import

from .filters import apply_filters, parse_params
from .overview import build_overview
from .reviews import build_my_reviews
from .models import PullRequest, PullRequestLabel, PullRequestReview, PullRequestReviewer, Repository
from .scope import org_pull_requests, scoped_pull_requests, target_user, visible_repositories
from .serializers import PullRequestDetailSerializer, PullRequestListSerializer, RepositorySerializer
from .statistics import build_reviewers, build_statistics
from .timeline import build_timeline

ORDERING = {
    "created_at": "created_at_github",
    "updated_at": "updated_at_github",
    "merged_at": "merged_at",
    "closed_at": "closed_at",
    "number": "number",
    "title": "title",
    "status": "status",
    "repository": "repository__full_name",
    "review_count": "review_count",
    "review_cycles": "review_cycles",
}


class PullRequestViewSet(viewsets.ReadOnlyModelViewSet):
    """GET /api/prs and GET /api/prs/:id"""

    def get_serializer_class(self):
        return PullRequestDetailSerializer if self.action == "retrieve" else PullRequestListSerializer

    def get_queryset(self):
        if self.action == "retrieve":  # any PR in the organization can be opened, whatever the list scope
            qs = org_pull_requests(self.request.org).select_related("repository")
            return qs.prefetch_related("labels", "reviewers", "reviews", "events")
        qs = scoped_pull_requests(self.request).select_related("repository")
        qs = qs.prefetch_related("labels", "reviewers", "reviews")
        qs = apply_filters(qs, parse_params(self.request.query_params))
        ordering = self.request.query_params.get("ordering", "-created_at")
        field = ORDERING.get(ordering.lstrip("-"), "created_at_github")
        expression = F(field).desc(nulls_last=True) if ordering.startswith("-") else F(field).asc(nulls_last=True)
        return qs.order_by(expression, "-id")


class TimelineView(APIView):
    """GET /api/timeline/:pr_id"""

    def get(self, request, pr_id):
        pr = get_object_or_404(
            org_pull_requests(request.org).prefetch_related("reviews", "reviewers", "events"), pk=pr_id
        )
        return Response(build_timeline(pr))


class StatisticsView(APIView):
    """GET /api/statistics"""

    def get(self, request):
        params = parse_params(request.query_params)
        return Response(build_statistics(scoped_pull_requests(request), params, user=target_user(request), org=request.org))


class ReviewersView(APIView):
    """GET /api/reviewers"""

    def get(self, request):
        params = parse_params(request.query_params)
        return Response(build_reviewers(scoped_pull_requests(request), params))


class MyReviewsView(APIView):
    """GET /api/my-reviews - other people's PRs the user was asked to review, and their review activity."""

    def get(self, request):
        return Response(build_my_reviews(target_user(request), request.org, parse_params(request.query_params)))


class OrganizationOverviewView(APIView):
    """GET /api/organization/overview (admins) - per-member PR and review activity."""

    permission_classes = [IsOrgAdmin]

    def get(self, request):
        return Response(build_overview(request.org, parse_params(request.query_params)))


class LabelsView(APIView):
    def get(self, request):
        ids = scoped_pull_requests(request).values("id")
        rows = (
            PullRequestLabel.objects.filter(pull_request__in=ids)
            .values("name")
            .annotate(count=Count("pull_request", distinct=True))
            .order_by("-count", "name")
        )
        return Response(list(rows))


class FilterOptionsView(APIView):
    """GET /api/filters - values to populate the filter dropdowns."""

    def get(self, request):
        prs = scoped_pull_requests(request)
        years = (
            prs.annotate(y=ExtractYear("created_at_github")).values_list("y", flat=True).distinct().order_by("-y")
        )
        repos = visible_repositories(request.org)
        is_admin = request.membership.is_admin
        authors = org_pull_requests(request.org).values_list("author_login", flat=True).distinct() if is_admin else []
        members = (
            OrganizationMembership.objects.filter(organization=request.org, status=OrganizationMembership.STATUS_ACTIVE)
            .select_related("user")
            .order_by("user__github_username")
            if is_admin
            else []
        )
        reviewers = (
            PullRequestReviewer.objects.filter(pull_request__in=prs.values("id"))
            .values_list("username", flat=True)
            .distinct()
        )
        reviewed = (
            PullRequestReview.objects.filter(pull_request__in=prs.values("id"))
            .values_list("reviewer", flat=True)
            .distinct()
        )
        names = {}
        for name in list(reviewers) + list(reviewed):
            names.setdefault(name.lower(), name)
        labels = (
            PullRequestLabel.objects.filter(pull_request__in=prs.values("id"))
            .values_list("name", flat=True)
            .distinct()
            .order_by("name")
        )
        return Response(
            {
                "years": list(years),
                "repositories": [{"id": r.id, "full_name": r.full_name} for r in repos],
                "reviewers": sorted(names.values(), key=str.lower),
                "labels": list(labels),
                "authors": sorted({a for a in authors if a}, key=str.lower),
                "members": [
                    {"id": m.user_id, "github_username": m.user.github_username, "display_name": m.user.display_name}
                    for m in members
                ],
                "statuses": [{"value": v, "label": l} for v, l in PullRequest.STATUS_CHOICES],
            }
        )


class RepositoryViewSet(viewsets.ModelViewSet):
    """GET /api/repositories, PATCH /api/repositories/:id, POST /api/repositories/select"""

    serializer_class = RepositorySerializer
    http_method_names = ["get", "patch", "post", "head", "options"]

    def get_permissions(self):
        # Everyone in the organization can see the repositories; only admins decide what is tracked.
        admin_only = self.action in ("partial_update", "select")
        return [IsOrgAdmin() if admin_only else HasOrganization()]

    def get_queryset(self):
        qs = Repository.objects.filter(organization=self.request.org)
        params = self.request.query_params
        if params.get("active") in ("true", "false"):
            qs = qs.filter(is_active=params["active"] == "true")
        if params.get("accessible", "true") == "true":
            qs = qs.filter(is_accessible=True)
        if params.get("search"):
            qs = qs.filter(full_name__icontains=params["search"])
        qs = qs.annotate(
            pr_count=Count("pull_requests"),
            merged_count=Count("pull_requests", filter=Q(pull_requests__status=PullRequest.STATUS_MERGED)),
            open_count=Count("pull_requests", filter=Q(pull_requests__state=PullRequest.STATE_OPEN)),
        )
        return qs.order_by("-is_active", "-pr_count", "full_name")

    def partial_update(self, request, *args, **kwargs):
        repo = self.get_object()
        if "is_active" in request.data:
            repo.is_active = bool(request.data["is_active"])
            repo.save(update_fields=["is_active", "updated_at"])
            audit("repository.toggled", user=request.user, request=request, repository=repo.full_name,
                  is_active=repo.is_active)
        return Response(self.get_serializer(self.get_queryset().get(pk=repo.pk)).data)

    @action(detail=False, methods=["post"], url_path="select")
    def select(self, request):
        """Choose what to track: ``{"mode": "all" | "selected", "repository_ids": [...]}``."""
        mode = request.data.get("mode", "selected")
        available = Repository.objects.filter(organization=request.org, is_accessible=True)
        if mode == "all":
            chosen = available
        else:
            ids = request.data.get("repository_ids") or []
            if not isinstance(ids, list):
                return Response({"detail": "repository_ids must be a list."}, status=http.HTTP_400_BAD_REQUEST)
            chosen = available.filter(pk__in=ids)
        chosen_ids = list(chosen.values_list("id", flat=True))
        Repository.objects.filter(organization=request.org).exclude(pk__in=chosen_ids).update(is_active=False)
        chosen.update(is_active=True)

        started = []
        if request.data.get("start_import", True):
            for repo in Repository.objects.filter(pk__in=chosen_ids).select_related("organization"):
                if start_import(repo):
                    started.append(repo.full_name)
        audit("repositories.selected", user=request.user, request=request, mode=mode, count=len(chosen_ids),
              import_started=len(started))
        return Response({"active": len(chosen_ids), "import_started": len(started)}, status=http.HTTP_202_ACCEPTED)
