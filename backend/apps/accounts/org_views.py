"""Admin-only organization management: who is in the organization and what role they have."""
from django.db import transaction
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from django.utils import timezone

from .models import AccessRequest, OrganizationMembership, audit
from .permissions import IsOrgAdmin


def serialize_member(membership):
    user = membership.user
    return {
        "id": user.id,
        "github_username": user.github_username,
        "display_name": user.display_name or user.github_username,
        "avatar_url": user.avatar_url,
        "role": membership.role,
        "status": membership.status,
        "access_blocked": membership.access_blocked,
        "last_login": user.last_login,
        "joined_at": membership.created_at,
    }


def active_admin_count(org):
    return OrganizationMembership.objects.filter(
        organization=org, role=OrganizationMembership.ROLE_ADMIN, status=OrganizationMembership.STATUS_ACTIVE
    ).count()


class MembersView(APIView):
    """GET /api/organization/members"""

    permission_classes = [IsOrgAdmin]

    def get(self, request):
        memberships = (
            OrganizationMembership.objects.filter(organization=request.org)
            .select_related("user")
            .order_by("user__github_username")
        )
        rows = [serialize_member(m) for m in memberships]
        rows.sort(key=lambda r: (r["status"] != OrganizationMembership.STATUS_ACTIVE, r["role"] != "ADMIN"))
        return Response(rows)


class MemberDetailView(APIView):
    """PATCH /api/organization/members/:user_id  ``{"role": "ADMIN"|"MEMBER"}`` and/or ``{"active": bool}``.

    An organization always keeps at least one active admin, and nobody can revoke themselves."""

    permission_classes = [IsOrgAdmin]

    def patch(self, request, user_id):
        with transaction.atomic():
            membership = (
                OrganizationMembership.objects.select_for_update()
                .select_related("user")
                .filter(organization=request.org, user_id=user_id)
                .first()
            )
            if membership is None:
                return Response({"detail": "No such member."}, status=status.HTTP_404_NOT_FOUND)
            is_self = membership.user_id == request.user.id
            role = request.data.get("role")
            active = request.data.get("active")

            if role is not None:
                if role not in (OrganizationMembership.ROLE_ADMIN, OrganizationMembership.ROLE_MEMBER):
                    return Response({"detail": "Unknown role."}, status=status.HTTP_400_BAD_REQUEST)
                demoting = role == OrganizationMembership.ROLE_MEMBER and membership.role == OrganizationMembership.ROLE_ADMIN
                if demoting and membership.status == OrganizationMembership.STATUS_ACTIVE and active_admin_count(request.org) <= 1:
                    return Response(
                        {"detail": "An organization needs at least one admin. Make someone else an admin first."},
                        status=status.HTTP_409_CONFLICT,
                    )
                if role != membership.role:
                    audit("member.role_changed", user=request.user, request=request,
                          member=membership.user.github_username, role=role)
                membership.role = role

            if active is not None:
                if not active and is_self:
                    return Response({"detail": "You can't revoke your own access."}, status=status.HTTP_409_CONFLICT)
                if not active and membership.role == OrganizationMembership.ROLE_ADMIN and active_admin_count(request.org) <= 1:
                    return Response({"detail": "An organization needs at least one admin."}, status=status.HTTP_409_CONFLICT)
                if bool(active) != (membership.status == OrganizationMembership.STATUS_ACTIVE):
                    audit("member.access_revoked" if not active else "member.access_restored", user=request.user,
                          request=request, member=membership.user.github_username)
                membership.status = OrganizationMembership.STATUS_ACTIVE if active else OrganizationMembership.STATUS_REVOKED
                membership.access_blocked = not active
            membership.save()
            if active:
                # Letting someone back in settles any request they had open.
                AccessRequest.objects.filter(
                    organization=request.org, user_id=user_id, status=AccessRequest.PENDING
                ).update(status=AccessRequest.APPROVED, decided_by=request.user, decided_at=timezone.now())
        return Response(serialize_member(membership))


def serialize_request(access_request):
    user = access_request.user
    return {
        "id": access_request.id,
        "user_id": user.id,
        "github_username": user.github_username,
        "display_name": user.display_name or user.github_username,
        "avatar_url": user.avatar_url,
        "message": access_request.message,
        "status": access_request.status,
        "created_at": access_request.created_at,
        "decided_at": access_request.decided_at,
        "decided_by": access_request.decided_by.github_username if access_request.decided_by else None,
    }


class AccessRequestsView(APIView):
    """GET /api/organization/access-requests - open requests first, then recent decisions."""

    permission_classes = [IsOrgAdmin]

    def get(self, request):
        queryset = AccessRequest.objects.filter(organization=request.org).select_related("user", "decided_by")
        pending = list(queryset.filter(status=AccessRequest.PENDING).order_by("created_at"))
        decided = list(queryset.exclude(status=AccessRequest.PENDING)[:50])
        return Response({"pending_count": len(pending), "results": [serialize_request(r) for r in pending + decided]})


class AccessRequestDetailView(APIView):
    """PATCH /api/organization/access-requests/:id ``{"decision": "approve"|"deny"}``"""

    permission_classes = [IsOrgAdmin]

    def patch(self, request, request_id):
        decision = request.data.get("decision")
        if decision not in ("approve", "deny"):
            return Response({"detail": "decision must be approve or deny."}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            access_request = (
                AccessRequest.objects.select_for_update(of=("self",))
                .select_related("user", "decided_by")
                .filter(organization=request.org, pk=request_id)
                .first()
            )
            if access_request is None:
                return Response({"detail": "No such request."}, status=status.HTTP_404_NOT_FOUND)
            if access_request.status != AccessRequest.PENDING:
                return Response({"detail": "This request was already decided."}, status=status.HTTP_409_CONFLICT)
            if decision == "approve":
                # Back in as a plain member, whatever they were before they were removed.
                OrganizationMembership.objects.filter(organization=request.org, user=access_request.user).update(
                    status=OrganizationMembership.STATUS_ACTIVE,
                    access_blocked=False,
                    role=OrganizationMembership.ROLE_MEMBER,
                )
            access_request.status = AccessRequest.APPROVED if decision == "approve" else AccessRequest.DENIED
            access_request.decided_by = request.user
            access_request.decided_at = timezone.now()
            access_request.save()
            audit("access.approved" if decision == "approve" else "access.denied", user=request.user,
                  request=request, member=access_request.user.github_username)
        return Response(serialize_request(access_request))
