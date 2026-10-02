"""Organization context for every authenticated request.

A user may belong to several organizations. The active one is remembered in the session
(``org_id``); if it is missing or no longer valid we fall back to the first active membership.
"""
from rest_framework.permissions import BasePermission

from .models import OrganizationMembership

SESSION_ORG_KEY = "org_id"
NO_ACCESS_MESSAGE = (
    "PRLens does not currently have access to the repositories available to you. "
    "Please contact your GitHub organization administrator."
)


def active_memberships(user):
    """Memberships that grant access: not revoked, and the organization's GitHub App installation is
    installed and not suspended."""
    return OrganizationMembership.objects.filter(
        user=user,
        status=OrganizationMembership.STATUS_ACTIVE,
        organization__installation__active=True,
        organization__installation__suspended_at__isnull=True,
    ).select_related("organization")


def resolve_membership(request):
    """The membership for the active organization, or ``None``. Cached per request."""
    if hasattr(request, "_membership_cache"):
        return request._membership_cache
    membership = None
    user = request.user
    if user.is_authenticated:
        memberships = list(active_memberships(user))
        wanted = request.session.get(SESSION_ORG_KEY)
        membership = next((m for m in memberships if m.organization_id == wanted), None)
        request.org_chosen = membership is not None or len(memberships) == 1
        if membership is None and memberships:
            membership = memberships[0]
            # With several organizations the user picks on the organization screen; until then use the first.
            if len(memberships) == 1:
                request.session[SESSION_ORG_KEY] = membership.organization_id
    request._membership_cache = membership
    request.membership = membership
    request.org = membership.organization if membership else None
    return membership


class HasOrganization(BasePermission):
    """Signed in *and* a member of an organization PR Tracker is installed in."""

    message = NO_ACCESS_MESSAGE

    def has_permission(self, request, view):
        if not (request.user and request.user.is_authenticated):
            return False
        return resolve_membership(request) is not None


class IsOrgAdmin(HasOrganization):
    message = "Only organization admins can do that."

    def has_permission(self, request, view):
        return super().has_permission(request, view) and request.membership.is_admin
