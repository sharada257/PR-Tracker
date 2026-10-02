import hashlib
import hmac
import json
from unittest import mock

from django.test import Client, TestCase

from apps.tracker.models import PullRequest, PullRequestReview

from ..models import WebhookEvent
from . import factories as f

URL = "/api/webhooks/github"
SECRET = "test-secret"


def post(client, event, payload, delivery="d-1", secret=SECRET, signature=None):
    body = json.dumps(payload).encode()
    if signature is None:
        signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        URL, data=body, content_type="application/json",
        HTTP_X_GITHUB_EVENT=event, HTTP_X_GITHUB_DELIVERY=delivery, HTTP_X_HUB_SIGNATURE_256=signature,
    )


def pr_event(action, pr, **extra):
    return {"action": action, "pull_request": pr, "repository": {"id": 100, "full_name": "acme/helpdesk"},
            "sender": f.ME, **extra}


class WebhookTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.org = f.make_org()
        self.repo = f.make_repo(self.org)
        # The follow-up API refresh for brand-new PRs must not hit the network.
        patcher = mock.patch("apps.github.tasks.sync.sync_pull_request", return_value=None)
        self.addCleanup(patcher.stop)
        self.sync_mock = patcher.start()

    # -- security / validation ---------------------------------------------
    def test_rejects_bad_signature(self):
        response = post(self.client, "pull_request", pr_event("opened", f.pr_payload()), signature="sha256=nope")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_rejects_signature_made_with_other_secret(self):
        response = post(self.client, "pull_request", pr_event("opened", f.pr_payload()), secret="other")
        self.assertEqual(response.status_code, 401)

    def test_ping(self):
        self.assertEqual(post(self.client, "ping", {"zen": "x"}).status_code, 200)

    def test_unsupported_event_ignored_and_not_stored(self):
        response = post(self.client, "issues", {"action": "opened"})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(WebhookEvent.objects.count(), 0)

    def test_requires_delivery_id(self):
        body = json.dumps({}).encode()
        sig = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
        response = self.client.post(URL, data=body, content_type="application/json",
                                    HTTP_X_GITHUB_EVENT="pull_request", HTTP_X_HUB_SIGNATURE_256=sig)
        self.assertEqual(response.status_code, 400)

    # -- processing ---------------------------------------------------------
    def test_opened_creates_pr(self):
        response = post(self.client, "pull_request", pr_event("opened", f.pr_payload(labels=["voice"])))
        self.assertEqual(response.status_code, 202)
        pr = PullRequest.objects.get()
        self.assertEqual((pr.number, pr.status), (1234, "open"))
        self.assertEqual(list(pr.labels.values_list("name", flat=True)), ["voice"])
        self.assertEqual(WebhookEvent.objects.get().status, WebhookEvent.PROCESSED)
        self.sync_mock.assert_called_once()  # new PR -> full refresh queued

    def test_redelivery_does_not_duplicate(self):
        payload = pr_event("opened", f.pr_payload(requested=[f.JOHN]))
        post(self.client, "pull_request", payload, delivery="same")
        again = post(self.client, "pull_request", payload, delivery="same")
        self.assertEqual(again.json()["status"], "duplicate")
        self.assertEqual(PullRequest.objects.count(), 1)
        self.assertEqual(PullRequest.objects.get().reviewers.count(), 1)
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_same_event_with_new_delivery_id_is_still_idempotent_in_data(self):
        payload = pr_event("review_requested", f.pr_payload(requested=[f.JOHN]))
        post(self.client, "pull_request", payload, delivery="a")
        post(self.client, "pull_request", payload, delivery="b")
        pr = PullRequest.objects.get()
        self.assertEqual(pr.reviewers.count(), 1)

    def test_failed_delivery_is_retried_on_redelivery(self):
        payload = pr_event("opened", f.pr_payload())
        with mock.patch("apps.github.processing.process_webhook", side_effect=RuntimeError("boom")):
            post(self.client, "pull_request", payload, delivery="x")
        event = WebhookEvent.objects.get()
        self.assertEqual(event.status, WebhookEvent.FAILED)
        self.assertIn("boom", event.error)
        response = post(self.client, "pull_request", payload, delivery="x")
        self.assertEqual(response.json()["status"], "requeued")
        event.refresh_from_db()
        self.assertEqual(event.status, WebhookEvent.PROCESSED)
        self.assertEqual(PullRequest.objects.count(), 1)

    def test_requests_and_submitted_reviews_queue_a_slack_dispatch(self):
        installation = {"installation": {"id": 700}}
        pr = f.pr_payload(requested=[f.JOHN])
        review = f.review_payload(7, f.JOHN, "approved", "2026-09-23T10:20:00Z")
        with mock.patch("apps.slack.tasks.dispatch_organization.delay") as queued:
            post(self.client, "pull_request", {**pr_event("review_requested", pr), **installation}, delivery="r1")
            post(self.client, "pull_request_review", {**pr_event("submitted", pr, review=review), **installation}, delivery="r2")
            post(self.client, "pull_request", {**pr_event("labeled", pr), **installation}, delivery="r3")
        self.assertEqual(queued.call_count, 2)

    def test_reviewer_assignment_and_removal(self):
        post(self.client, "pull_request", pr_event("opened", f.pr_payload()), delivery="1")
        post(self.client, "pull_request",
             pr_event("review_requested", f.pr_payload(requested=[f.JOHN], updated="2026-09-23T10:20:00Z")), delivery="2")
        pr = PullRequest.objects.get()
        self.assertEqual(pr.status, "review")
        self.assertEqual(pr.reviewers.get().assigned_at.isoformat(), "2026-09-23T10:20:00+00:00")
        post(self.client, "pull_request",
             pr_event("review_request_removed", f.pr_payload(updated="2026-09-23T10:30:00Z")), delivery="3")
        row = pr.reviewers.get()
        self.assertIsNotNone(row.removed_at)
        pr.refresh_from_db()
        self.assertEqual(pr.status, "open")

    def test_review_events_track_state_and_approval(self):
        pr_json = f.pr_payload(updated="2026-09-23T14:31:00Z")
        post(self.client, "pull_request", pr_event("opened", f.pr_payload()), delivery="1")
        review = f.review_payload(11, f.JOHN, "changes_requested", "2026-09-23T14:31:00Z")
        post(self.client, "pull_request_review", pr_event("submitted", pr_json, review=review), delivery="2")
        pr = PullRequest.objects.get()
        self.assertEqual(pr.status, "changes_requested")
        self.assertEqual(PullRequestReview.objects.get().state, "CHANGES_REQUESTED")

        approve = f.review_payload(12, f.JOHN, "approved", "2026-09-24T16:15:00Z")
        post(self.client, "pull_request_review",
             pr_event("submitted", f.pr_payload(updated="2026-09-24T16:15:00Z"), review=approve), delivery="3")
        pr.refresh_from_db()
        self.assertEqual(pr.status, "approved")
        self.assertEqual(pr.approved_at.isoformat(), "2026-09-24T16:15:00+00:00")

        dismissed = f.review_payload(11, f.JOHN, "dismissed", "2026-09-23T14:31:00Z")
        post(self.client, "pull_request_review",
             pr_event("dismissed", f.pr_payload(updated="2026-09-24T17:00:00Z"), review=dismissed), delivery="4")
        self.assertEqual(PullRequestReview.objects.get(github_review_id=11).state, "DISMISSED")

    def test_synchronize_records_commit_and_counts_cycle(self):
        post(self.client, "pull_request", pr_event("opened", f.pr_payload()), delivery="1")
        review = f.review_payload(11, f.JOHN, "changes_requested", "2026-09-23T14:31:00Z")
        post(self.client, "pull_request_review",
             pr_event("submitted", f.pr_payload(updated="2026-09-23T14:31:00Z"), review=review), delivery="2")
        post(self.client, "pull_request",
             pr_event("synchronize", f.pr_payload(updated="2026-09-24T11:05:00Z"), after="abc999"), delivery="3")
        approve = f.review_payload(12, f.JOHN, "approved", "2026-09-24T16:15:00Z")
        post(self.client, "pull_request_review",
             pr_event("submitted", f.pr_payload(updated="2026-09-24T16:15:00Z"), review=approve), delivery="4")
        pr = PullRequest.objects.get()
        self.assertEqual(pr.review_cycles, 1)

    def test_merge_and_close(self):
        post(self.client, "pull_request", pr_event("opened", f.pr_payload()), delivery="1")
        merged = f.pr_payload(state="closed", updated="2026-09-25T10:00:00Z", merged_at="2026-09-25T10:00:00Z",
                              closed_at="2026-09-25T10:00:00Z")
        post(self.client, "pull_request", pr_event("closed", merged), delivery="2")
        pr = PullRequest.objects.get()
        self.assertEqual(pr.status, "merged")

        other = f.pr_payload(number=2, state="closed", updated="2026-09-25T10:00:00Z", closed_at="2026-09-25T10:00:00Z")
        post(self.client, "pull_request", pr_event("closed", other), delivery="3")
        self.assertEqual(PullRequest.objects.get(number=2).status, "closed")

    def test_out_of_order_delivery_does_not_regress_state(self):
        newer = f.pr_payload(state="closed", updated="2026-09-25T10:00:00Z", merged_at="2026-09-25T10:00:00Z",
                             closed_at="2026-09-25T10:00:00Z")
        older = f.pr_payload(updated="2026-09-24T10:00:00Z", title="old title")
        post(self.client, "pull_request", pr_event("closed", newer), delivery="2")
        post(self.client, "pull_request", pr_event("edited", older), delivery="1")
        pr = PullRequest.objects.get()
        self.assertEqual(pr.status, "merged")
        self.assertNotEqual(pr.title, "old title")

    def test_every_authors_prs_are_tracked_but_untracked_repos_are_ignored(self):
        post(self.client, "pull_request", pr_event("opened", f.pr_payload(author=f.SOMEONE)), delivery="1")
        self.assertEqual(PullRequest.objects.get().author_login, "stranger")

        self.repo.is_active = False
        self.repo.save()
        post(self.client, "pull_request", pr_event("opened", f.pr_payload(number=2)), delivery="2")
        self.assertEqual(PullRequest.objects.count(), 1)
        self.assertEqual(WebhookEvent.objects.get(github_delivery_id="2").status, WebhookEvent.IGNORED)

    def test_event_for_another_installation_is_ignored(self):
        payload = pr_event("opened", f.pr_payload(), installation={"id": 999})
        post(self.client, "pull_request", payload, delivery="1")
        self.assertEqual(PullRequest.objects.count(), 0)

    def test_same_repository_in_two_organizations_updates_only_the_sending_installation(self):
        other = f.make_org(login="globex", org_id=501, installation_id=701)
        other_repo = f.make_repo(other, github_id=100, full_name="globex/helpdesk")
        payload = pr_event("opened", f.pr_payload(), installation={"id": 701})
        post(self.client, "pull_request", payload, delivery="1")
        self.assertEqual(PullRequest.objects.get().repository_id, other_repo.id)

    # -- installation lifecycle ----------------------------------------------
    def test_installation_created_registers_org_and_queues_sync(self):
        payload = {
            "action": "created",
            "installation": {"id": 801, "repository_selection": "all",
                             "account": {"id": 601, "login": "initech", "type": "Organization"}},
        }
        with mock.patch("apps.github.tasks.sync_organization.delay") as sync_org:
            post(self.client, "installation", payload, delivery="i1")
        from apps.accounts.models import Organization

        org = Organization.objects.get(github_organization_id=601)
        self.assertEqual(org.installation.github_installation_id, 801)
        sync_org.assert_called_once()
        self.assertEqual(org.memberships.count(), 0)  # nobody is admin until the first person signs in

    def test_installation_deleted_hides_repositories(self):
        payload = {"action": "deleted", "installation": {"id": 700, "account": {"id": 500, "login": "acme"}}}
        post(self.client, "installation", payload, delivery="i2")
        self.org.installation.refresh_from_db()
        self.assertFalse(self.org.installation.active)
        self.repo.refresh_from_db()
        self.assertFalse(self.repo.is_accessible)

    def test_installation_suspend_and_unsuspend(self):
        base = {"installation": {"id": 700, "account": {"id": 500, "login": "acme"}}}
        post(self.client, "installation", {"action": "suspend", **base}, delivery="s1")
        self.org.installation.refresh_from_db()
        self.assertFalse(self.org.installation.usable)
        with mock.patch("apps.github.tasks.sync_organization.delay"):
            post(self.client, "installation", {"action": "unsuspend", **base}, delivery="s2")
        self.org.installation.refresh_from_db()
        self.assertTrue(self.org.installation.usable)

    def test_repositories_removed_from_installation_are_hidden(self):
        payload = {"action": "removed", "installation": {"id": 700}, "repository_selection": "selected",
                   "repositories_removed": [{"id": 100}]}
        post(self.client, "installation_repositories", payload, delivery="r1")
        self.repo.refresh_from_db()
        self.assertFalse(self.repo.is_accessible)

    def test_secret_must_be_configured(self):
        with self.settings(GITHUB_WEBHOOK_SECRET=""):
            self.assertEqual(post(self.client, "ping", {}).status_code, 503)
