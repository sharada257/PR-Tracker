from unittest import mock

from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, OrganizationMembership
from apps.accounts.services import sync_memberships
from apps.github.tests import factories as f

from .test_api import ApiTestCase


class AdminTestCase(ApiTestCase):
    """octo (self.user) is the admin. john-doe and sarah are members with their own PRs and reviews."""

    def setUp(self):
        super().setUp()
        self.john = f.make_member(self.org, f.make_user("john-doe", 2))
        self.sarah = f.make_member(self.org, f.make_user("sarah", 3))
        self.add(number=10, author=f.JOHN, title="John's feature", created="2026-09-10T09:00:00Z",
                 reviews=[f.review_payload(1, f.SARAH, "APPROVED", "2026-09-11T09:00:00Z")])
        self.add(number=11, author=f.SARAH, title="Sarah's fix", created="2026-09-12T09:00:00Z", requested=[f.JOHN])
        self.add(number=12, title="Octo's PR", created="2026-09-13T09:00:00Z")

    def as_user(self, user):
        client = APIClient()
        client.force_login(user)
        return client


class ViewingOtherPeopleTests(AdminTestCase):
    def numbers(self, client, **params):
        response = client.get("/api/prs", params)
        self.assertEqual(response.status_code, 200, response.content)
        return sorted(r["number"] for r in response.json()["results"])

    def test_admin_defaults_to_own_prs_and_can_view_everyone_or_one_member(self):
        self.assertEqual(self.numbers(self.client), [12])
        self.assertEqual(self.numbers(self.client, scope="all"), [10, 11, 12])
        self.assertEqual(self.numbers(self.client, member=self.john.id), [10])
        self.assertEqual(self.numbers(self.client, member=self.user.id), [12])

    def test_members_cannot_view_everyone_or_other_members(self):
        client = self.as_user(self.john)
        self.assertEqual(self.numbers(client), [10])
        self.assertEqual(client.get("/api/prs", {"scope": "all"}).status_code, 403)
        self.assertEqual(client.get("/api/prs", {"member": self.sarah.id}).status_code, 403)
        self.assertEqual(client.get("/api/statistics", {"scope": "all"}).status_code, 403)
        self.assertEqual(client.get("/api/statistics", {"member": self.sarah.id}).status_code, 403)
        self.assertEqual(client.get("/api/my-reviews", {"member": self.sarah.id}).status_code, 403)
        # asking for yourself explicitly is fine
        self.assertEqual(self.numbers(client, member=self.john.id), [10])

    def test_unknown_or_revoked_member_is_not_found(self):
        self.assertEqual(self.client.get("/api/prs", {"member": 99999}).status_code, 404)
        self.assertEqual(self.client.get("/api/prs", {"member": "abc"}).status_code, 404)
        f.make_member(self.org, self.sarah, status="REVOKED")
        self.assertEqual(self.client.get("/api/prs", {"member": self.sarah.id}).status_code, 404)

    def test_statistics_and_my_reviews_can_be_viewed_as_a_member(self):
        stats = self.get("/api/statistics", member=self.john.id)
        self.assertEqual(stats["counts"]["total"], 1)
        # John was asked to review Sarah's PR: pending for John, not for the admin.
        self.assertEqual(self.get("/api/my-reviews", member=self.john.id)["pending"]["count"], 1)
        self.assertEqual(self.get("/api/my-reviews")["pending"]["count"], 0)
        sarah = self.get("/api/statistics", member=self.sarah.id)["my_review_activity"]["totals"]
        self.assertEqual((sarah["reviews"], sarah["approvals"]), (1, 1))

    def test_filter_options_list_members_only_for_admins(self):
        options = self.get("/api/filters")
        self.assertEqual({m["github_username"] for m in options["members"]}, {"octo", "john-doe", "sarah"})
        member_options = self.as_user(self.john).get("/api/filters").json()
        self.assertEqual((member_options["members"], member_options["authors"]), ([], []))

    def test_any_pr_can_still_be_opened_by_members(self):
        pr = self.repo.pull_requests.get(number=11)
        self.assertEqual(self.as_user(self.john).get("/api/prs/%d" % pr.id).status_code, 200)


class MemberManagementTests(AdminTestCase):
    def patch(self, user, **body):
        return self.client.patch("/api/organization/members/%d" % user.id, body, format="json")

    def test_only_admins_see_and_manage_members(self):
        client = self.as_user(self.john)
        self.assertEqual(client.get("/api/organization/members").status_code, 403)
        self.assertEqual(client.patch("/api/organization/members/%d" % self.sarah.id, {"role": "ADMIN"}, format="json").status_code, 403)
        self.assertEqual(client.get("/api/organization/overview").status_code, 403)
        rows = self.get("/api/organization/members")
        self.assertEqual([r["github_username"] for r in rows][0], "octo")  # active admins first
        self.assertEqual({r["role"] for r in rows}, {"ADMIN", "MEMBER"})

    def test_promote_and_demote(self):
        self.assertEqual(self.patch(self.john, role="ADMIN").json()["role"], "ADMIN")
        self.assertEqual(self.as_user(self.john).get("/api/organization/members").status_code, 200)
        self.assertEqual(self.patch(self.john, role="MEMBER").json()["role"], "MEMBER")
        self.assertTrue(AuditLog.objects.filter(action="member.role_changed").exists())
        self.assertEqual(self.patch(self.john, role="OWNER").status_code, 400)

    def test_the_last_admin_cannot_be_demoted_or_revoked(self):
        self.assertEqual(self.patch(self.user, role="MEMBER").status_code, 409)
        self.assertEqual(self.patch(self.user, active=False).status_code, 409)
        self.patch(self.john, role="ADMIN")
        self.assertEqual(self.patch(self.user, role="MEMBER").status_code, 200)  # another admin exists now

    def test_admins_cannot_revoke_themselves(self):
        self.patch(self.john, role="ADMIN")
        self.assertEqual(self.patch(self.user, active=False).status_code, 409)

    def test_revoked_members_lose_access_and_signing_in_again_does_not_bring_them_back(self):
        self.assertEqual(self.patch(self.sarah, active=False).json()["status"], "REVOKED")
        sarah = self.as_user(self.sarah)
        self.assertEqual(sarah.get("/api/prs").status_code, 403)
        self.assertFalse(sarah.get("/api/auth/me").json()["has_access"])
        # GitHub still shows Sarah the installation, but an admin removed her.
        installation = {"id": 700, "repository_selection": "all", "suspended_at": None,
                        "account": {"id": 500, "login": "acme", "type": "Organization"}}
        sync_memberships(self.sarah, [installation])
        self.assertEqual(sarah.get("/api/prs").status_code, 403)
        self.assertEqual(self.patch(self.sarah, active=True).json()["status"], "ACTIVE")
        self.assertEqual(sarah.get("/api/prs").status_code, 200)
        self.assertTrue(AuditLog.objects.filter(action="member.access_revoked").exists())

    def test_admins_cannot_manage_other_organizations(self):
        other = f.make_org(login="globex", org_id=501, installation_id=701)
        stranger = f.make_member(other, f.make_user("zed", 50))
        self.assertEqual(self.patch(stranger, role="ADMIN").status_code, 404)

    def test_overview_has_one_row_per_member(self):
        rows = {r["github_username"]: r for r in self.get("/api/organization/overview")["members"]}
        self.assertEqual(set(rows), {"octo", "john-doe", "sarah"})
        self.assertEqual((rows["john-doe"]["prs_created"], rows["john-doe"]["pending_reviews"]), (1, 1))
        sarah = rows["sarah"]
        self.assertEqual((sarah["prs_created"], sarah["reviews_submitted"], sarah["approvals"]), (1, 1, 1))
        self.assertEqual(rows["octo"]["prs_created"], 1)
        # date filters apply
        none = self.get("/api/organization/overview", date_from="2025-01-01", date_to="2025-01-31")["members"]
        self.assertEqual(sum(r["prs_created"] + r["reviews_submitted"] for r in none), 0)


class AccessRequestTests(AdminTestCase):
    """A removed person asks to come back; an admin approves or denies."""

    def setUp(self):
        super().setUp()
        self.client.patch(f"/api/organization/members/{self.john.id}", {"active": False}, format="json")
        self.john_client = self.as_user(self.john)

    def ask(self, client=None, message="Please let me back in"):
        return (client or self.john_client).post(
            "/api/auth/access-request", {"organization_id": self.org.id, "message": message}, format="json"
        )

    def pending(self):
        return self.client.get("/api/organization/access-requests").json()

    def test_removed_person_sees_their_organization_and_can_ask_once(self):
        me = self.john_client.get("/api/auth/me").json()
        self.assertFalse(me["has_access"])
        self.assertEqual([o["name"] for o in me["blocked_organizations"]], [self.org.name])
        self.assertIsNone(me["blocked_organizations"][0]["request"])
        self.assertEqual(self.ask().status_code, 201)
        self.assertEqual(self.ask().status_code, 200)  # still the same single open request
        self.assertEqual(self.pending()["pending_count"], 1)
        request = self.john_client.get("/api/auth/me").json()["blocked_organizations"][0]["request"]
        self.assertEqual((request["status"], request["message"]), ("PENDING", "Please let me back in"))

    def test_only_removed_people_can_ask(self):
        self.assertEqual(self.ask(self.as_user(self.sarah)).status_code, 404)  # still has access
        self.assertEqual(self.ask(APIClient()).status_code, 401)

    def test_losing_github_access_is_not_a_removal_so_there_is_nothing_to_request(self):
        OrganizationMembership.objects.filter(user=self.sarah).update(status="REVOKED")  # not access_blocked
        client = self.as_user(self.sarah)
        self.assertEqual(client.get("/api/auth/me").json()["blocked_organizations"], [])
        self.assertEqual(self.ask(client).status_code, 404)

    def test_approving_restores_access_as_a_plain_member(self):
        self.ask()
        request_id = self.pending()["results"][0]["id"]
        response = self.client.patch(f"/api/organization/access-requests/{request_id}", {"decision": "approve"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        me = self.john_client.get("/api/auth/me").json()
        self.assertTrue(me["has_access"])
        self.assertEqual(me["organization"]["role"], "MEMBER")
        self.assertEqual(self.pending()["pending_count"], 0)
        self.assertTrue(AuditLog.objects.filter(action="access.approved").exists())

    def test_denying_keeps_them_out_and_they_may_ask_again(self):
        self.ask()
        request_id = self.pending()["results"][0]["id"]
        self.client.patch(f"/api/organization/access-requests/{request_id}", {"decision": "deny"}, format="json")
        me = self.john_client.get("/api/auth/me").json()
        self.assertFalse(me["has_access"])
        self.assertEqual(me["blocked_organizations"][0]["request"]["status"], "DENIED")
        self.assertEqual(self.ask().status_code, 201)
        self.assertEqual(self.pending()["pending_count"], 1)

    def test_a_request_can_only_be_decided_once(self):
        self.ask()
        request_id = self.pending()["results"][0]["id"]
        url = f"/api/organization/access-requests/{request_id}"
        self.client.patch(url, {"decision": "deny"}, format="json")
        self.assertEqual(self.client.patch(url, {"decision": "approve"}, format="json").status_code, 409)
        self.assertEqual(self.client.patch(url, {"decision": "maybe"}, format="json").status_code, 400)

    def test_restoring_from_the_members_page_settles_the_open_request(self):
        self.ask()
        self.client.patch(f"/api/organization/members/{self.john.id}", {"active": True}, format="json")
        self.assertEqual(self.pending()["pending_count"], 0)
        self.assertEqual(self.pending()["results"][0]["status"], "APPROVED")

    def test_members_cannot_see_or_decide_requests(self):
        self.ask()
        request_id = self.pending()["results"][0]["id"]
        member = self.as_user(self.sarah)
        self.assertEqual(member.get("/api/organization/access-requests").status_code, 403)
        self.assertEqual(member.patch(f"/api/organization/access-requests/{request_id}", {"decision": "approve"}, format="json").status_code, 403)
        self.assertFalse(self.john_client.get("/api/auth/me").json()["has_access"])
