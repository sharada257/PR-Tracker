import logging
import secrets

from django.conf import settings
from django.contrib.auth import login, logout
from django.http import HttpResponseRedirect
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.github import auth as gh_auth
from apps.github.client import GitHubClient, GitHubError
from apps.github.models import SyncJob
from apps.github.tasks import start_organization_sync

from .models import AccessRequest, OrganizationMembership, User, audit
from .permissions import SESSION_ORG_KEY, active_memberships, resolve_membership
from .services import sync_memberships, upsert_user

logger = logging.getLogger(__name__)

STATE_KEY = "github_oauth_state"
INSTALL_KEY = "github_pending_installation"


def _redirect_uri():
    return settings.PUBLIC_URL + "/api/auth/github/callback"


def _frontend(path="/", **params):
    from urllib.parse import urlencode

    query = ("?" + urlencode(params)) if params else ""
    return HttpResponseRedirect(settings.PUBLIC_URL + path + query)


def serialize_user(user):
    return {
        "id": user.id,
        "github_username": user.github_username,
        "display_name": user.display_name or user.github_username,
        "avatar_url": user.avatar_url,
    }


def serialize_organization(membership):
    org = membership.organization
    return {
        "id": org.id,
        "name": org.name,
        "login": org.github_organization_login,
        "avatar_url": org.avatar_url,
        "role": membership.role,
    }


class AuthConfigView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response(
            {
                "github_oauth": gh_auth.oauth_configured(),
                "github_app": gh_auth.app_configured(),
                "install_url": gh_auth.install_url(),
                "demo_login": settings.ALLOW_DEMO_LOGIN,
            }
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class MeView(APIView):
    """Session probe. Always 200 so the SPA can branch without error noise."""

    permission_classes = [AllowAny]

    def get(self, request):
        if not request.user.is_authenticated:
            return Response({"authenticated": False})
        membership = resolve_membership(request)
        return Response(
            {
                "authenticated": True,
                "user": serialize_user(request.user),
                # ``has_access`` False -> the SPA shows "PRLens does not currently have access ...".
                "has_access": membership is not None,
                "organization": serialize_organization(membership) if membership else None,
                "organizations": [serialize_organization(m) for m in active_memberships(request.user)],
                # More than one organization and none picked yet: the SPA shows the chooser.
                "blocked_organizations": blocked_organizations(request.user) if membership is None else [],
                "needs_organization_choice": membership is not None and not request.org_chosen,
                "install_url": gh_auth.install_url(),
            }
        )


class GitHubLoginView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        if not gh_auth.oauth_configured():
            return _frontend("/login", error="oauth_not_configured")
        state = secrets.token_urlsafe(24)
        request.session[STATE_KEY] = state
        return HttpResponseRedirect(gh_auth.authorize_url(_redirect_uri(), state))


def blocked_organizations(user):
    """Organizations whose admin removed this person, with the state of their request to come back."""
    memberships = OrganizationMembership.objects.filter(
        user=user,
        status=OrganizationMembership.STATUS_REVOKED,
        access_blocked=True,
        organization__installation__active=True,
        organization__installation__suspended_at__isnull=True,
    ).select_related("organization")
    result = []
    for membership in memberships:
        org = membership.organization
        latest = AccessRequest.objects.filter(organization=org, user=user).first()
        result.append(
            {
                "id": org.id,
                "name": org.name,
                "login": org.github_organization_login,
                "avatar_url": org.avatar_url,
                "request": {
                    "status": latest.status,
                    "message": latest.message,
                    "created_at": latest.created_at,
                    "decided_at": latest.decided_at,
                }
                if latest
                else None,
            }
        )
    return result


class AccessRequestView(APIView):
    """POST /api/auth/access-request ``{"organization_id", "message"?}`` - ask the admins to let you back in.

    Only available to people an admin removed. A person has at most one open request per organization."""

    permission_classes = []

    def post(self, request):
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        membership = (
            OrganizationMembership.objects.filter(
                user=request.user,
                organization_id=request.data.get("organization_id"),
                status=OrganizationMembership.STATUS_REVOKED,
                access_blocked=True,
            )
            .select_related("organization")
            .first()
        )
        if membership is None:
            return Response({"detail": "There is nothing to request access to."}, status=status.HTTP_404_NOT_FOUND)
        message = str(request.data.get("message") or "").strip()[:500]
        access_request, created = AccessRequest.objects.get_or_create(
            organization=membership.organization, user=request.user, status=AccessRequest.PENDING,
            defaults={"message": message},
        )
        if created:
            audit("access.requested", user=request.user, request=request, organization=membership.organization)
        return Response(
            {"status": access_request.status, "created_at": access_request.created_at},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class GitHubInstallView(APIView):
    """Send the user to GitHub to install the App on an organization ("Set up PRLens").

    GitHub returns the ``state`` we pass here, so the way back is protected like a normal sign-in. GitHub
    decides who may install; PRLens only learns the outcome from the installation / webhook."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        url = gh_auth.install_url()
        if not url or not gh_auth.oauth_configured():
            return _frontend("/login", error="oauth_not_configured")
        state = secrets.token_urlsafe(24)
        request.session[STATE_KEY] = state
        return HttpResponseRedirect("%s?state=%s" % (url, state))


class GitHubCallbackView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        expected = request.session.pop(STATE_KEY, None)
        state, code = request.GET.get("state"), request.GET.get("code")
        # Returning from installing the App: remember which installation the user just completed.
        installed = request.GET.get("installation_id") if request.GET.get("setup_action") == "install" else None
        if installed and not code:
            # GitHub is not set up to authorise users during installation: sign them in now, then finish.
            request.session[INSTALL_KEY] = installed
            return HttpResponseRedirect("/api/auth/github/login")
        installed = installed or request.session.pop(INSTALL_KEY, None)
        if request.GET.get("error") or not code:
            return _frontend("/login", error="access_denied")
        if not expected or not secrets.compare_digest(expected, state or ""):
            audit("auth.state_mismatch", request=request)
            return _frontend("/login", error="invalid_state")
        try:
            # The user's token is used for these two calls only and is never stored.
            token = gh_auth.exchange_code(code, _redirect_uri())
            client = GitHubClient(token=token)
            gh_user = client.get_authenticated_user()
            installations = client.list_user_installations()
        except GitHubError as exc:
            logger.warning("GitHub sign-in failed: %s", exc)
            audit("auth.login_failed", request=request, reason=str(exc)[:200])
            return _frontend("/login", error="github_error")
        user, created = upsert_user(gh_user)
        needs_sync = sync_memberships(user, installations, installed=installed)
        login(request._request, user)
        request.session.pop(SESSION_ORG_KEY, None)
        audit("auth.login", user=user, request=request, method="oauth", new_user=created)
        for organization in needs_sync:
            start_organization_sync(organization, trigger=SyncJob.TRIGGER_INSTALL)
        return _frontend("/")


# Seeded demo accounts: an organization admin and an ordinary developer (a plain member).
DEMO_USERS = {"admin": 9_000_001, "member": 9_100_002}


class DemoLoginView(APIView):
    """Sign in as a seeded demo account (``role``: admin or member). Disabled unless ALLOW_DEMO_LOGIN is set."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        github_id = DEMO_USERS.get(request.data.get("role") or "admin")
        user = User.objects.filter(github_user_id=github_id).first() if settings.ALLOW_DEMO_LOGIN and github_id else None
        if user is None:
            return Response({"detail": "Demo mode is not available."}, status=status.HTTP_404_NOT_FOUND)
        if (request.data.get("role") or "admin") == "admin":
            # The Admin demo button always works, even if the demo admin demoted or removed themselves.
            OrganizationMembership.objects.filter(user=user).exclude(
                role=OrganizationMembership.ROLE_ADMIN, status=OrganizationMembership.STATUS_ACTIVE, access_blocked=False
            ).update(role=OrganizationMembership.ROLE_ADMIN, status=OrganizationMembership.STATUS_ACTIVE, access_blocked=False)
        login(request._request, user)
        request.session.pop(SESSION_ORG_KEY, None)
        audit("auth.login", user=user, request=request, method="demo", role=request.data.get("role") or "admin")
        return Response({"user": serialize_user(user)})


class SwitchOrganizationView(APIView):
    permission_classes = []  # any signed-in user; the target must be one of their active memberships

    def post(self, request):
        if not request.user.is_authenticated:
            return Response(status=status.HTTP_401_UNAUTHORIZED)
        target = active_memberships(request.user).filter(organization_id=request.data.get("organization_id")).first()
        if target is None:
            return Response({"detail": "You are not a member of that organization."}, status=status.HTTP_404_NOT_FOUND)
        request.session[SESSION_ORG_KEY] = target.organization_id
        return Response(serialize_organization(target))


class LogoutView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        if request.user.is_authenticated:
            audit("auth.logout", user=request.user, request=request)
        logout(request._request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuditLogView(APIView):
    def get(self, request):
        entries = request.user.audit_logs.all()[:50]
        return Response(
            [{"id": e.id, "action": e.action, "detail": e.detail, "created_at": e.created_at} for e in entries]
        )
