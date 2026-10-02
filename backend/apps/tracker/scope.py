"""Organization scoping. Every tracker query starts from one of these helpers.

GitHub stays the source of truth for access: we only ever expose repositories the GitHub App
installation can see (``is_accessible``) and that haven't been switched off (``is_active``).
"""
from django.db.models import Q
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.accounts.models import OrganizationMembership

from .models import PullRequest, Repository


def visible_repositories(org):
    return Repository.objects.filter(organization=org, is_active=True, is_accessible=True)


def org_pull_requests(org):
    """Every PR in the organization's visible repositories."""
    return PullRequest.objects.filter(
        repository__organization=org, repository__is_active=True, repository__is_accessible=True
    )


def authored_by(qs, user):
    """PRs written by ``user``. Matched by GitHub id (stable across renames) with the login as
    fallback for rows that lack an id."""
    q = Q(author_login__iexact=user.github_username or "\0")
    if user.github_user_id:
        q = Q(author_github_id=user.github_user_id) | Q(author_github_id__isnull=True, author_login__iexact=user.github_username)
    return qs.filter(q)


def not_authored_by(qs, user):
    return qs.exclude(pk__in=authored_by(qs, user).values("pk"))


def target_user(request):
    """Whose data a request is about: the signed-in user, or - admins only - the member picked with
    ``?member=<user id>``. Members asking for anyone but themselves are refused."""
    if hasattr(request, "_target_user"):
        return request._target_user
    raw = request.query_params.get("member")
    target = request.user
    if raw and raw != str(request.user.id):
        if not request.membership.is_admin:
            raise PermissionDenied("Only organization admins can view other members' data.")
        membership = (
            OrganizationMembership.objects.filter(
                organization=request.org, status=OrganizationMembership.STATUS_ACTIVE, user_id=raw if raw.isdigit() else 0
            )
            .select_related("user")
            .first()
        )
        if membership is None:
            raise NotFound("That person is not an active member of this organization.")
        target = membership.user
    request._target_user = target
    return target


def scoped_pull_requests(request):
    """The PRs a request may see: the user's own (default), a chosen member's (``?member=``, admins),
    or the whole organization (``?scope=all``, admins)."""
    qs = org_pull_requests(request.org)
    if request.query_params.get("member"):
        return authored_by(qs, target_user(request))
    if request.query_params.get("scope") == "all":
        if not request.membership.is_admin:
            raise PermissionDenied("Only organization admins can view everyone's pull requests.")
        return qs
    return authored_by(qs, request.user)
