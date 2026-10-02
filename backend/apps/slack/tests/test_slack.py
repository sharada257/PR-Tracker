from datetime import datetime, timedelta, timezone as dt_tz
from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog
from apps.github.tests import factories as f
from apps.slack import client as slack_client
from apps.slack import crypto, service
from apps.slack.models import SlackIntegration, SlackNotification, SlackUserLink
from apps.tracker.models import PullRequestReviewer
from apps.tracker.tests.test_api import ApiTestCase

SLACK_USERS = [
    {"id": "U1", "name": "octo", "real_name": "Octo Cat", "display_name": "", "email": "", "avatar_url": ""},
    {"id": "U2", "name": "jd", "real_name": "John Doe", "display_name": "", "email": "john@acme.test", "avatar_url": ""},
    {"id": "U3", "name": "sarah-smith", "real_name": "Sarah", "display_name": "", "email": "", "avatar_url": ""},
]


class FakeSlack:
    def __init__(self):
        self.sent = []
        self.error = None

    def users(self):
        return SLACK_USERS

    def post(self, user_id, text, blocks=None):
        if self.error:
            raise self.error
        self.sent.append((user_id, text, blocks))

    def revoke(self):
        pass


class SlackTestCase(ApiTestCase):
    """octo (self.user, admin) is the reviewer others ask; john-doe and sarah are members."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.john = f.make_member(self.org, f.make_user("john-doe", 2))
        self.sarah = f.make_member(self.org, f.make_user("sarah-smith", 3))
        self.integration = SlackIntegration.objects.create(
            organization=self.org, team_id="T1", team_name="Acme", bot_token_encrypted=crypto.encrypt("xoxb-secret")
        )
        SlackIntegration.objects.filter(pk=self.integration.pk).update(connected_at=timezone.now() - timedelta(days=5))
        self.integration.refresh_from_db()
        self.fake = FakeSlack()
        patcher = mock.patch("apps.slack.service.client_for", return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def link(self, user, slack_id, source="manual"):
        return SlackUserLink.objects.create(organization=self.org, user=user, slack_user_id=slack_id, source=source)

    def ask_octo(self, number, hours_ago, author=f.JOHN, **kwargs):
        pr = self.add(number=number, author=author, requested=[f.ME], created="2026-09-10T09:00:00Z", **kwargs)
        PullRequestReviewer.objects.filter(pull_request=pr).update(assigned_at=timezone.now() - timedelta(hours=hours_ago))
        return pr


class TokenStorageTests(SlackTestCase):
    def test_bot_token_is_stored_encrypted(self):
        self.assertNotIn("xoxb-secret", self.integration.bot_token_encrypted)
        self.assertEqual(crypto.decrypt(self.integration.bot_token_encrypted), "xoxb-secret")


class DispatchTests(SlackTestCase):
    def setUp(self):
        super().setUp()
        self.link(self.user, "U1")

    def test_new_review_request_sends_one_dm_to_the_reviewer(self):
        self.ask_octo(20, hours_ago=1)
        self.assertEqual(service.dispatch(self.integration), 1)
        user_id, text, blocks = self.fake.sent[0]
        self.assertEqual(user_id, "U1")
        self.assertEqual(text, "Review requested: #20 — Fix voice queue wait time (acme/helpdesk)")
        self.assertIn("Review requested", blocks[0]["text"]["text"])
        self.assertIn("#20 — Fix voice queue wait time", blocks[1]["text"]["text"])
        self.assertIn("acme/helpdesk · john-doe", blocks[1]["text"]["text"])
        self.assertIn("john-doe requested your review.", blocks[1]["text"]["text"])
        self.assertNotIn("Reviewers:", blocks[1]["text"]["text"])
        self.assertEqual(blocks[2]["elements"][0]["url"], "https://github.com/acme/helpdesk/pull/20")
        self.assertEqual(service.dispatch(self.integration), 0)  # never twice
        self.assertEqual(len(self.fake.sent), 1)

    def test_nothing_for_people_without_a_slack_link_drafts_or_closed_prs(self):
        pr = self.ask_octo(21, hours_ago=1)
        SlackUserLink.objects.all().delete()
        self.assertEqual(service.dispatch(self.integration), 0)
        self.link(self.user, "U1")
        PullRequestReviewer.objects.filter(pull_request=pr).update(removed_at=timezone.now())
        self.assertEqual(service.dispatch(self.integration), 0)
        self.ask_octo(22, hours_ago=1, draft=True)
        self.assertEqual(service.dispatch(self.integration), 0)

    def test_requests_from_before_slack_was_connected_are_not_replayed(self):
        SlackIntegration.objects.filter(pk=self.integration.pk).update(connected_at=timezone.now())
        self.integration.refresh_from_db()
        self.ask_octo(23, hours_ago=3)
        self.assertEqual(service.dispatch(self.integration), 0)

    def test_a_rerequest_after_removal_notifies_again(self):
        pr = self.ask_octo(24, hours_ago=2)
        service.dispatch(self.integration)
        PullRequestReviewer.objects.filter(pull_request=pr).update(assigned_at=timezone.now() - timedelta(minutes=5))
        service.dispatch(self.integration)
        self.assertEqual(len(self.fake.sent), 2)

    def only_reminders(self):
        SlackIntegration.objects.filter(pk=self.integration.pk).update(notify_review_requests=False)
        self.integration.refresh_from_db()

    def test_reminder_is_sent_once_the_threshold_passes_and_not_repeated(self):
        self.only_reminders()
        self.ask_octo(25, hours_ago=10)
        with mock.patch("apps.slack.service._is_business_hours", return_value=True):
            self.assertEqual(service.dispatch(self.integration), 0)  # not waiting long enough yet
            PullRequestReviewer.objects.update(assigned_at=timezone.now() - timedelta(hours=30))
            self.assertEqual(service.dispatch(self.integration), 1)
            self.assertTrue(self.fake.sent[0][1].startswith("Review reminder:"))
            self.assertEqual(service.dispatch(self.integration), 0)

    def test_reminders_are_capped_by_the_maximum(self):
        self.only_reminders()
        self.ask_octo(26, hours_ago=100)  # four thresholds have passed, but the maximum is two
        with mock.patch("apps.slack.service._is_business_hours", return_value=True):
            service.dispatch(self.integration)
            service.dispatch(self.integration)
        self.assertEqual(len(self.fake.sent), 1)
        self.assertTrue(SlackNotification.objects.get().key.endswith("#2"))

    def test_reminders_wait_for_business_hours(self):
        self.only_reminders()
        self.ask_octo(31, hours_ago=30)
        with mock.patch("apps.slack.service._is_business_hours", return_value=False):
            self.assertEqual(service.dispatch(self.integration), 0)
        with mock.patch("apps.slack.service._is_business_hours", return_value=True):
            self.assertEqual(service.dispatch(self.integration), 1)

    def test_business_hours_are_weekdays_nine_to_six_local_time(self):
        ist = lambda day, hour: datetime(2026, 10, day, hour, 0, tzinfo=dt_tz.utc) - timedelta(hours=5, minutes=30)
        self.assertTrue(service._is_business_hours(ist(5, 10)))  # Monday 10:00 IST
        self.assertFalse(service._is_business_hours(ist(3, 10)))  # Saturday
        self.assertFalse(service._is_business_hours(ist(5, 20)))  # Monday evening

    def test_switches_turn_things_off(self):
        SlackIntegration.objects.filter(pk=self.integration.pk).update(notify_review_requests=False, send_reminders=False)
        self.integration.refresh_from_db()
        self.ask_octo(27, hours_ago=30)
        self.assertEqual(service.dispatch(self.integration), 0)

    def test_rejected_token_pauses_the_integration_and_keeps_the_message_for_later(self):
        self.ask_octo(28, hours_ago=1)
        self.fake.error = slack_client.SlackError("token_revoked")
        service.dispatch(self.integration)
        self.integration.refresh_from_db()
        self.assertFalse(self.integration.active)
        self.assertEqual(self.integration.last_error, "token_revoked")
        self.assertEqual(SlackNotification.objects.count(), 0)

    def test_undeliverable_user_is_recorded_and_not_retried(self):
        self.ask_octo(29, hours_ago=1)
        self.fake.error = slack_client.SlackError("user_not_found")
        self.assertEqual(service.dispatch(self.integration), 0)
        self.assertEqual(SlackNotification.objects.get().status, "failed")
        self.fake.error = None
        self.assertEqual(service.dispatch(self.integration), 0)

    def test_rate_limit_is_retried_on_the_next_run(self):
        self.ask_octo(30, hours_ago=1)
        self.fake.error = slack_client.SlackRateLimited(10)
        self.assertEqual(service.dispatch(self.integration), 0)
        self.assertEqual(SlackNotification.objects.count(), 0)
        self.fake.error = None
        self.assertEqual(service.dispatch(self.integration), 1)


class ReviewOutcomeTests(SlackTestCase):
    """john-doe wrote the pull request; octo and sarah review it."""

    def setUp(self):
        super().setUp()
        self.link(self.john, "U2")

    def stamp(self, hours_ago):
        return (timezone.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def reviewed(self, number, reviewer, state, hours_ago=1, review_id=None, body="", author=f.JOHN):
        review = f.review_payload(review_id or number * 10, reviewer, state, self.stamp(hours_ago), body=body)
        return self.add(number=number, author=author, reviews=[review])

    def test_author_is_told_once_when_a_reviewer_approves(self):
        self.reviewed(40, f.ME, "APPROVED")
        self.assertEqual(service.dispatch(self.integration), 1)
        user_id, text, blocks = self.fake.sent[0]
        self.assertEqual(user_id, "U2")
        self.assertEqual(text, "PR approved: #40 — Fix voice queue wait time (acme/helpdesk)")
        self.assertIn("✅ *PR approved*", blocks[0]["text"]["text"])
        self.assertIn("acme/helpdesk · octo", blocks[1]["text"]["text"])
        self.assertIn("octo approved this PR.", blocks[1]["text"]["text"])
        self.assertEqual(blocks[2]["elements"][0]["url"], "https://github.com/acme/helpdesk/pull/40")
        self.assertEqual(service.dispatch(self.integration), 0)  # never twice
        self.assertEqual(SlackNotification.objects.get().kind, SlackNotification.REVIEW_APPROVED)

    def test_requested_changes_goes_to_the_author(self):
        self.reviewed(41, f.ME, "CHANGES_REQUESTED", body="Please add tests")
        self.assertEqual(service.dispatch(self.integration), 1)
        user_id, text, blocks = self.fake.sent[0]
        self.assertEqual(user_id, "U2")
        self.assertEqual(text, "Changes requested: #41 — Fix voice queue wait time (acme/helpdesk)")
        self.assertIn("🔴 *Changes requested*", blocks[0]["text"]["text"])
        self.assertIn("octo requested changes on this PR.", blocks[1]["text"]["text"])
        self.assertEqual(SlackNotification.objects.get().kind, SlackNotification.REVIEW_CHANGES)

    def test_each_review_is_its_own_message(self):
        self.reviewed(42, f.ME, "CHANGES_REQUESTED", hours_ago=3, review_id=1)
        service.dispatch(self.integration)
        self.add(number=42, author=f.JOHN, reviews=[
            f.review_payload(1, f.ME, "CHANGES_REQUESTED", self.stamp(3)),
            f.review_payload(2, f.ME, "APPROVED", self.stamp(1)),
        ])
        self.assertEqual(service.dispatch(self.integration), 1)
        self.assertEqual(
            [n[1].split(":")[0] for n in self.fake.sent], ["Changes requested", "PR approved"]
        )

    def test_comments_old_reviews_self_reviews_and_unlinked_authors_are_ignored(self):
        self.reviewed(43, f.ME, "COMMENTED")
        self.reviewed(44, f.ME, "APPROVED", hours_ago=60)  # too old to be news
        self.reviewed(45, f.JOHN, "APPROVED")  # reviewing your own pull request
        self.reviewed(46, f.ME, "APPROVED", author=f.SARAH)  # sarah has no Slack link
        self.assertEqual(service.dispatch(self.integration), 0)

    def test_reviews_from_before_slack_was_connected_are_not_replayed(self):
        SlackIntegration.objects.filter(pk=self.integration.pk).update(connected_at=timezone.now())
        self.integration.refresh_from_db()
        self.reviewed(47, f.ME, "APPROVED")
        self.assertEqual(service.dispatch(self.integration), 0)

    def test_the_switch_turns_it_off(self):
        SlackIntegration.objects.filter(pk=self.integration.pk).update(notify_review_outcomes=False)
        self.integration.refresh_from_db()
        self.reviewed(48, f.ME, "APPROVED")
        self.assertEqual(service.dispatch(self.integration), 0)

    def test_setting_can_be_changed_by_an_admin(self):
        response = self.client.patch("/api/slack", {"notify_review_outcomes": False}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["settings"]["notify_review_outcomes"])


class ReviewCycleTests(SlackTestCase):
    """john-doe wrote the pull requests; octo (U1) and sarah (U3) review them. The author is not linked here, so
    only the reviewers' messages show up."""

    def setUp(self):
        super().setUp()
        self.sarah_reviewer = f.make_member(self.org, f.make_user("sarah", 30))
        self.link(self.user, "U1")
        self.link(self.sarah_reviewer, "U3")

    def at(self, hours_ago):
        return (timezone.now() - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def pr(self, number, reviews=(), commits=(), requested=(f.ME,), asked=1):
        pr = self.add(number=number, author=f.JOHN, requested=list(requested), reviews=list(reviews),
                      commits=list(commits), created="2026-09-10T09:00:00Z")
        PullRequestReviewer.objects.filter(pull_request=pr).update(assigned_at=timezone.now() - timedelta(hours=asked))
        return pr

    def review(self, rid, who, state, hours_ago):
        return f.review_payload(rid, who, state, self.at(hours_ago))

    def push(self, sha, hours_ago):
        return f.commit_payload(sha, self.at(hours_ago), author=f.JOHN)

    def body(self, index=0):
        return self.fake.sent[index][2][1]["text"]["text"]

    def test_several_reviewers_asked_at_once_see_who_else_was_asked(self):
        self.pr(60, requested=[f.ME, f.SARAH])
        self.assertEqual(service.dispatch(self.integration), 2)
        self.assertEqual(sorted(m[0] for m in self.fake.sent), ["U1", "U3"])
        for index in (0, 1):
            self.assertIn("Reviewers: octo, sarah", self.body(index))

    def test_asked_again_after_changes_were_requested(self):
        self.pr(61, reviews=[self.review(1, f.ME, "CHANGES_REQUESTED", 6)], commits=[self.push("a1", 3)])
        self.assertEqual(service.dispatch(self.integration), 1)
        text = self.fake.sent[0][1]
        self.assertTrue(text.startswith("Review requested again:"))
        self.assertIn("🔁 *Review requested again*", self.fake.sent[0][2][0]["text"]["text"])
        self.assertIn("john-doe requested your review again after making changes.", self.body())
        self.assertEqual(self.fake.sent[0][2][2]["elements"][0]["text"]["text"], "Review PR")
        self.assertEqual(SlackNotification.objects.get().kind, SlackNotification.REVIEW_REREQUESTED)

    def test_asked_again_after_an_approval_warns_it_may_be_outdated(self):
        self.pr(62, reviews=[self.review(2, f.ME, "APPROVED", 6)], commits=[self.push("b1", 3)])
        self.assertEqual(service.dispatch(self.integration), 1)
        self.assertIn("made additional changes and requested your review again", self.body())
        self.assertIn("Your previous approval may no longer represent the latest changes.", self.body())

    def test_asked_again_without_new_commits_is_plain(self):
        self.pr(63, reviews=[self.review(3, f.ME, "APPROVED", 6)])
        self.assertEqual(service.dispatch(self.integration), 1)
        self.assertIn("john-doe requested your review again.", self.body())
        self.assertNotIn("previous approval", self.body())

    def test_reviewer_is_told_when_changes_are_pushed_after_their_feedback(self):
        self.pr(64, reviews=[self.review(4, f.ME, "CHANGES_REQUESTED", 6)], commits=[self.push("c1", 3)], requested=[])
        self.assertEqual(service.dispatch(self.integration), 1)
        user_id, text, blocks = self.fake.sent[0]
        self.assertEqual(user_id, "U1")
        self.assertTrue(text.startswith("Changes pushed:"))
        self.assertIn("🔄 *Changes pushed*", blocks[0]["text"]["text"])
        self.assertIn("acme/helpdesk · john-doe", blocks[1]["text"]["text"])
        self.assertIn("john-doe pushed new changes after review feedback.", blocks[1]["text"]["text"])
        self.assertEqual(blocks[2]["elements"][0]["text"]["text"], "Review again")
        self.assertEqual(service.dispatch(self.integration), 0)  # once per review, however many pushes follow

    def test_every_reviewer_who_asked_for_changes_is_told(self):
        self.pr(65, reviews=[self.review(5, f.ME, "CHANGES_REQUESTED", 6), self.review(6, f.SARAH, "CHANGES_REQUESTED", 5)],
                commits=[self.push("d1", 3)], requested=[])
        self.assertEqual(service.dispatch(self.integration), 2)
        self.assertEqual(sorted(m[0] for m in self.fake.sent), ["U1", "U3"])

    def test_no_push_message_when_nothing_changed_or_the_verdict_moved_on(self):
        self.pr(66, reviews=[self.review(7, f.ME, "CHANGES_REQUESTED", 3)], commits=[self.push("e1", 6)], requested=[])  # commit came first
        self.pr(67, reviews=[self.review(8, f.ME, "CHANGES_REQUESTED", 6), self.review(9, f.ME, "APPROVED", 4)],
                commits=[self.push("f1", 3)], requested=[])  # since approved
        self.pr(68, reviews=[self.review(10, f.ME, "COMMENTED", 6)], commits=[self.push("g1", 3)], requested=[])
        self.assertEqual(service.dispatch(self.integration), 0)

    def test_no_push_message_when_the_reviewer_was_already_asked_again(self):
        self.pr(69, reviews=[self.review(11, f.ME, "CHANGES_REQUESTED", 6)], commits=[self.push("h1", 3)], asked=1)
        self.assertEqual(service.dispatch(self.integration), 1)  # only "review requested again"
        self.assertTrue(self.fake.sent[0][1].startswith("Review requested again:"))

    def test_the_switch_covers_push_messages(self):
        SlackIntegration.objects.filter(pk=self.integration.pk).update(notify_review_outcomes=False)
        self.integration.refresh_from_db()
        self.pr(70, reviews=[self.review(12, f.ME, "CHANGES_REQUESTED", 6)], commits=[self.push("i1", 3)], requested=[])
        self.assertEqual(service.dispatch(self.integration), 0)


class AutoMatchTests(SlackTestCase):
    def test_matches_by_email_username_and_full_name_without_touching_manual_links(self):
        self.john.email = "john@acme.test"
        self.john.save()
        self.link(self.sarah, "U9")  # manual: stays
        linked = service.auto_match(self.integration, SLACK_USERS)
        links = {l.user.github_username: (l.slack_user_id, l.source) for l in SlackUserLink.objects.all()}
        self.assertEqual(links["john-doe"], ("U2", "auto"))  # by email
        self.assertEqual(links["octo"], ("U1", "auto"))  # by username
        self.assertEqual(links["sarah-smith"], ("U9", "manual"))
        self.assertEqual(linked, 2)


class AdminApiTests(SlackTestCase):
    def test_only_admins_can_use_the_slack_endpoints(self):
        member = APIClient()
        member.force_login(self.john)
        for method, path in (("get", "/api/slack"), ("patch", "/api/slack"), ("get", "/api/slack/links"),
                             ("post", "/api/slack/test"), ("delete", "/api/slack"), ("get", "/api/slack/connect")):
            self.assertEqual(getattr(member, method)(path).status_code, 403, path)

    def test_status_never_exposes_the_token(self):
        body = self.client.get("/api/slack").content.decode()
        self.assertNotIn("xoxb", body)
        self.assertNotIn("bot_token", body)
        self.assertTrue(self.client.get("/api/slack").json()["connected"])

    def test_settings_are_validated_and_saved(self):
        response = self.client.patch("/api/slack", {"send_reminders": False, "reminder_after_hours": 8, "max_reminders": 3}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        self.integration.refresh_from_db()
        self.assertEqual((self.integration.send_reminders, self.integration.reminder_after_hours, self.integration.max_reminders), (False, 8, 3))
        self.assertEqual(self.client.patch("/api/slack", {"reminder_after_hours": 0}, format="json").status_code, 400)
        self.assertEqual(self.client.patch("/api/slack", {"send_reminders": "yes"}, format="json").status_code, 400)

    def test_manual_links(self):
        url = "/api/slack/links/%d" % self.john.id
        self.assertEqual(self.client.put(url, {"slack_user_id": "U2"}, format="json").json(), {"slack_user_id": "U2", "source": "manual"})
        self.assertEqual(self.client.put("/api/slack/links/%d" % self.sarah.id, {"slack_user_id": "U2"}, format="json").status_code, 409)
        self.assertEqual(self.client.put(url, {"slack_user_id": "UNOPE"}, format="json").status_code, 400)
        data = self.client.get("/api/slack/links").json()
        self.assertEqual({m["github_username"]: m["slack_user_id"] for m in data["members"]}["john-doe"], "U2")
        self.client.put(url, {"slack_user_id": None}, format="json")
        self.assertFalse(SlackUserLink.objects.filter(user=self.john).exists())

    def test_test_message_goes_to_the_asking_admin(self):
        self.assertEqual(self.client.post("/api/slack/test").status_code, 409)  # not linked yet
        self.link(self.user, "U1")
        self.assertEqual(self.client.post("/api/slack/test").status_code, 200)
        self.assertEqual(self.fake.sent[0][0], "U1")

    def test_disconnect_removes_token_and_links(self):
        self.link(self.john, "U2")
        self.assertEqual(self.client.delete("/api/slack").status_code, 204)
        self.assertFalse(SlackIntegration.objects.exists())
        self.assertFalse(SlackUserLink.objects.exists())


@override_settings(SLACK_CLIENT_ID="cid", SLACK_CLIENT_SECRET="secret", PUBLIC_URL="https://app.test")
class OAuthTests(SlackTestCase):
    def setUp(self):
        super().setUp()
        SlackIntegration.objects.all().delete()

    def test_connect_stores_encrypted_token_links_people_and_audits(self):
        response = self.client.get("/api/slack/connect")
        self.assertEqual(response.status_code, 302)
        self.assertIn("slack.com/oauth/v2/authorize", response["Location"])
        self.assertIn("chat%3Awrite", response["Location"])
        state = self.client.session["slack_oauth_state"]["state"]
        grant = {"access_token": "xoxb-new", "bot_user_id": "UBOT", "team": {"id": "T9", "name": "Acme HQ"}}
        with mock.patch("apps.slack.client.exchange_code", return_value=grant):
            callback = self.client.get("/api/slack/callback", {"code": "c", "state": state})
        self.assertIn("connected=1", callback["Location"])
        integration = SlackIntegration.objects.get()
        self.assertEqual(integration.team_name, "Acme HQ")
        self.assertEqual(crypto.decrypt(integration.bot_token_encrypted), "xoxb-new")
        self.assertTrue(SlackUserLink.objects.filter(user=self.user, slack_user_id="U1").exists())
        self.assertTrue(AuditLog.objects.filter(action="slack.connected").exists())

    def test_forged_state_is_rejected(self):
        self.client.get("/api/slack/connect")
        with mock.patch("apps.slack.client.exchange_code") as exchange:
            response = self.client.get("/api/slack/callback", {"code": "c", "state": "forged"})
        self.assertIn("error=invalid_state", response["Location"])
        exchange.assert_not_called()
        self.assertFalse(SlackIntegration.objects.exists())

    def test_a_member_cannot_finish_an_admins_connection(self):
        self.client.get("/api/slack/connect")
        state = self.client.session["slack_oauth_state"]
        member = APIClient()
        member.force_login(self.john)
        session = member.session
        session["slack_oauth_state"] = state
        session.save()
        with mock.patch("apps.slack.client.exchange_code") as exchange:
            response = member.get("/api/slack/callback", {"code": "c", "state": state["state"]})
        self.assertIn("error=invalid_state", response["Location"])
        exchange.assert_not_called()
