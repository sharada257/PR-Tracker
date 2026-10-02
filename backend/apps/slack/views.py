"""Admin-only: connect a Slack workspace, choose what is sent, and map people to Slack accounts."""
import logging
import secrets

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponseRedirect
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrganizationMembership, audit
from apps.accounts.permissions import IsOrgAdmin, resolve_membership

from . import client as slack
from . import crypto, service
from .models import SlackIntegration, SlackUserLink

logger = logging.getLogger(__name__)
STATE_KEY = "slack_oauth_state"
USERS_TTL = 300


def _frontend(**params):
    from urllib.parse import urlencode

    query = ("?" + urlencode(params)) if params else ""
    return HttpResponseRedirect(settings.PUBLIC_URL + "/organization/slack" + query)


def _integration(org):
    return SlackIntegration.objects.filter(organization=org).first()


def serialize(org, integration):
    links = SlackUserLink.objects.filter(organization=org).count()
    members = OrganizationMembership.objects.filter(organization=org, status=OrganizationMembership.STATUS_ACTIVE).count()
    data = {"configured": slack.configured(), "redirect_uri": slack.redirect_uri(), "connected": integration is not None,
            "linked_count": links, "member_count": members}
    if integration:
        data.update(
            active=integration.active,
            last_error=integration.last_error,
            team_name=integration.team_name,
            connected_at=integration.connected_at,
            settings={
                "notify_review_requests": integration.notify_review_requests,
                "notify_review_outcomes": integration.notify_review_outcomes,
                "send_reminders": integration.send_reminders,
                "reminder_after_hours": integration.reminder_after_hours,
                "max_reminders": integration.max_reminders,
                "business_hours_only": integration.business_hours_only,
            },
        )
    return data


def _slack_users(integration, refresh=False):
    key = "slack-users:%s" % integration.organization_id
    users = None if refresh else cache.get(key)
    if users is None:
        users = service.client_for(integration).users()
        cache.set(key, users, USERS_TTL)
    return users


class SlackView(APIView):
    """GET status + settings · PATCH settings · DELETE disconnect"""

    permission_classes = [IsOrgAdmin]

    def get(self, request):
        return Response(serialize(request.org, _integration(request.org)))

    def patch(self, request):
        integration = _integration(request.org)
        if integration is None:
            return Response({"detail": "Slack is not connected."}, status=status.HTTP_404_NOT_FOUND)
        data = request.data
        for field in ("notify_review_requests", "notify_review_outcomes", "send_reminders", "business_hours_only"):
            if field in data:
                if not isinstance(data[field], bool):
                    return Response({"detail": "%s must be true or false." % field}, status=status.HTTP_400_BAD_REQUEST)
                setattr(integration, field, data[field])
        for field, low, high in (("reminder_after_hours", 1, 168), ("max_reminders", 0, 5)):
            if field in data:
                value = data[field]
                if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                    return Response({"detail": "%s must be between %d and %d." % (field, low, high)}, status=status.HTTP_400_BAD_REQUEST)
                setattr(integration, field, value)
        integration.save()
        audit("slack.settings_changed", user=request.user, request=request)
        return Response(serialize(request.org, integration))

    def delete(self, request):
        integration = _integration(request.org)
        if integration is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        try:
            service.client_for(integration).revoke()
        except Exception:  # noqa: BLE001 - disconnecting must work even if Slack is unreachable
            logger.warning("Could not revoke the Slack token for %s", request.org)
        integration.delete()
        SlackUserLink.objects.filter(organization=request.org).delete()
        cache.delete("slack-users:%s" % request.org.id)
        audit("slack.disconnected", user=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConnectView(APIView):
    """GET /api/slack/connect - send the admin to Slack's "Add to Slack" screen."""

    permission_classes = [IsOrgAdmin]

    def get(self, request):
        if not slack.configured():
            return _frontend(error="not_configured")
        state = secrets.token_urlsafe(24)
        request.session[STATE_KEY] = {"state": state, "org": request.org.id}
        return HttpResponseRedirect(slack.authorize_url(state))


class CallbackView(APIView):
    """GET /api/slack/callback - Slack sends the admin back here with a code."""

    permission_classes = []

    def get(self, request):
        pending = request.session.pop(STATE_KEY, None)
        membership = resolve_membership(request)
        if request.GET.get("error"):
            return _frontend(error="cancelled")
        if (
            not pending
            or not secrets.compare_digest(pending["state"], request.GET.get("state") or "")
            or membership is None
            or not membership.is_admin
            or membership.organization_id != pending["org"]
        ):
            return _frontend(error="invalid_state")
        try:
            grant = slack.exchange_code(request.GET.get("code", ""))
        except slack.SlackError as exc:
            logger.warning("Slack OAuth failed: %s", exc.code)
            return _frontend(error="slack_error")
        team = grant.get("team") or {}
        integration, _ = SlackIntegration.objects.update_or_create(
            organization=request.org,
            defaults={
                "team_id": team.get("id", ""),
                "team_name": team.get("name", ""),
                "bot_user_id": grant.get("bot_user_id", ""),
                "bot_token_encrypted": crypto.encrypt(grant["access_token"]),
                "connected_by": request.user,
                "active": True,
                "last_error": "",
            },
        )
        # Only requests made from now on trigger messages: reconnecting must not replay the past.
        from django.utils import timezone

        SlackIntegration.objects.filter(pk=integration.pk).update(connected_at=timezone.now())
        try:
            service.auto_match(integration, _slack_users(integration, refresh=True))
        except slack.SlackError:
            logger.warning("Could not read the Slack user list for %s", request.org)
        audit("slack.connected", user=request.user, request=request, team=team.get("name", ""))
        return _frontend(connected=1)


class LinksView(APIView):
    """GET /api/slack/links - every active member, who they are on Slack, and the workspace's people."""

    permission_classes = [IsOrgAdmin]

    def get(self, request):
        integration = _integration(request.org)
        if integration is None:
            return Response({"detail": "Slack is not connected."}, status=status.HTTP_404_NOT_FOUND)
        try:
            users = _slack_users(integration, refresh=request.GET.get("refresh") == "1")
        except slack.SlackError as exc:
            return Response({"detail": "Slack said: %s" % exc.code}, status=status.HTTP_502_BAD_GATEWAY)
        links = {l.user_id: l for l in SlackUserLink.objects.filter(organization=request.org)}
        memberships = (
            OrganizationMembership.objects.filter(organization=request.org, status=OrganizationMembership.STATUS_ACTIVE)
            .select_related("user")
            .order_by("user__github_username")
        )
        members = []
        for m in memberships:
            link = links.get(m.user_id)
            members.append(
                {
                    "id": m.user_id,
                    "github_username": m.user.github_username,
                    "display_name": m.user.display_name or m.user.github_username,
                    "avatar_url": m.user.avatar_url,
                    "slack_user_id": link.slack_user_id if link else None,
                    "source": link.source if link else None,
                }
            )
        return Response({"members": members, "slack_users": users})


class LinkDetailView(APIView):
    """PUT /api/slack/links/:user_id ``{"slack_user_id": "U123" | null}``"""

    permission_classes = [IsOrgAdmin]

    def put(self, request, user_id):
        integration = _integration(request.org)
        if integration is None:
            return Response({"detail": "Slack is not connected."}, status=status.HTTP_404_NOT_FOUND)
        membership = OrganizationMembership.objects.filter(organization=request.org, user_id=user_id).first()
        if membership is None:
            return Response({"detail": "No such member."}, status=status.HTTP_404_NOT_FOUND)
        slack_user_id = request.data.get("slack_user_id")
        if not slack_user_id:
            SlackUserLink.objects.filter(organization=request.org, user_id=user_id).delete()
            return Response({"slack_user_id": None, "source": None})
        if slack_user_id not in {u["id"] for u in _slack_users(integration)}:
            return Response({"detail": "That person is not in the Slack workspace."}, status=status.HTTP_400_BAD_REQUEST)
        if SlackUserLink.objects.filter(organization=request.org, slack_user_id=slack_user_id).exclude(user_id=user_id).exists():
            return Response({"detail": "That Slack account is already linked to someone else."}, status=status.HTTP_409_CONFLICT)
        link, _ = SlackUserLink.objects.update_or_create(
            organization=request.org, user_id=user_id,
            defaults={"slack_user_id": slack_user_id, "source": SlackUserLink.MANUAL},
        )
        return Response({"slack_user_id": link.slack_user_id, "source": link.source})


class AutoMatchView(APIView):
    """POST /api/slack/auto-match - link members by email / name; manual links stay."""

    permission_classes = [IsOrgAdmin]

    def post(self, request):
        integration = _integration(request.org)
        if integration is None:
            return Response({"detail": "Slack is not connected."}, status=status.HTTP_404_NOT_FOUND)
        try:
            linked = service.auto_match(integration, _slack_users(integration, refresh=True))
        except slack.SlackError as exc:
            return Response({"detail": "Slack said: %s" % exc.code}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"linked": linked})


class TestMessageView(APIView):
    """POST /api/slack/test - a message to the admin who asks, to prove it works."""

    permission_classes = [IsOrgAdmin]

    def post(self, request):
        integration = _integration(request.org)
        if integration is None:
            return Response({"detail": "Slack is not connected."}, status=status.HTTP_404_NOT_FOUND)
        link = SlackUserLink.objects.filter(organization=request.org, user=request.user).first()
        if link is None:
            return Response({"detail": "Link your own Slack account in the table first, then try again."},
                            status=status.HTTP_409_CONFLICT)
        text, blocks = service.test_message(request.org)
        try:
            service.client_for(integration).post(link.slack_user_id, text, blocks)
        except slack.SlackError as exc:
            if exc.is_auth_error:
                SlackIntegration.objects.filter(pk=integration.pk).update(active=False, last_error=exc.code)
            return Response({"detail": "Slack said: %s" % exc.code}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({"sent": True})
