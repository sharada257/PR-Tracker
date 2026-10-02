from datetime import datetime, timezone

from django.test import TestCase

from apps.tracker.models import PullRequest, PullRequestReview, PullRequestReviewer, Repository
from apps.tracker.timeline import build_timeline

from .. import sync, tasks
from . import factories as f


def dt(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


class FullSyncTests(TestCase):
    def setUp(self):
        self.org = f.make_org()
        self.repo = f.make_repo(self.org)

    def apply_story(self):
        pr, reviews, commits, timeline = f.story()
        return f.apply_pr(self.repo, pr, reviews, commits, timeline)

    def test_story_populates_metrics(self):
        pr = self.apply_story()
        self.assertEqual(pr.status, "merged")
        self.assertEqual(pr.first_review_at, dt("2026-09-23T14:31:00Z"))
        self.assertEqual(pr.approved_at, dt("2026-09-24T16:15:00Z"))
        self.assertEqual(pr.review_count, 2)
        self.assertEqual(pr.review_cycles, 1)
        self.assertEqual(sorted(pr.labels.values_list("name", flat=True)), ["backend", "voice"])
        self.assertEqual(pr.github_pr_id, 6234)

    def test_timeline_matches_spec_lifecycle(self):
        pr = self.apply_story()
        kinds = [item["kind"] for item in build_timeline(pr)]
        self.assertEqual(
            kinds,
            ["created", "reviewer_assigned", "changes_requested", "commits_pushed", "review_approved", "merged"],
        )

    def test_sync_is_idempotent(self):
        self.apply_story()
        snapshot = lambda: (  # noqa: E731
            PullRequest.objects.count(),
            PullRequestReview.objects.count(),
            PullRequestReviewer.objects.count(),
            PullRequest.objects.get().events.count(),
            PullRequest.objects.get().labels.count(),
        )
        first = snapshot()
        self.apply_story()
        self.apply_story()
        self.assertEqual(first, snapshot())

    def test_review_original_state_is_preserved(self):
        self.apply_story()
        states = list(PullRequestReview.objects.order_by("submitted_at").values_list("state", flat=True))
        self.assertEqual(states, ["CHANGES_REQUESTED", "APPROVED"])

    def test_prs_by_every_author_are_tracked(self):
        pr = f.apply_pr(self.repo, f.pr_payload(author=f.SOMEONE), [], [], [])
        self.assertEqual(pr.author_login, "stranger")
        self.assertEqual(PullRequest.objects.count(), 1)

    def test_reviewer_history_is_preserved_and_review_fulfils_request(self):
        pr_json = f.pr_payload(requested=[f.SARAH], updated="2026-09-24T10:00:00Z")
        timeline = [
            f.timeline_event("review_requested", "2026-09-23T10:20:00Z", requested_reviewer=f.JOHN),
            f.timeline_event("review_request_removed", "2026-09-23T11:00:00Z", requested_reviewer=f.JOHN),
            f.timeline_event("review_requested", "2026-09-23T11:01:00Z", requested_reviewer=f.SARAH),
            f.timeline_event("review_requested", "2026-09-23T11:02:00Z", requested_reviewer=f.SOMEONE),
        ]
        reviews = [f.review_payload(1, f.SOMEONE, "COMMENTED", "2026-09-23T12:00:00Z")]
        pr = f.apply_pr(self.repo, pr_json, reviews, [], timeline)
        rows = {r.username: r for r in pr.reviewers.all()}
        self.assertIsNotNone(rows["john-doe"].removed_at)  # explicitly removed
        self.assertIsNone(rows["sarah"].removed_at)  # still requested
        self.assertEqual(rows["stranger"].removed_at, dt("2026-09-23T12:00:00Z"))  # fulfilled by reviewing
        self.assertEqual(pr.status, "review")
        titles = [i["title"] for i in build_timeline(pr)]
        self.assertIn("Reviewer removed: john-doe", titles)
        self.assertNotIn("Reviewer removed: stranger", titles)

    def test_team_reviewers_are_recorded(self):
        pr_json = f.pr_payload(teams=[{"id": 7, "slug": "backend", "name": "Backend"}])
        pr = f.apply_pr(self.repo, pr_json, [], [], [])
        row = pr.reviewers.get()
        self.assertTrue(row.is_team)
        self.assertEqual(row.username, "acme/backend")


class StatusTests(TestCase):
    def setUp(self):
        self.repo = f.make_repo(f.make_org())

    def sync(self, reviews=(), commits=(), **kwargs):
        timeline = kwargs.pop("timeline", [])
        return f.apply_pr(self.repo, f.pr_payload(**kwargs), list(reviews), list(commits), timeline)

    def test_open_without_activity(self):
        self.assertEqual(self.sync().status, "open")

    def test_draft_stays_open_even_with_requests(self):
        self.assertEqual(self.sync(draft=True, requested=[f.JOHN]).status, "open")

    def test_requested_reviewer_means_in_review(self):
        self.assertEqual(self.sync(requested=[f.JOHN]).status, "review")

    def test_changes_requested_beats_approval(self):
        reviews = [
            f.review_payload(1, f.JOHN, "APPROVED", "2026-09-23T12:00:00Z"),
            f.review_payload(2, f.SARAH, "CHANGES_REQUESTED", "2026-09-23T13:00:00Z"),
        ]
        self.assertEqual(self.sync(reviews).status, "changes_requested")

    def test_later_approval_supersedes_same_reviewers_changes_request(self):
        reviews = [
            f.review_payload(1, f.JOHN, "CHANGES_REQUESTED", "2026-09-23T12:00:00Z"),
            f.review_payload(2, f.JOHN, "APPROVED", "2026-09-23T13:00:00Z"),
        ]
        self.assertEqual(self.sync(reviews).status, "approved")

    def test_dismissed_review_no_longer_blocks(self):
        reviews = [f.review_payload(1, f.JOHN, "DISMISSED", "2026-09-23T12:00:00Z")]
        self.assertEqual(self.sync(reviews).status, "review")

    def test_author_self_review_is_ignored(self):
        reviews = [f.review_payload(1, f.ME, "COMMENTED", "2026-09-23T12:00:00Z")]
        pr = self.sync(reviews)
        self.assertEqual(pr.review_count, 0)
        self.assertIsNone(pr.first_review_at)

    def test_closed_without_merge(self):
        pr = self.sync(state="closed", closed_at="2026-09-25T10:00:00Z")
        self.assertEqual(pr.status, "closed")

    def test_review_cycles_need_commit_between_reviews(self):
        reviews = [
            f.review_payload(1, f.JOHN, "CHANGES_REQUESTED", "2026-09-23T12:00:00Z"),
            f.review_payload(2, f.JOHN, "CHANGES_REQUESTED", "2026-09-24T12:00:00Z"),
            f.review_payload(3, f.JOHN, "APPROVED", "2026-09-25T12:00:00Z"),
        ]
        commits = [f.commit_payload("a", "2026-09-23T18:00:00Z"), f.commit_payload("b", "2026-09-24T18:00:00Z")]
        self.assertEqual(self.sync(reviews, commits).review_cycles, 2)
        no_commits = f.pr_payload(number=2)
        self.assertEqual(f.apply_pr(self.repo, no_commits, reviews, [], []).review_cycles, 0)


class ImportAndReconcileTests(TestCase):
    def setUp(self):
        self.org = f.make_org()
        self.repo = f.make_repo(self.org)

    def fake_client(self, count=5):
        prs, items = {}, []
        for n in range(1, count + 1):
            created = "2026-0%d-10T10:00:00Z" % n
            prs[n] = f.pr_payload(number=n, created=created)
            items.append({"number": n, "created_at": created, "updated_at": created})
        return f.FakeClient(prs=prs, search_items=items)

    def run_import(self, client, batch=2):
        done, steps = False, 0
        while not done:
            done = sync.import_step(self.repo, client=client, batch_size=batch)
            self.repo.refresh_from_db()
            steps += 1
            self.assertLess(steps, 20)
        return steps

    def test_import_is_resumable_in_batches(self):
        self.repo.import_started_at = datetime.now(timezone.utc)
        self.repo.save()
        client = self.fake_client(5)
        # One batch only, then "crash".
        sync.import_step(self.repo, client=client, batch_size=2)
        self.repo.refresh_from_db()
        self.assertEqual(PullRequest.objects.count(), 2)
        self.assertEqual(self.repo.import_cursor, dt("2026-02-10T10:00:00Z"))
        # Resume from the cursor.
        self.run_import(client)
        self.assertEqual(sorted(PullRequest.objects.values_list("number", flat=True)), [1, 2, 3, 4, 5])
        self.assertEqual(self.repo.import_processed, 5)

    def test_import_task_chain_marks_repo_done(self):
        client = self.fake_client(3)
        from unittest import mock

        with mock.patch("apps.github.sync.auth.client_for_repository", return_value=client), mock.patch(
            "django.conf.settings.IMPORT_BATCH_SIZE", 2
        ):
            tasks.start_import(self.repo)
        self.repo.refresh_from_db()
        self.assertEqual(self.repo.import_status, Repository.IMPORT_DONE)
        self.assertEqual(PullRequest.objects.count(), 3)
        self.assertIsNotNone(self.repo.import_finished_at)

    def test_reconcile_recovers_missed_updates(self):
        client = self.fake_client(2)
        self.repo.import_started_at = datetime.now(timezone.utc)
        self.repo.save()
        self.run_import(client, batch=10)
        self.assertEqual(PullRequest.objects.get(number=1).status, "open")

        # Webhook for PR 1 was "missed": it was merged on GitHub, and a new PR 3 appeared.
        client.prs[1] = f.pr_payload(
            number=1, created="2026-01-10T10:00:00Z", updated="2026-09-30T10:00:00Z", state="closed",
            merged_at="2026-09-30T10:00:00Z", closed_at="2026-09-30T10:00:00Z",
        )
        client.prs[3] = f.pr_payload(number=3, created="2026-09-29T10:00:00Z")
        client.search_items[0]["updated_at"] = "2026-09-30T10:00:00Z"
        client.search_items.append({"number": 3, "created_at": "2026-09-29T10:00:00Z", "updated_at": "2026-09-29T10:00:00Z"})

        self.repo.last_synced_at = dt("2026-09-01T00:00:00Z")
        self.repo.save()
        sync.reconcile_repository(self.repo, client=client)
        self.assertEqual(PullRequest.objects.get(number=1).status, "merged")
        self.assertTrue(PullRequest.objects.filter(number=3).exists())
