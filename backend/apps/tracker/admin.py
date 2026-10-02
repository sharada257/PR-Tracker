from django.contrib import admin

from .models import PullRequest, Repository


@admin.register(Repository)
class RepositoryAdmin(admin.ModelAdmin):
    list_display = ("full_name", "organization", "is_active", "is_accessible", "import_status", "last_synced_at")
    list_filter = ("is_active", "is_accessible", "import_status")
    search_fields = ("full_name",)


@admin.register(PullRequest)
class PullRequestAdmin(admin.ModelAdmin):
    list_display = ("number", "title", "repository", "author_login", "status")
    search_fields = ("title", "author_login")
    raw_id_fields = ("repository",)
