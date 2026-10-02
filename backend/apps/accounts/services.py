"""Sign-in: turning "who GitHub says you are" into users, organizations and memberships."""
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.github import sync as gh_sync
from apps.github.models import SyncJob

from .models import Organization, OrganizationMembership, User


def upsert_user(gh_user):
    user, created = User.objects.get_or_create(
        github_user_id=gh_user["id"], defaults={"username": "gh_%s" % gh_user["id"]}
    )
    user.github_username = gh_user["login"]
    user.display_name = gh_user.get("name") or gh_user["login"]
    user.avatar_url = gh_user.get("avatar_url") or ""
    user.email = gh_user.get("email") or user.email
    if created:
        user.set_unusable_password()
    user.last_login = timezone.now()
    user.save()
    return user, created


def _parse(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def sync_memberships(user, installations, installed=None):
    """Make the user's memberships match the GitHub App installations GitHub says they can access.

    ``installations`` is the response of ``GET /user/installations``. Returns the organizations
    that were seen for the first time and still need an initial sync. ``installed`` is the installation id
    GitHub just sent the user back from after installing the App (so they completed the setup).
    """
    needs_sync, seen = [], []
    for item in installations:
        account = item.get("account") or {}
        if not account.get("id"):
            continue
        with transaction.atomic():
            organization, installation_record = gh_sync.upsert_organization(
                account,
                item["id"],
                item.get("repository_selection") or "",
                suspended_at=_parse(item.get("suspended_at")),
                installed_by=user.github_user_id if installed and str(installed) == str(item["id"]) else None,
            )
            organization = Organization.objects.select_for_update().get(pk=organization.pk)
            membership = OrganizationMembership.objects.filter(organization=organization, user=user).first()
            if membership is None:
                # The person who installed the App administers the organization. If nobody recorded who
                # installed it, the first person to sign in does. Everyone else is a member.
                installer = installation_record.installed_by_github_id
                may_admin = not installer or installer == user.github_user_id
                has_admin = organization.memberships.filter(
                    role=OrganizationMembership.ROLE_ADMIN, status=OrganizationMembership.STATUS_ACTIVE
                ).exists()
                OrganizationMembership.objects.create(
                    organization=organization,
                    user=user,
                    role=OrganizationMembership.ROLE_ADMIN if may_admin and not has_admin else OrganizationMembership.ROLE_MEMBER,
                )
            elif membership.status != OrganizationMembership.STATUS_ACTIVE and not membership.access_blocked:
                membership.status = OrganizationMembership.STATUS_ACTIVE
                membership.save(update_fields=["status", "updated_at"])
        seen.append(organization.pk)
        if not SyncJob.objects.filter(organization=organization).exists():
            needs_sync.append(organization)
    # GitHub no longer shows the user this installation: they lost access.
    OrganizationMembership.objects.filter(user=user, status=OrganizationMembership.STATUS_ACTIVE).exclude(
        organization_id__in=seen
    ).update(status=OrganizationMembership.STATUS_REVOKED)
    return needs_sync
