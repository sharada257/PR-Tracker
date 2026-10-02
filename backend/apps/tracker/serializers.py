from rest_framework import serializers

from .models import PullRequest, PullRequestReview, Repository
from .timeline import build_timeline

HOURS = 3600.0


def involved_reviewers(pr):
    """People involved in reviewing: requested or actually reviewed (author excluded)."""
    seen, people = set(), []
    author = (pr.author_login or "").lower()

    def add(login, avatar, is_team=False):
        key = login.lower()
        if key and key != author and key not in seen:
            seen.add(key)
            people.append({"username": login, "avatar_url": avatar or "", "is_team": is_team})

    events = [(r.assigned_at or pr.created_at_github, r.username, r.avatar_url, r.is_team) for r in pr.reviewers.all()]
    events += [
        (r.submitted_at or pr.created_at_github, r.reviewer, r.reviewer_avatar_url, False)
        for r in pr.reviews.all()
        if r.state != PullRequestReview.PENDING
    ]
    for _, login, avatar, is_team in sorted(events, key=lambda e: e[0]):
        add(login, avatar, is_team)
    return people


class RepositoryRefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Repository
        fields = ["id", "owner", "name", "full_name", "url"]


class PullRequestListSerializer(serializers.ModelSerializer):
    repository = RepositoryRefSerializer(read_only=True)
    author = serializers.SerializerMethodField()
    reviewers = serializers.SerializerMethodField()
    labels = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    created_at = serializers.DateTimeField(source="created_at_github", read_only=True)
    updated_at = serializers.DateTimeField(source="updated_at_github", read_only=True)
    time_to_first_review_hours = serializers.SerializerMethodField()
    time_to_merge_hours = serializers.SerializerMethodField()

    class Meta:
        model = PullRequest
        fields = [
            "id", "github_pr_id", "number", "title", "repository", "author", "reviewers", "status", "status_label",
            "state", "draft", "created_at", "updated_at", "closed_at", "merged_at", "labels", "url",
            "review_count", "review_cycles", "first_review_at", "approved_at",
            "time_to_first_review_hours", "time_to_merge_hours",
        ]

    def get_author(self, pr):
        return {"login": pr.author_login, "avatar_url": pr.author_avatar_url}

    def get_reviewers(self, pr):
        return involved_reviewers(pr)

    def get_labels(self, pr):
        return [{"name": label.name, "color": label.color} for label in pr.labels.all()]

    def get_time_to_first_review_hours(self, pr):
        if not pr.first_review_at:
            return None
        return round((pr.first_review_at - pr.created_at_github).total_seconds() / HOURS, 2)

    def get_time_to_merge_hours(self, pr):
        if not pr.merged_at:
            return None
        return round((pr.merged_at - pr.created_at_github).total_seconds() / HOURS, 2)


class PullRequestDetailSerializer(PullRequestListSerializer):
    reviews = serializers.SerializerMethodField()
    reviewer_history = serializers.SerializerMethodField()
    timeline = serializers.SerializerMethodField()

    class Meta(PullRequestListSerializer.Meta):
        fields = PullRequestListSerializer.Meta.fields + [
            "description", "base_branch", "head_branch", "merge_commit_sha", "additions", "deletions",
            "changed_files", "commits_count", "comments_count", "last_synced_at",
            "reviews", "reviewer_history", "timeline",
        ]

    def get_reviews(self, pr):
        return [
            {
                "id": r.id,
                "github_review_id": r.github_review_id,
                "reviewer": r.reviewer,
                "avatar_url": r.reviewer_avatar_url,
                "state": r.state,
                "body": r.body,
                "submitted_at": r.submitted_at,
                "commit_id": r.commit_id,
            }
            for r in pr.reviews.all()
        ]

    def get_reviewer_history(self, pr):
        return [
            {
                "username": r.username,
                "avatar_url": r.avatar_url,
                "is_team": r.is_team,
                "assigned_at": r.assigned_at,
                "removed_at": r.removed_at,
            }
            for r in pr.reviewers.all()
        ]

    def get_timeline(self, pr):
        return build_timeline(pr)


class RepositorySerializer(serializers.ModelSerializer):
    pr_count = serializers.IntegerField(read_only=True, default=0)
    merged_count = serializers.IntegerField(read_only=True, default=0)
    open_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Repository
        fields = [
            "id", "github_repository_id", "owner", "name", "full_name", "url", "private", "is_active",
            "is_accessible", "import_status", "import_processed", "import_total", "import_error",
            "import_finished_at", "last_synced_at", "pr_count", "merged_count", "open_count",
        ]
