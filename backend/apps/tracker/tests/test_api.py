from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from apps.github import sync
from apps.github.tests import factories as f


class ApiTestCase(TestCase):
    def setUp(self):
        self.org = f.make_org()
        self.user = f.make_member(self.org, f.make_user(), role="ADMIN")
        self.repo = f.make_repo(self.org)
        self.other_repo = f.make_repo(self.org, github_id=101, full_name="acme/contact-center")
        self.client = APIClient()
        self.client.force_login(self.user)

    def add(self, repo=None, **kwargs):
        reviews = kwargs.pop("reviews", [])
        commits = kwargs.pop("commits", [])
        timeline = kwargs.pop("timeline", [])
        return f.apply_pr(repo or self.repo, f.pr_payload(**kwargs), reviews, commits, timeline)

    def seed(self):
        story, reviews, commits, timeline = f.story()
        self.add_story = f.apply_pr(self.repo, story, reviews, commits, timeline)  # Sep 2026, merged
        self.add(number=1, created="2025-03-05T09:00:00Z", labels=["sms"], title="SMS onboarding",
                 state="closed", merged_at="2025-03-06T09:00:00Z", closed_at="2025-03-06T09:00:00Z")
        self.add(repo=self.other_repo, number=2, created="2026-09-02T09:00:00Z", requested=[f.SARAH],
                 title="Contact center API", labels=["backend"])
        self.add(number=3, created="2026-08-30T09:00:00Z", title="Closed idea", state="closed",
                 closed_at="2026-08-31T09:00:00Z")

    def get(self, path, **params):
        response = self.client.get(path, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()


class AuthTests(ApiTestCase):
    def test_all_data_endpoints_require_login(self):
        anonymous = APIClient()
        for path in ["/api/prs", "/api/prs/1", "/api/statistics", "/api/repositories", "/api/reviewers",
                     "/api/timeline/1", "/api/filters", "/api/my-reviews", "/api/labels", "/api/import/status", "/api/github/webhook-info"]:
            self.assertEqual(anonymous.get(path).status_code, 401, path)

    def test_me_reports_anonymous_without_error(self):
        data = APIClient().get("/api/auth/me").json()
        self.assertFalse(data["authenticated"])

    def test_other_organizations_data_is_invisible(self):
        self.seed()
        other_org = f.make_org(login="globex", org_id=501, installation_id=701)
        intruder = f.make_member(other_org, f.make_user("mallory", 666), role="ADMIN")
        client = APIClient()
        client.force_login(intruder)
        self.assertEqual(client.get("/api/prs").json()["count"], 0)
        self.assertEqual(client.get("/api/prs?scope=all").json()["count"], 0)
        self.assertEqual(client.get("/api/repositories").json()["count"], 0)
        pr = self.repo.pull_requests.first()
        self.assertEqual(client.get("/api/prs/%d" % pr.id).status_code, 404)
        self.assertEqual(client.get("/api/timeline/%d" % pr.id).status_code, 404)
        self.assertEqual(client.patch("/api/repositories/%d" % self.repo.id, {"is_active": False}, format="json").status_code, 404)
        self.assertEqual(client.get("/api/statistics?scope=all").json()["counts"]["total"], 0)

    def test_signed_in_user_without_an_organization_gets_the_no_access_message(self):
        client = APIClient()
        client.force_login(f.make_user("loner", 777))
        response = client.get("/api/prs")
        self.assertEqual(response.status_code, 403)
        self.assertIn("does not currently have access", response.json()["detail"])
        me = client.get("/api/auth/me").json()
        self.assertTrue(me["authenticated"])
        self.assertFalse(me["has_access"])
        self.assertIsNone(me["organization"])

    def test_revoked_members_and_removed_installations_lose_access(self):
        member = f.make_member(self.org, f.make_user("bob", 8))
        client = APIClient()
        client.force_login(member)
        self.assertEqual(client.get("/api/prs").status_code, 200)
        f.make_member(self.org, member, status="REVOKED")
        self.assertEqual(client.get("/api/prs").status_code, 403)
        f.make_member(self.org, member)
        self.org.installation.active = False
        self.org.installation.save()
        self.assertEqual(client.get("/api/prs").status_code, 403)

    def test_me_describes_the_active_organization_and_role(self):
        me = self.get("/api/auth/me")
        self.assertTrue(me["has_access"])
        self.assertEqual(me["organization"]["login"], "acme")
        self.assertEqual(me["organization"]["role"], "ADMIN")

    def test_switching_organization(self):
        other = f.make_org(login="globex", org_id=501, installation_id=701)
        f.make_member(other, self.user)
        self.assertEqual(self.get("/api/auth/me")["organizations"].__len__(), 2)
        response = self.client.post("/api/auth/organization", {"organization_id": other.id}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.get("/api/auth/me")["organization"]["login"], "globex")
        stranger = f.make_org(login="initech", org_id=502, installation_id=702)
        self.assertEqual(
            self.client.post("/api/auth/organization", {"organization_id": stranger.id}, format="json").status_code, 404
        )

    def test_scope_me_is_default_and_scope_all_shows_teammates(self):
        self.seed()
        self.add(number=900, author=f.JOHN, title="Teammate work")
        self.assertEqual(self.get("/api/prs")["count"], 4)
        everyone = self.get("/api/prs", scope="all")
        self.assertEqual(everyone["count"], 5)
        self.assertEqual(self.get("/api/prs", scope="all", author="john-doe")["count"], 1)
        self.assertIn("john-doe", self.get("/api/filters", scope="all")["authors"])
        # a teammate's PR can be opened even though it is not "mine"
        pr = self.repo.pull_requests.get(number=900)
        self.assertEqual(self.get("/api/prs/%d" % pr.id)["author"]["login"], "john-doe")

    def test_member_cannot_change_tracking_or_reimport_but_can_sync(self):
        member = f.make_member(self.org, f.make_user("bob", 8))
        client = APIClient()
        client.force_login(member)
        self.assertEqual(client.get("/api/repositories").status_code, 200)
        self.assertEqual(client.patch("/api/repositories/%d" % self.repo.id, {"is_active": False}, format="json").status_code, 403)
        self.assertEqual(client.post("/api/repositories/select", {"mode": "all"}, format="json").status_code, 403)
        self.assertEqual(client.get("/api/github/webhook-info").status_code, 403)
        self.assertEqual(client.post("/api/github/sync", {"full": True}, format="json").status_code, 403)
        from unittest import mock

        with mock.patch("apps.github.views.start_organization_sync", return_value="batch") as start:
            self.assertEqual(client.post("/api/github/sync", {}, format="json").status_code, 202)
        start.assert_called_once()

    def test_inactive_repositories_are_hidden(self):
        self.seed()
        self.other_repo.is_active = False
        self.other_repo.save()
        self.assertEqual(self.get("/api/prs")["count"], 3)


class ListAndFilterTests(ApiTestCase):
    def numbers(self, **params):
        return sorted(r["number"] for r in self.get("/api/prs", **params)["results"])

    def test_list_shape(self):
        self.seed()
        data = self.get("/api/prs", year=2026, month=9, status="merged")
        self.assertEqual(data["count"], 1)
        row = data["results"][0]
        self.assertEqual(row["number"], 1234)
        self.assertEqual(row["repository"]["full_name"], "acme/helpdesk")
        self.assertEqual(row["status"], "merged")
        self.assertEqual(row["reviewers"][0]["username"], "john-doe")
        self.assertEqual(sorted(l["name"] for l in row["labels"]), ["backend", "voice"])
        self.assertEqual(row["time_to_merge_hours"], 30.13)

    def test_year_month_repo_status_are_combinable(self):
        self.seed()
        self.assertEqual(self.numbers(year=2026), [2, 3, 1234])
        self.assertEqual(self.numbers(year=2026, month=9), [2, 1234])
        self.assertEqual(self.numbers(year=2026, month=9, repository=self.repo.id), [1234])
        self.assertEqual(self.numbers(year=2026, month=9, repository="acme/contact-center"), [2])
        self.assertEqual(self.numbers(year=2025), [1])
        self.assertEqual(self.numbers(year=2026, status="merged,closed"), [3, 1234])
        self.assertEqual(self.numbers(year=2026, month=9, status="merged"), [1234])
        self.assertEqual(self.numbers(quarter=3, year=2026), [2, 3, 1234])
        self.assertEqual(self.numbers(date_from="2026-09-01", date_to="2026-09-02"), [2])

    def test_reviewer_and_label_filters(self):
        self.seed()
        self.assertEqual(self.numbers(reviewer="john-doe"), [1234])
        self.assertEqual(self.numbers(reviewer="SARAH"), [2])  # requested but not yet reviewed
        self.assertEqual(self.numbers(label="backend"), [2, 1234])
        self.assertEqual(self.numbers(label="sms"), [1])

    def test_search_covers_title_description_repo_labels_reviewers(self):
        self.seed()
        self.assertEqual(self.numbers(q="voice queue"), [1234])
        self.assertEqual(self.numbers(q="contact center"), [2])  # every word must match somewhere
        self.assertEqual(self.numbers(q="voice sms"), [])
        self.assertEqual(self.numbers(q="contact-center"), [2])
        self.assertEqual(self.numbers(q="sms"), [1])  # label + title
        self.assertEqual(self.numbers(q="john"), [1234])  # reviewer
        self.assertEqual(self.numbers(q="#1234"), [1234])
        self.add(number=77, title="Unrelated", body="handles the zebra crossing edge case")
        self.assertEqual(self.numbers(q="zebra"), [77])  # description

    def test_ordering_and_pagination(self):
        self.seed()
        data = self.get("/api/prs", ordering="created_at", page_size=2)
        self.assertEqual([r["number"] for r in data["results"]], [1, 3])
        self.assertEqual(data["count"], 4)
        self.assertIsNotNone(data["next"])
        data = self.get("/api/prs", ordering="-merged_at", page_size=1)
        self.assertEqual(data["results"][0]["number"], 1234)

    def test_detail_includes_reviews_history_and_timeline(self):
        self.seed()
        pr = self.repo.pull_requests.get(number=1234)
        data = self.get("/api/prs/%d" % pr.id)
        self.assertEqual([r["state"] for r in data["reviews"]], ["CHANGES_REQUESTED", "APPROVED"])
        self.assertEqual(data["reviewer_history"][0]["username"], "john-doe")
        self.assertEqual(data["timeline"][0]["title"], "PR created")
        self.assertEqual(data["timeline"][-1]["title"], "PR merged")
        timeline = self.get("/api/timeline/%d" % pr.id)
        self.assertEqual(len(timeline), len(data["timeline"]))


class StatisticsTests(ApiTestCase):
    def test_statistics(self):
        self.seed()
        data = self.get("/api/statistics", year=2026)
        self.assertEqual(data["counts"]["filtered"], 3)
        self.assertEqual(data["counts"]["total"], 4)
        self.assertEqual(data["status"]["merged"], 1)
        self.assertEqual(data["status"]["closed"], 1)
        self.assertEqual(data["open_prs"], 1)
        self.assertEqual(data["waiting_review"], 1)  # PR #2: open, reviewer requested
        self.assertEqual(data["merge_metrics"]["merged"], 1)
        self.assertEqual(data["merge_metrics"]["closed_without_merge"], 1)
        self.assertAlmostEqual(data["merge_metrics"]["time_to_merge_hours"]["average"], 30.13, places=2)
        self.assertAlmostEqual(data["review_metrics"]["time_to_first_review_hours"]["average"], 4.32, places=2)
        total = sum(r["review_count"] for r in self.get("/api/prs", page_size=100)["results"])
        self.assertEqual(data["review_metrics"]["total_reviews"], total)
        self.assertGreater(data["review_metrics"]["total_reviews"], 0)
        activity = self.get("/api/statistics")["activity"]
        self.assertEqual(activity["granularity"], "month")
        sept = [b for b in activity["buckets"] if b["start"] == "2026-09-01"][0]
        self.assertEqual((sept["created"], sept["merged"]), (2, 1))
        self.assertEqual([y["year"] for y in data["by_year"]], [2025, 2026])
        self.assertEqual({r["full_name"]: r["total"] for r in data["by_repository"]},
                         {"acme/helpdesk": 2, "acme/contact-center": 1})
        self.assertIn("time_to_merge", data["definitions"])

    def test_month_period_block(self):
        self.seed()
        period = self.get("/api/statistics", year=2026, month=9)["period"]
        self.assertEqual((period["created"], period["merged"], period["still_open"]), (2, 1, 1))

    def test_waiting_review_counts_requested_in_review_and_changes_requested_only(self):
        waiting = lambda: self.get("/api/statistics")["waiting_review"]
        self.add(number=60, title="No reviewer asked")  # open, nobody asked -> not waiting
        self.assertEqual(waiting(), 0)
        self.add(number=61, title="Draft", draft=True, requested=[f.SARAH])
        self.assertEqual(waiting(), 0)
        self.add(number=62, title="Requested", requested=[f.SARAH])
        self.assertEqual(waiting(), 1)
        self.add(number=63, title="Approved", requested=[f.SARAH],
                 reviews=[f.review_payload(1, f.SARAH, "APPROVED", "2026-09-03T09:00:00Z")])
        self.assertEqual(waiting(), 1)  # approved PRs drop out
        self.add(number=64, title="Changes requested", requested=[f.SARAH],
                 reviews=[f.review_payload(2, f.SARAH, "CHANGES_REQUESTED", "2026-09-03T09:00:00Z")])
        self.assertEqual(waiting(), 2)  # changes requested still counts

    def test_pr_state_counts_open_merged_and_closed(self):
        stats = lambda: self.get("/api/statistics")  # noqa: E731
        self.add(number=60, title="Nobody asked")
        self.add(number=62, title="Requested", requested=[f.SARAH])
        self.add(number=63, title="Approved", requested=[f.SARAH],
                 reviews=[f.review_payload(1, f.SARAH, "APPROVED", "2026-09-03T09:00:00Z")])
        self.add(number=64, title="Changes", requested=[f.SARAH],
                 reviews=[f.review_payload(2, f.SARAH, "CHANGES_REQUESTED", "2026-09-03T09:00:00Z")])
        self.add(number=65, title="Merged", merged_at="2026-09-05T09:00:00Z", closed_at="2026-09-05T09:00:00Z", state="closed")
        self.add(number=66, title="Closed", state="closed", closed_at="2026-09-06T09:00:00Z")
        data = stats()
        self.assertEqual(
            data["pr_state"],
            {"open": 4, "in_review": 2, "approved": 1, "no_review": 1, "merged": 1, "closed": 1},
        )
        # active PRs split into in review (requested / changes requested), approved and not-yet-requested
        state = data["pr_state"]
        self.assertEqual(state["in_review"] + state["approved"] + state["no_review"], state["open"])
        self.assertEqual(state["open"] + state["merged"] + state["closed"], data["counts"]["filtered"])

    def test_activity_adapts_to_the_selected_range(self):
        self.seed()
        data = self.get("/api/statistics", date_from="2026-08-28", date_to="2026-09-03")["activity"]
        self.assertEqual(data["granularity"], "day")
        self.assertEqual(len(data["buckets"]), 7)
        by_day = {b["start"]: (b["created"], b["merged"]) for b in data["buckets"]}
        self.assertEqual(by_day["2026-08-30"], (1, 0))
        self.assertEqual(by_day["2026-09-02"], (1, 0))
        weekly = self.get("/api/statistics", date_from="2026-06-01", date_to="2026-09-30")["activity"]
        self.assertEqual(weekly["granularity"], "week")
        self.assertEqual(sum(b["created"] for b in weekly["buckets"]), 3)
        self.assertEqual(sum(b["merged"] for b in weekly["buckets"]), 1)

    def test_activity_without_dates_spans_all_data(self):
        self.seed()
        data = self.get("/api/statistics")["activity"]
        self.assertEqual(data["granularity"], "month")
        self.assertEqual(data["buckets"][0]["start"], "2025-03-05")  # first bucket starts at the first PR
        self.assertEqual(sum(b["created"] for b in data["buckets"]), 4)
        self.assertEqual(self.get("/api/statistics", repository=99999)["activity"]["buckets"], [])

    def test_period_block_covers_all_time_without_dates(self):
        self.seed()
        self.assertEqual(self.get("/api/statistics")["period"]["created"], 4)
        period = self.get("/api/statistics", date_from="2026-09-01", date_to="2026-09-30")["period"]
        self.assertEqual((period["created"], period["merged"]), (2, 1))

    def test_calendar_counts_use_current_date(self):
        now = timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.add(number=50, created=now)
        counts = self.get("/api/statistics")["counts"]
        self.assertEqual((counts["this_year"], counts["this_month"], counts["this_week"]), (1, 1, 1))

    def test_filters_apply_to_statistics(self):
        self.seed()
        data = self.get("/api/statistics", repository=self.other_repo.id)
        self.assertEqual(data["counts"]["filtered"], 1)

    def test_reviewers_view(self):
        self.seed()
        people = {p["login"]: p for p in self.get("/api/reviewers")}
        john = people["john-doe"]
        self.assertEqual((john["prs_reviewed"], john["reviews_submitted"], john["approvals"], john["changes_requested"]),
                         (1, 2, 1, 1))
        self.assertEqual(people["sarah"]["prs_reviewed"], 0)
        self.assertEqual(people["sarah"]["prs_requested"], 1)
        only_2025 = {p["login"] for p in self.get("/api/reviewers", year=2025)}
        self.assertEqual(only_2025, set())

    def test_filter_options(self):
        self.seed()
        options = self.get("/api/filters")
        self.assertEqual(options["years"], [2026, 2025])
        self.assertEqual(options["reviewers"], ["john-doe", "sarah"])
        self.assertEqual(options["labels"], ["backend", "sms", "voice"])


class RepositoryTests(ApiTestCase):
    def test_select_all_and_selected(self):
        self.repo.is_active = False
        self.repo.save()
        from unittest import mock

        with mock.patch("apps.tracker.views.start_import", return_value=True) as start:
            response = self.client.post("/api/repositories/select", {"mode": "all"}, format="json")
            self.assertEqual(response.status_code, 202)
            self.assertEqual(start.call_count, 2)
            self.client.post("/api/repositories/select", {"mode": "selected", "repository_ids": [self.repo.id]}, format="json")
        self.repo.refresh_from_db()
        self.other_repo.refresh_from_db()
        self.assertTrue(self.repo.is_active)
        self.assertFalse(self.other_repo.is_active)

    def test_repository_stats(self):
        self.seed()
        data = self.get("/api/repositories", active="true")
        by_name = {r["full_name"]: r for r in data["results"]}
        self.assertEqual(by_name["acme/helpdesk"]["pr_count"], 3)
        self.assertEqual(by_name["acme/helpdesk"]["merged_count"], 2)
        self.assertEqual(by_name["acme/contact-center"]["open_count"], 1)
