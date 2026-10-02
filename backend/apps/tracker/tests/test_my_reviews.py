from rest_framework.test import APIClient

from apps.github.tests import factories as f

from .test_api import ApiTestCase

ASKED = "2026-09-30T09:00:00Z"


class MyReviewsApiTests(ApiTestCase):
    def theirs(self, number, author=f.JOHN, repo=None, **kwargs):
        return self.add(repo=repo, number=number, author=author, **kwargs)

    def seed_reviews(self):
        ask = lambda at: [f.timeline_event("review_requested", at, requested_reviewer=f.ME)]  # noqa: E731
        # Pending: asked to review, nothing submitted yet.
        self.theirs(10, requested=[f.ME], created="2026-09-29T09:00:00Z", timeline=ask("2026-09-29T09:30:00Z"),
                    title="Fix SMS validation")
        self.theirs(14, repo=self.other_repo, requested=[f.ME], created="2026-09-30T08:00:00Z",
                    timeline=ask(ASKED), title="Contact center API", author=f.SARAH)
        # Reviewed: approved, changes requested, and comment-only.
        self.theirs(11, created="2026-09-26T09:00:00Z", reviews=[f.review_payload(1, f.ME, "APPROVED", "2026-09-28T10:00:00Z")])
        self.theirs(12, repo=self.other_repo, author=f.SARAH, created="2026-09-18T09:00:00Z",
                    reviews=[f.review_payload(2, f.ME, "CHANGES_REQUESTED", "2026-09-20T10:00:00Z")])
        self.theirs(13, created="2025-11-30T09:00:00Z", reviews=[f.review_payload(3, f.ME, "COMMENTED", "2025-12-01T10:00:00Z")])
        # Noise that must not show up.
        self.theirs(20, requested=[f.SARAH])  # someone else's review: stored, but never listed for me
        self.add(number=30, created="2026-09-10T09:00:00Z", title="My own PR")

    def mine(self, **params):
        return self.get("/api/my-reviews", **params)

    def test_requires_login(self):
        self.assertEqual(APIClient().get("/api/my-reviews").status_code, 401)

    def test_empty_state(self):
        data = self.mine()
        self.assertEqual(data["activity"], {"reviewed": 0, "approvals": 0, "changes_requested": 0, "commented": 0, "pending": 0})
        self.assertEqual((data["pending"]["count"], data["count"], data["results"]), (0, 0, []))

    def test_pending_reviews_list_the_longest_waiting_first(self):
        self.seed_reviews()
        pending = self.mine()["pending"]
        self.assertEqual(pending["count"], 2)
        self.assertEqual([i["number"] for i in pending["items"]], [10, 14])
        first = pending["items"][0]
        self.assertEqual((first["title"], first["repository"]["full_name"], first["author_login"]),
                         ("Fix SMS validation", "acme/helpdesk", "john-doe"))
        self.assertGreater(first["waiting_hours"], pending["items"][1]["waiting_hours"])
        self.assertEqual(first["status"], "pending")

    def test_review_activity_counts(self):
        self.seed_reviews()
        self.assertEqual(
            self.mine(date_from="2025-01-01")["activity"],
            {"reviewed": 3, "approvals": 1, "changes_requested": 1, "commented": 1, "pending": 2},
        )

    def test_table_lists_reviewed_prs_newest_first(self):
        self.seed_reviews()
        data = self.mine(date_from="2025-01-01")
        self.assertEqual([(r["number"], r["status"]) for r in data["results"]],
                         [(11, "approved"), (12, "changes_requested"), (13, "commented")])
        self.assertTrue(data["results"][0]["reviewed_at"].startswith("2026-09-28"))
        self.assertEqual(data["results"][1]["repository"]["full_name"], "acme/contact-center")

    def test_date_range_filters_reviewed_but_not_pending(self):
        self.seed_reviews()
        data = self.mine(date_from="2026-01-01", date_to="2026-12-31")
        self.assertEqual([r["number"] for r in data["results"]], [11, 12])
        self.assertEqual(data["activity"]["reviewed"], 2)
        self.assertEqual(data["activity"]["pending"], 2)  # pending is "right now"
        narrow = self.mine(date_from="2026-09-27", date_to="2026-09-29")
        self.assertEqual([r["number"] for r in narrow["results"]], [11])

    def test_status_and_repository_filters(self):
        self.seed_reviews()
        base = {"date_from": "2025-01-01"}
        self.assertEqual([r["number"] for r in self.mine(status="approved", **base)["results"]], [11])
        self.assertEqual([r["number"] for r in self.mine(status="approved,commented", **base)["results"]], [11, 13])
        self.assertEqual([r["number"] for r in self.mine(repository=self.other_repo.id, **base)["results"]], [12])
        pending = self.mine(status="pending", date_from="2026-09-27")
        self.assertEqual([r["number"] for r in pending["results"]], [10, 14])  # date range does not apply
        self.assertEqual(self.mine(repository=self.other_repo.id)["pending"]["count"], 1)

    def test_search_narrows_the_table_but_not_the_cards(self):
        self.seed_reviews()
        numbers = lambda **p: [r["number"] for r in self.mine(**p)["results"]]  # noqa: E731
        self.assertEqual(numbers(q="sarah"), [12])  # author
        self.assertEqual(numbers(q="#11"), [11])  # PR number
        self.assertEqual(numbers(q="acme/helpdesk"), [11, 13])  # repository (PR 13 is also in helpdesk)
        self.assertEqual(numbers(q="contact api", status="pending"), [14])  # every word, any order, with filters
        self.assertEqual(numbers(q="nothing matches"), [])
        data = self.mine(q="nothing matches")
        self.assertEqual((data["pending"]["count"], data["activity"]["reviewed"]), (2, 3))

    def test_my_review_activity_in_statistics(self):
        self.seed_reviews()
        # a second review of PR 11 (a comment) counts as another review event but not another PR
        self.theirs(11, reviews=[f.review_payload(1, f.ME, "APPROVED", "2026-09-28T10:00:00Z"),
                                 f.review_payload(4, f.ME, "COMMENTED", "2026-09-29T10:00:00Z")])
        data = self.get("/api/statistics", date_from="2026-01-01", date_to="2026-10-01")["my_review_activity"]
        self.assertEqual(
            data["totals"], {"reviews": 3, "prs": 2, "approvals": 1, "changes_requested": 1, "commented": 1}
        )
        self.assertEqual(data["granularity"], "month")
        self.assertEqual({b["start"]: b["reviews"] for b in data["buckets"]}["2026-09-01"], 3)
        self.assertEqual(sum(b["reviews"] for b in data["buckets"]), 3)
        # all time also includes last year's comment; other people's reviews and my own PRs never count
        self.assertEqual(self.get("/api/statistics")["my_review_activity"]["totals"]["reviews"], 4)
        by_repo = self.get("/api/statistics", repository=self.other_repo.id)["my_review_activity"]["totals"]
        self.assertEqual((by_repo["reviews"], by_repo["changes_requested"]), (1, 1))
        # the review activity does not leak into the user's own PR counts
        self.assertEqual(self.get("/api/statistics")["counts"]["total"], 1)

    def test_pagination(self):
        self.seed_reviews()
        page = self.mine(date_from="2025-01-01", page_size=2, page=2)
        self.assertEqual((page["count"], page["pages"], page["page"]), (3, 2, 2))
        self.assertEqual([r["number"] for r in page["results"]], [13])

    def test_a_newer_decision_replaces_an_older_one_and_dismissals_reset_it(self):
        self.theirs(40, reviews=[
            f.review_payload(7, f.ME, "CHANGES_REQUESTED", "2026-09-20T10:00:00Z"),
            f.review_payload(8, f.ME, "APPROVED", "2026-09-22T10:00:00Z"),
        ])
        self.theirs(41, reviews=[
            f.review_payload(9, f.ME, "APPROVED", "2026-09-20T10:00:00Z"),
            f.review_payload(10, f.ME, "DISMISSED", "2026-09-21T10:00:00Z"),
        ])
        got = {r["number"]: r["status"] for r in self.mine(date_from="2026-01-01")["results"]}
        self.assertEqual(got, {40: "approved", 41: "commented"})

    def test_a_pr_with_my_review_re_requested_is_pending_and_reviewed(self):
        self.theirs(50, requested=[f.ME], reviews=[f.review_payload(11, f.ME, "CHANGES_REQUESTED", "2026-09-20T10:00:00Z")],
                    timeline=[f.timeline_event("review_requested", "2026-09-25T10:00:00Z", requested_reviewer=f.ME)])
        data = self.mine(date_from="2026-01-01")
        self.assertEqual(data["pending"]["count"], 1)
        self.assertEqual([(r["number"], r["status"]) for r in data["results"]], [(50, "changes_requested")])

    def test_closed_and_draft_prs_are_not_pending(self):
        self.theirs(60, requested=[f.ME], draft=True)
        self.theirs(61, requested=[f.ME], state="closed", closed_at="2026-09-25T10:00:00Z")
        self.assertEqual(self.mine()["pending"]["count"], 0)

    def test_review_prs_never_leak_into_my_own_views(self):
        self.seed_reviews()
        prs = self.get("/api/prs")
        self.assertEqual([r["number"] for r in prs["results"]], [30])
        stats = self.get("/api/statistics")
        self.assertEqual((stats["counts"]["total"], stats["period"]["created"]), (1, 1))
        self.assertEqual(stats["open_prs"], 1)
        repo = [r for r in self.get("/api/repositories")["results"] if r["id"] == self.repo.id][0]
        self.assertEqual(repo["pr_count"], 5)  # repositories count every PR in the organization
        self.assertEqual(self.get("/api/import/status")["totals"]["pull_requests"], 7)
        # ... but any PR in the organization can be opened.
        reviewed = self.repo.pull_requests.get(number=11)
        self.assertEqual(self.client.get("/api/prs/%d" % reviewed.id).status_code, 200)

    def test_users_only_see_their_own_review_data(self):
        self.seed_reviews()
        client = APIClient()
        client.force_login(f.make_member(f.make_org(login="globex", org_id=501, installation_id=701), f.make_user("mallory", 666)))
        data = client.get("/api/my-reviews", {"date_from": "2020-01-01"}).json()
        self.assertEqual((data["count"], data["pending"]["count"]), (0, 0))

    def test_inactive_repositories_are_hidden(self):
        self.seed_reviews()
        self.other_repo.is_active = False
        self.other_repo.save()
        self.assertEqual(self.mine()["pending"]["count"], 1)

    def test_importing_flag_follows_the_repository_import(self):
        self.assertFalse(self.mine()["importing"])
        self.repo.import_status = "running"
        self.repo.save()
        self.assertTrue(self.mine()["importing"])

    def test_author_filter_and_options(self):
        self.seed_reviews()
        data = self.mine(date_from="2020-01-01")
        self.assertEqual(data["authors"], ["john-doe", "sarah"])
        only_sarah = self.mine(date_from="2020-01-01", author="sarah")
        self.assertEqual({r["author_login"] for r in only_sarah["results"]}, {"sarah"})
        self.assertGreater(only_sarah["count"], 0)

    def test_teammates_review_data_is_their_own(self):
        self.seed_reviews()
        teammate = f.make_member(self.org, f.make_user("john-doe", 2))
        client = APIClient()
        client.force_login(teammate)
        data = client.get("/api/my-reviews", {"date_from": "2020-01-01"}).json()
        self.assertEqual(data["pending"]["count"], 0)  # nothing was asked of John
        self.assertEqual(data["activity"]["reviewed"], 0)
