"""Builders for GitHub-shaped payloads and a fake API client."""
from datetime import datetime, timezone

from apps.accounts.models import Organization, OrganizationMembership, User
from apps.github.models import GitHubInstallation
from apps.tracker.models import Repository

def dt_utc(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


ME = {"login": "octo", "id": 1, "avatar_url": "https://avatars/1"}
JOHN = {"login": "john-doe", "id": 2, "avatar_url": "https://avatars/2"}
SARAH = {"login": "sarah", "id": 3, "avatar_url": "https://avatars/3"}
SOMEONE = {"login": "stranger", "id": 99, "avatar_url": "https://avatars/99"}


def make_org(login="acme", org_id=500, installation_id=700):
    organization, _ = Organization.objects.get_or_create(
        github_organization_id=org_id,
        defaults={"name": login.title(), "github_organization_login": login, "account_type": "Organization"},
    )
    GitHubInstallation.objects.get_or_create(
        github_installation_id=installation_id,
        defaults={"organization": organization, "github_account_id": org_id, "github_account_login": login,
                  "account_type": "Organization", "repository_selection": "all"},
    )
    return organization


def make_user(login="octo", github_id=1):
    user, _ = User.objects.get_or_create(
        github_user_id=github_id, defaults={"username": "gh_%s" % github_id, "github_username": login}
    )
    return user


def make_member(org, user=None, role=OrganizationMembership.ROLE_MEMBER, status=OrganizationMembership.STATUS_ACTIVE):
    user = user or make_user()
    OrganizationMembership.objects.update_or_create(
        organization=org, user=user, defaults={"role": role, "status": status}
    )
    return user


def make_repo(org, github_id=100, full_name="acme/helpdesk", active=True, **extra):
    owner, name = full_name.split("/")
    return Repository.objects.create(
        organization=org, github_repository_id=github_id, owner=owner, name=name, full_name=full_name,
        url="https://github.com/" + full_name, is_active=active, **extra,
    )


def apply_pr(repo, *args, **kwargs):
    """``sync.apply_full_pull_request`` returning just the pull request."""
    from apps.github import sync

    return sync.apply_full_pull_request(repo, *args, **kwargs)[0]


def pr_payload(number=1234, author=ME, state="open", created="2026-09-23T10:12:00Z", updated=None, merged_at=None,
               closed_at=None, requested=(), teams=(), labels=(), draft=False, gh_id=None, title="Fix voice queue wait time",
               body="Fixes the queue wait calculation"):
    return {
        "id": gh_id or 5000 + number,
        "number": number,
        "title": title,
        "body": body,
        "user": author,
        "state": state,
        "draft": draft,
        "html_url": "https://github.com/acme/helpdesk/pull/%d" % number,
        "created_at": created,
        "updated_at": updated or created,
        "closed_at": closed_at,
        "merged_at": merged_at,
        "merge_commit_sha": "deadbeefcafe" if merged_at else None,
        "base": {"ref": "main", "repo": {"owner": {"login": "acme"}}},
        "head": {"ref": "feature/branch-%d" % number},
        "requested_reviewers": list(requested),
        "requested_teams": list(teams),
        "labels": [{"name": n, "color": "ededed"} for n in labels],
        "additions": 10, "deletions": 2, "changed_files": 3, "commits": 2, "comments": 1, "review_comments": 2,
    }


def review_payload(rid, user, state, submitted_at, commit_id="abc123", body=""):
    return {"id": rid, "user": user, "state": state, "submitted_at": submitted_at, "commit_id": commit_id, "body": body}


def commit_payload(sha, date, author=ME, message="Fix it"):
    return {"sha": sha, "author": author, "commit": {"message": message, "committer": {"date": date}, "author": {"date": date, "name": "x"}}}


def timeline_event(event, created_at, **extra):
    return {"event": event, "created_at": created_at, "id": extra.pop("id", hash((event, created_at)) % 10**9), **extra}


def story():
    """The PR #1234 lifecycle from the product spec."""
    pr = pr_payload(
        created="2026-09-23T10:12:00Z", updated="2026-09-24T16:20:00Z", state="closed",
        merged_at="2026-09-24T16:20:00Z", closed_at="2026-09-24T16:20:00Z", labels=["voice", "backend"],
    )
    reviews = [
        review_payload(11, JOHN, "CHANGES_REQUESTED", "2026-09-23T14:31:00Z", body="Please add tests"),
        review_payload(12, JOHN, "APPROVED", "2026-09-24T16:15:00Z"),
    ]
    commits = [
        commit_payload("c0", "2026-09-23T09:00:00Z"),
        commit_payload("c1", "2026-09-24T11:05:00Z", message="Add tests"),
    ]
    timeline = [timeline_event("review_requested", "2026-09-23T10:20:00Z", requested_reviewer=JOHN)]
    return pr, reviews, commits, timeline


class FakeClient:
    def __init__(self, prs=None, reviews=None, commits=None, timelines=None, search_items=None):
        self.prs = prs or {}
        self.reviews = reviews or {}
        self.commits = commits or {}
        self.timelines = timelines or {}
        self.search_items = search_items or []
        self.calls = []

    def get_pull(self, full_name, number):
        self.calls.append(("pull", number))
        return self.prs[number]

    def list_reviews(self, full_name, number):
        return self.reviews.get(number, [])

    def list_commits(self, full_name, number):
        return self.commits.get(number, [])

    def list_timeline(self, full_name, number):
        return self.timelines.get(number, [])

    def search_issues(self, query, sort="created", order="asc", per_page=50, page=1):
        self.calls.append(("search", query))
        items = list(self.search_items)
        if "created:>=" in query:
            since = query.split("created:>=")[1].split()[0]
            items = [i for i in items if i["created_at"] >= since]
        if "updated:>=" in query:
            since = query.split("updated:>=")[1].split()[0]
            items = [i for i in items if i["updated_at"] >= since]
        key = "created_at" if sort == "created" else "updated_at"
        items.sort(key=lambda i: i[key], reverse=(order == "desc"))
        start = (page - 1) * per_page
        return {"total_count": len(items), "items": items[start:start + per_page]}
