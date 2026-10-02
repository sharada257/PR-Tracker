"""Generate realistic demo data by pushing GitHub-shaped payloads through the real
sync pipeline (so the demo exercises exactly the code paths real data does).

    python manage.py seed_demo [--reset] [--count 90]
"""
import random
import uuid
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand
from django.utils import timezone as dj_timezone

from apps.accounts.models import Organization, OrganizationMembership, User
from apps.github import sync
from apps.github.models import GitHubInstallation, SyncJob
from apps.tracker.models import PullRequestReview, Repository

DEMO_GITHUB_ID = 9_000_001
DEMO_ORG_ID = 9_000_100
DEMO_INSTALLATION_ID = 9_000_200
REVIEWERS = [
    {"login": "john-doe", "id": 9_100_001, "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4"},
    {"login": "sarah-smith", "id": 9_100_002, "avatar_url": "https://avatars.githubusercontent.com/u/2?v=4"},
    {"login": "david-lee", "id": 9_100_003, "avatar_url": "https://avatars.githubusercontent.com/u/3?v=4"},
]
REPOS = [(9_001, "acme/helpdesk", 0.6), (9_002, "acme/contact-center", 0.3), (9_003, "acme/sms-gateway", 0.1)]
LABELS = ["bug", "feature", "backend", "frontend", "voice", "sms", "refactor"]
TITLES = [
    "Fix voice queue wait time", "Fix abandoned voice queue handling", "Update voice queue calculation",
    "SMS onboarding flow", "Contact center API pagination", "Add retry to SMS delivery",
    "Refactor ticket assignment service", "Improve agent presence updates", "Add call recording consent banner",
    "Fix timezone bug in SLA reports", "Cache contact lookups", "Support multiple phone numbers per contact",
    "Add webhook signature validation", "Migrate queue stats to async worker", "Handle empty transcripts",
    "Add bulk reassign endpoint", "Fix duplicate notifications", "Optimise inbox query", "Add DTMF menu editor",
    "Rate limit public API", "Clean up unused feature flags", "Fix flaky voicemail test", "Add CSV export for call logs",
]


def iso(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


class Command(BaseCommand):
    help = "Create a demo user with a realistic PR history."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete existing demo data first")
        parser.add_argument("--count", type=int, default=90)

    def handle(self, *args, count, reset, **options):
        if reset:
            Organization.objects.filter(github_organization_id=DEMO_ORG_ID).delete()
            User.objects.filter(github_user_id__in=[DEMO_GITHUB_ID] + [r["id"] for r in REVIEWERS]).delete()
        user, _ = User.objects.get_or_create(
            github_user_id=DEMO_GITHUB_ID,
            defaults={"username": "gh_demo", "github_username": "demo-dev", "display_name": "Demo Developer",
                      "avatar_url": "https://avatars.githubusercontent.com/u/9?v=4"},
        )
        org, _ = Organization.objects.get_or_create(
            github_organization_id=DEMO_ORG_ID,
            defaults={"name": "Acme Engineering", "github_organization_login": "acme", "account_type": "Organization",
                      "avatar_url": "https://avatars.githubusercontent.com/u/9?v=4"},
        )
        GitHubInstallation.objects.get_or_create(
            github_installation_id=DEMO_INSTALLATION_ID,
            defaults={"organization": org, "github_account_id": DEMO_ORG_ID, "github_account_login": "acme",
                      "account_type": "Organization", "repository_selection": "all"},
        )
        # The demo user administers the organization; the reviewers are plain members.
        OrganizationMembership.objects.get_or_create(
            organization=org, user=user, defaults={"role": OrganizationMembership.ROLE_ADMIN}
        )
        for person in REVIEWERS:
            teammate, _ = User.objects.get_or_create(
                github_user_id=person["id"],
                defaults={"username": "gh_%s" % person["id"], "github_username": person["login"],
                          "display_name": person["login"], "avatar_url": person["avatar_url"]},
            )
            OrganizationMembership.objects.get_or_create(organization=org, user=teammate)
        if Repository.objects.filter(organization=org).exists():
            self.stdout.write("Demo data already exists (use --reset to rebuild).")
            return

        rng = random.Random(42)
        repos = []
        for gh_id, full_name, weight in REPOS:
            owner, name = full_name.split("/")
            repo = Repository.objects.create(
                organization=org, github_repository_id=gh_id, owner=owner, name=name, full_name=full_name,
                url="https://github.com/" + full_name, is_active=True, import_status=Repository.IMPORT_DONE,
                import_finished_at=dj_timezone.now(), last_synced_at=dj_timezone.now(),
            )
            repos.append((repo, weight))

        me = {"login": user.github_username, "id": user.github_user_id, "avatar_url": user.avatar_url}
        start = datetime(2025, 1, 6, 9, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 30, 12, 0, tzinfo=timezone.utc)
        span = (end - start).total_seconds()
        numbers = {repo.id: 100 for repo, _ in repos}
        times = sorted(start + timedelta(seconds=rng.random() * span) for _ in range(count))

        for created in times:
            repo = rng.choices([r for r, _ in repos], weights=[w for _, w in repos])[0]
            numbers[repo.id] += rng.randint(1, 6)
            number = numbers[repo.id]
            self.build_pr(repo, number, created, me, rng)
        self.build_review_prs([r for r, _ in repos], me, rng)
        batch = uuid.uuid4()
        now = dj_timezone.now()
        SyncJob.objects.create(
            organization=org, batch=batch, type=SyncJob.TYPE_REPOSITORIES, trigger=SyncJob.TRIGGER_INSTALL,
            status=SyncJob.COMPLETED, started_at=now, completed_at=now, records_processed=len(repos),
            records_created=len(repos),
        )
        for repo, _ in repos:
            total = repo.pull_requests.count()
            SyncJob.objects.create(
                organization=org, repository=repo, batch=batch, type=SyncJob.TYPE_INITIAL,
                trigger=SyncJob.TRIGGER_INSTALL, status=SyncJob.COMPLETED, started_at=now, completed_at=now,
                records_processed=total, records_created=total,
                reviews_processed=PullRequestReview.objects.filter(pull_request__repository=repo).count(),
            )
        self.stdout.write(self.style.SUCCESS("Created %d demo pull requests for @%s." % (count, user.github_username)))

    def build_review_prs(self, repos, me, rng):
        """PRs written by teammates that the demo user was asked to review / has reviewed."""
        now = dj_timezone.now()
        number = 5000
        titles = list(TITLES)
        rng.shuffle(titles)

        def make(repo, author, created, title, state="open", merged_at=None, requested=(), reviews=(), asked_at=None):
            nonlocal number
            number += 1
            updated = max([created] + [datetime.fromisoformat(r["submitted_at"].replace("Z", "+00:00")) for r in reviews]
                          + [merged_at or created])
            timeline = []
            if asked_at:
                timeline.append({"event": "review_requested", "id": number * 10, "created_at": iso(asked_at),
                                 "requested_reviewer": me})
            payload = {
                "id": repo.github_repository_id * 100000 + number, "number": number, "title": title,
                "body": "%s." % title, "user": author, "state": state, "draft": False,
                "html_url": "%s/pull/%d" % (repo.url, number), "created_at": iso(created), "updated_at": iso(updated),
                "closed_at": iso(merged_at) if merged_at else None, "merged_at": iso(merged_at) if merged_at else None,
                "merge_commit_sha": "0f1e2d3c4b5a" if merged_at else None,
                "base": {"ref": "main"}, "head": {"ref": "feature/%d" % number},
                "requested_reviewers": list(requested), "requested_teams": [], "labels": [],
                "additions": rng.randint(5, 400), "deletions": rng.randint(0, 120), "changed_files": rng.randint(1, 12),
                "commits": 2, "comments": 0, "review_comments": rng.randint(0, 6),
            }
            sync.apply_full_pull_request(repo, payload, list(reviews), [], timeline)

        def review(rid, state_, at, who=me):
            return {"id": rid, "user": who, "state": state_, "submitted_at": iso(at), "commit_id": "abc", "body": ""}

        # Two reviews still waiting on the demo user: ~18h and ~4h.
        for repo, hours, title in ((repos[0], 18, "Fix SMS validation"), (repos[1], 4, "Contact center API")):
            asked = now - timedelta(hours=hours)
            make(repo, rng.choice(REVIEWERS), asked - timedelta(minutes=30), title, requested=[me], asked_at=asked)

        # Reviews already done: mostly approvals, a few change requests / comments.
        outcomes = ["APPROVED"] * 11 + ["CHANGES_REQUESTED"] * 2 + ["COMMENTED"] * 2
        start = datetime(2026, 1, 12, 10, 0, tzinfo=timezone.utc)
        step = (now - timedelta(days=2) - start) / len(outcomes)
        for i, outcome in enumerate(outcomes):
            reviewed_at = start + step * i + timedelta(hours=rng.uniform(0, 30))
            created = reviewed_at - timedelta(hours=rng.uniform(6, 60))
            merged_at = reviewed_at + timedelta(hours=rng.uniform(1, 30)) if outcome == "APPROVED" else None
            make(
                repos[i % len(repos)], rng.choice(REVIEWERS), created, titles[i % len(titles)],
                state="closed" if merged_at else "open", merged_at=merged_at,
                reviews=[review(7000 + i, outcome, reviewed_at)],
            )

    def build_pr(self, repo, number, created, me, rng):
        now = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        reviewer = rng.choice(REVIEWERS)
        outcome = rng.random()
        recent = (now - created) < timedelta(days=12)
        if recent and outcome < 0.7:
            outcome = 0.95  # recent PRs are mostly still open
        title = rng.choice(TITLES)
        reviews, commits, timeline, merged_at, closed_at, state = [], [], [], None, None, "open"
        requested = []

        first_review = created + timedelta(hours=rng.uniform(0.5, 30))
        timeline.append({"event": "review_requested", "id": number * 10, "created_at": iso(created + timedelta(minutes=8)),
                         "requested_reviewer": reviewer})
        rid = number * 100
        commits.append({"sha": "%d-0" % number, "author": me, "commit": {
            "message": "Initial work", "committer": {"date": iso(created - timedelta(hours=3))},
            "author": {"date": iso(created - timedelta(hours=3))}}})

        def review(state_, at, who=reviewer):
            nonlocal rid
            rid += 1
            return {"id": rid, "user": who, "state": state_, "submitted_at": iso(at), "commit_id": "abc", "body": ""}

        def commit(at, msg="Address review feedback"):
            commits.append({"sha": "%d-%d" % (number, len(commits)), "author": me, "commit": {
                "message": msg, "committer": {"date": iso(at)}, "author": {"date": iso(at)}}})

        updated = created
        if outcome < 0.78:  # merged
            at = first_review
            cycles = rng.choices([0, 1, 2, 3], weights=[55, 30, 10, 5])[0]
            for _ in range(cycles):
                reviews.append(review("CHANGES_REQUESTED", at))
                at += timedelta(hours=rng.uniform(1, 20))
                commit(at)
                at += timedelta(hours=rng.uniform(1, 12))
            if rng.random() < 0.3:
                reviews.append(review("COMMENTED", first_review - timedelta(minutes=5), REVIEWERS[1]))
            reviews.append(review("APPROVED", at))
            merged_at = at + timedelta(minutes=rng.uniform(5, 600))
            closed_at = merged_at
            state = "closed"
            updated = merged_at
        elif outcome < 0.88:  # closed unmerged
            closed_at = created + timedelta(days=rng.uniform(0.5, 6))
            state, updated = "closed", closed_at
            if rng.random() < 0.5:
                reviews.append(review("COMMENTED", first_review))
        else:  # open, at various review stages
            stage = rng.choice(["none", "requested", "changes", "approved", "commented"])
            if stage == "none":
                timeline.clear()
            elif stage == "requested":
                requested = [reviewer]
            elif stage == "changes":
                reviews.append(review("CHANGES_REQUESTED", first_review))
            elif stage == "approved":
                reviews.append(review("APPROVED", first_review))
            else:
                requested = [reviewer]
                reviews.append(review("COMMENTED", first_review))
            updated = max([created] + [datetime.fromisoformat(r["submitted_at"].replace("Z", "+00:00")) for r in reviews])

        labels = rng.sample(LABELS, rng.randint(1, 3))
        payload = {
            "id": repo.github_repository_id * 100000 + number, "number": number, "title": title,
            "body": "%s. Part of the %s work." % (title, labels[0]), "user": me, "state": state,
            "draft": False, "html_url": "%s/pull/%d" % (repo.url, number),
            "created_at": iso(created), "updated_at": iso(updated),
            "closed_at": iso(closed_at) if closed_at else None, "merged_at": iso(merged_at) if merged_at else None,
            "merge_commit_sha": "0f1e2d3c4b5a" if merged_at else None,
            "base": {"ref": "main"}, "head": {"ref": "feature/%d" % number},
            "requested_reviewers": requested, "requested_teams": [],
            "labels": [{"name": n, "color": "0e8a16"} for n in labels],
            "additions": rng.randint(5, 600), "deletions": rng.randint(0, 200), "changed_files": rng.randint(1, 20),
            "commits": len(commits), "comments": rng.randint(0, 5), "review_comments": rng.randint(0, 12),
        }
        sync.apply_full_pull_request(repo, payload, reviews, commits, timeline)
