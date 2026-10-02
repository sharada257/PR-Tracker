from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AccessRequest, AuditLog, Organization, OrganizationMembership, User


@admin.register(User)
class PRUserAdmin(UserAdmin):
    list_display = ("github_username", "display_name", "github_user_id", "last_login")
    search_fields = ("github_username", "display_name")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "github_organization_login", "account_type", "created_at")
    search_fields = ("name", "github_organization_login")


@admin.register(OrganizationMembership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "role", "status")
    list_filter = ("role", "status")


@admin.register(AccessRequest)
class AccessRequestAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "organization", "status", "decided_by")
    list_filter = ("status",)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "action", "user", "organization")
    list_filter = ("action",)
