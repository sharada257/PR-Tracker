from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """A person who signed in with GitHub.

    ``username`` is a stable synthetic value (``gh_<id>``) so that GitHub
    username changes never break the account; the current login lives in
    ``github_username``. No GitHub token is ever stored: the sign-in token is used once
    to learn who the user is and which installations they can see, then discarded.
    """

    github_user_id = models.BigIntegerField(unique=True, null=True)
    github_username = models.CharField(max_length=100, db_index=True)
    display_name = models.CharField(max_length=255, blank=True)
    avatar_url = models.URLField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.github_username or self.username


class Organization(models.Model):
    """A GitHub account (normally an organization) where the GitHub App is installed.

    Everything we import (repositories, pull requests, reviews) belongs to an organization, and
    every query is scoped to it. GitHub remains the authority for who can see which repository: we
    only ever expose what the installation can access.
    """

    name = models.CharField(max_length=255)
    github_organization_id = models.BigIntegerField(unique=True)
    github_organization_login = models.CharField(max_length=100, db_index=True)
    account_type = models.CharField(max_length=20, blank=True)  # "Organization" or "User"
    avatar_url = models.URLField(max_length=500, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name or self.github_organization_login


class OrganizationMembership(models.Model):
    """A user's PR Tracker role in an organization. Repository access is deliberately *not* modelled
    here - GitHub (through the installation) is the only source of truth for that."""

    ROLE_ADMIN = "ADMIN"
    ROLE_MEMBER = "MEMBER"
    ROLE_CHOICES = [(ROLE_ADMIN, "Admin"), (ROLE_MEMBER, "Member")]

    STATUS_ACTIVE = "ACTIVE"
    STATUS_REVOKED = "REVOKED"
    STATUS_CHOICES = [(STATUS_ACTIVE, "Active"), (STATUS_REVOKED, "Revoked")]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_ACTIVE, db_index=True)
    # An admin removed this person. Signing in again must not bring them back, even though GitHub
    # still shows them the installation. (Losing access on GitHub revokes without this flag, and
    # regaining it restores the membership automatically.)
    access_blocked = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.UniqueConstraint(fields=["organization", "user"], name="uniq_membership_per_org_user"),
        ]

    @property
    def is_admin(self):
        return self.role == self.ROLE_ADMIN and self.status == self.STATUS_ACTIVE

    def __str__(self):
        return "%s @ %s (%s)" % (self.user, self.organization, self.role)


class AccessRequest(models.Model):
    """Someone an admin removed asks to come back. Admins approve or deny it in the Requests page."""

    PENDING, APPROVED, DENIED = "PENDING", "APPROVED", "DENIED"
    STATUS_CHOICES = [(PENDING, "Pending"), (APPROVED, "Approved"), (DENIED, "Denied")]

    organization = models.ForeignKey(Organization, on_delete=models.CASCADE, related_name="access_requests")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="access_requests")
    message = models.CharField(max_length=500, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING, db_index=True)
    decided_by = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="decided_access_requests"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            # One open request per person and organization.
            models.UniqueConstraint(
                fields=["organization", "user"], condition=models.Q(status="PENDING"), name="uniq_pending_access_request"
            ),
        ]

    def __str__(self):
        return "%s -> %s (%s)" % (self.user, self.organization, self.status)


class AuditLog(models.Model):
    """Trail of important authentication / configuration events."""

    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs")
    organization = models.ForeignKey(
        Organization, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    action = models.CharField(max_length=64, db_index=True)
    detail = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return "%s by %s" % (self.action, self.user_id)


def audit(action, user=None, request=None, organization=None, **detail):
    ip = None
    if request is not None:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        ip = (forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")) or None
        organization = organization or getattr(request, "org", None)
    kwargs = {"user": user, "organization": organization, "action": action, "detail": detail}
    try:
        return AuditLog.objects.create(ip_address=ip, **kwargs)
    except Exception:  # auditing must never break the request (e.g. malformed IP)
        return AuditLog.objects.create(**kwargs)
