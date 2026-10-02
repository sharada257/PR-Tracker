from unittest import mock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import AuditLog, Organization, OrganizationMembership, User
from apps.accounts.services import sync_memberships
from apps.github.client import GitHubClient, GitHubError, GitHubRateLimited
from apps.github.models import GitHubInstallation, SyncJob
from apps.github.tests import factories as f

GH_USER = {"id": 4242, "login": "octo", "name": "Octo Cat", "avatar_url": "https://a/4242"}


def installation(inst_id=700, account_id=500, login="acme", **extra):
    return {"id": inst_id, "repository_selection": "all", "suspended_at": None,
            "account": {"id": account_id, "login": login, "type": "Organization", "avatar_url": "https://a/org"}, **extra}


class MembershipTests(TestCase):
    def test_first_user_becomes_admin_and_the_next_a_member(self):
        first, second = f.make_user("a", 1), f.make_user("b", 2)
        needs_sync = sync_memberships(first, [installation()])
        sync_memberships(second, [installation()])
        org = Organization.objects.get(github_organization_id=500)
        roles = {m.user.github_username: m.role for m in org.memberships.all()}
        self.assertEqual(roles, {"a": "ADMIN", "b": "MEMBER"})
        self.assertEqual(needs_sync, [org])
        self.assertEqual(GitHubInstallation.objects.get().github_installation_id, 700)

    def test_initial_sync_is_only_requested_once(self):
        user = f.make_user("a", 1)
        org = sync_memberships(user, [installation()])[0]
        SyncJob.objects.create(organization=org, type=SyncJob.TYPE_REPOSITORIES, status=SyncJob.COMPLETED)
        self.assertEqual(sync_memberships(user, [installation()]), [])

    def test_losing_access_on_github_revokes_membership_and_regaining_restores_it(self):
        user = f.make_user("a", 1)
        sync_memberships(user, [installation(), installation(701, 501, "globex")])
        sync_memberships(user, [installation()])  # globex vanished from the user's installations
        statuses = {m.organization.github_organization_login: m.status for m in user.memberships.all()}
        self.assertEqual(statuses, {"acme": "ACTIVE", "globex": "REVOKED"})
        sync_memberships(user, [installation(), installation(701, 501, "globex")])
        self.assertTrue(all(m.status == "ACTIVE" for m in user.memberships.all()))
        self.assertEqual(user.memberships.get(organization__github_organization_login="acme").role, "ADMIN")

    def test_user_with_no_installations_has_no_memberships(self):
        user = f.make_user("a", 1)
        self.assertEqual(sync_memberships(user, []), [])
        self.assertFalse(user.memberships.exists())


@override_settings(GITHUB_CLIENT_ID="cid", GITHUB_CLIENT_SECRET="secret")
class OAuthTests(TestCase):
    def start(self, client):
        response = client.get("/api/auth/github/login")
        self.assertEqual(response.status_code, 302)
        self.assertIn("github.com/login/oauth/authorize", response["Location"])
        return client.session["github_oauth_state"]

    def callback(self, state, installations):
        with mock.patch("apps.accounts.views.gh_auth.exchange_code", return_value="ghu_tok"), mock.patch.object(
            GitHubClient, "get_authenticated_user", return_value=GH_USER
        ), mock.patch.object(GitHubClient, "list_user_installations", return_value=installations), mock.patch(
            "apps.accounts.views.start_organization_sync"
        ) as start:
            response = self.client.get("/api/auth/github/callback", {"code": "c", "state": state})
        return response, start

    def test_state_mismatch_is_rejected(self):
        self.start(self.client)
        response = self.client.get("/api/auth/github/callback", {"code": "c", "state": "forged"})
        self.assertIn("error=invalid_state", response["Location"])
        self.assertEqual(User.objects.count(), 0)

    def test_callback_without_prior_login_attempt_is_rejected(self):
        response = self.client.get("/api/auth/github/callback", {"code": "c", "state": "x"})
        self.assertIn("error=invalid_state", response["Location"])

    def test_sign_in_creates_user_org_and_admin_membership_and_stores_no_token(self):
        state = self.start(self.client)
        response, start = self.callback(state, [installation()])
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(github_user_id=4242)
        self.assertFalse(hasattr(user, "github_token_encrypted"))
        self.assertNotIn("ghu_tok", str(list(User.objects.values())))
        me = self.client.get("/api/auth/me").json()
        self.assertEqual(me["user"]["github_username"], "octo")
        self.assertTrue(me["has_access"])
        self.assertEqual((me["organization"]["login"], me["organization"]["role"]), ("acme", "ADMIN"))
        start.assert_called_once()  # brand-new organization -> initial sync
        self.assertTrue(AuditLog.objects.filter(action="auth.login", user=user).exists())

    def test_second_user_in_the_same_org_is_a_member_and_triggers_no_new_sync(self):
        org = f.make_org()
        SyncJob.objects.create(organization=org, type=SyncJob.TYPE_REPOSITORIES, status=SyncJob.COMPLETED)
        f.make_member(org, f.make_user("admin", 1), role="ADMIN")
        state = self.start(self.client)
        response, start = self.callback(state, [installation()])
        self.assertEqual(self.client.get("/api/auth/me").json()["organization"]["role"], "MEMBER")
        start.assert_not_called()

    def test_user_with_no_installation_signs_in_but_has_no_access(self):
        state = self.start(self.client)
        self.callback(state, [])
        me = self.client.get("/api/auth/me").json()
        self.assertTrue(me["authenticated"])
        self.assertFalse(me["has_access"])
        self.assertEqual(self.client.get("/api/prs").status_code, 403)

    def test_github_failure_redirects_with_error(self):
        state = self.start(self.client)
        with mock.patch("apps.accounts.views.gh_auth.exchange_code", side_effect=GitHubError("nope")):
            response = self.client.get("/api/auth/github/callback", {"code": "c", "state": state})
        self.assertIn("error=github_error", response["Location"])

    def test_oauth_not_configured_redirects_with_error(self):
        with override_settings(GITHUB_CLIENT_ID=""):
            response = self.client.get("/api/auth/github/login")
        self.assertIn("error=oauth_not_configured", response["Location"])

    def test_personal_access_token_login_no_longer_exists(self):
        self.assertEqual(APIClient().post("/api/auth/token", {"token": "ghp_x"}, format="json").status_code, 404)
        self.assertNotIn("pat_login", self.client.get("/api/auth/config").json())


class CsrfTests(TestCase):
    def test_state_changing_requests_require_csrf_token(self):
        user = f.make_member(f.make_org(), f.make_user())
        client = APIClient(enforce_csrf_checks=True)
        client.force_login(user)
        self.assertEqual(client.post("/api/auth/logout").status_code, 403)
        self.assertEqual(client.post("/api/github/sync", {}, format="json").status_code, 403)


class ClientTests(TestCase):
    def fake_response(self, status, headers=None, message="x"):
        response = mock.Mock(status_code=status, headers=headers or {}, text=message)
        response.json.return_value = {"message": message}
        return response

    def test_primary_rate_limit(self):
        response = self.fake_response(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "4102444800"})
        with self.assertRaises(GitHubRateLimited) as ctx:
            GitHubClient._raise_for_status(response)
        self.assertGreater(ctx.exception.retry_after_seconds, 60)

    def test_secondary_rate_limit_uses_retry_after(self):
        response = self.fake_response(403, {"retry-after": "30"}, "You have exceeded a secondary rate limit")
        with self.assertRaises(GitHubRateLimited) as ctx:
            GitHubClient._raise_for_status(response)
        self.assertLessEqual(ctx.exception.retry_after_seconds, 40)

    def test_plain_forbidden_is_not_a_rate_limit(self):
        response = self.fake_response(403, {"x-ratelimit-remaining": "42"}, "Resource not accessible")
        with self.assertRaises(GitHubError) as ctx:
            GitHubClient._raise_for_status(response)
        self.assertNotIsInstance(ctx.exception, GitHubRateLimited)

    def test_pagination_follows_link_headers(self):
        pages = [
            mock.Mock(status_code=200, headers={}, links={"next": {"url": "https://api.github.com/x?page=2"}},
                      json=lambda: [1, 2]),
            mock.Mock(status_code=200, headers={}, links={}, json=lambda: [3]),
        ]
        session = mock.Mock()
        session.request.side_effect = pages
        client = GitHubClient(token="t", session=session)
        self.assertEqual(list(client.paginate("/x")), [1, 2, 3])
        self.assertEqual(session.request.call_count, 2)
        sent = session.request.call_args_list[0][1]["headers"]
        self.assertEqual(sent["Authorization"], "Bearer t")


@override_settings(ALLOW_DEMO_LOGIN=True)
class DemoLoginTests(TestCase):
    """The login page offers one demo button per role."""

    def setUp(self):
        self.org = f.make_org()
        f.make_member(self.org, f.make_user("demo-dev", 9_000_001), role=OrganizationMembership.ROLE_ADMIN)
        f.make_member(self.org, f.make_user("sarah-smith", 9_100_002))

    def sign_in(self, role=None):
        client = APIClient()
        response = client.post("/api/auth/demo", {"role": role} if role else {}, format="json")
        return client, response

    def test_admin_button_signs_in_as_admin(self):
        client, response = self.sign_in("admin")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/auth/me").json()["organization"]["role"], "ADMIN")

    def test_admin_button_works_again_after_the_demo_admin_demoted_themselves(self):
        OrganizationMembership.objects.filter(user__github_user_id=9_000_001).update(role="MEMBER")
        client, _ = self.sign_in("admin")
        self.assertEqual(client.get("/api/auth/me").json()["organization"]["role"], "ADMIN")

    def test_developer_button_signs_in_as_plain_member(self):
        client, response = self.sign_in("member")
        self.assertEqual(response.status_code, 200)
        me = client.get("/api/auth/me").json()
        self.assertEqual(me["user"]["github_username"], "sarah-smith")
        self.assertEqual(me["organization"]["role"], "MEMBER")
        self.assertEqual(client.get("/api/organization/members").status_code, 403)

    def test_defaults_to_admin_and_rejects_unknown_roles(self):
        self.assertEqual(self.sign_in()[1].status_code, 200)
        self.assertEqual(self.sign_in("root")[1].status_code, 404)

    @override_settings(ALLOW_DEMO_LOGIN=False)
    def test_disabled_outside_demo_mode(self):
        self.assertEqual(self.sign_in("admin")[1].status_code, 404)


class InstallerAdminTests(TestCase):
    """Whoever completed the GitHub App setup is the first admin, not whoever signs in first."""

    def test_installer_is_admin_even_if_someone_else_signs_in_first(self):
        installer, bob = f.make_user("alice", 1), f.make_user("bob", 2)
        f.make_org()
        GitHubInstallation.objects.filter(github_installation_id=700).update(installed_by_github_id=1)
        sync_memberships(bob, [installation()])
        sync_memberships(installer, [installation()])
        roles = {m.user.github_username: m.role for m in Organization.objects.get().memberships.all()}
        self.assertEqual(roles, {"bob": "MEMBER", "alice": "ADMIN"})

    def test_installer_returning_from_github_is_recorded_and_made_admin(self):
        alice = f.make_user("alice", 1)
        sync_memberships(alice, [installation()], installed=700)
        self.assertEqual(GitHubInstallation.objects.get().installed_by_github_id, 1)
        self.assertEqual(alice.memberships.get().role, "ADMIN")

    def test_installer_is_not_overwritten_by_a_later_claim(self):
        f.make_org()
        GitHubInstallation.objects.filter(github_installation_id=700).update(installed_by_github_id=1)
        mallory = f.make_user("mallory", 9)
        sync_memberships(mallory, [installation()], installed=700)
        self.assertEqual(GitHubInstallation.objects.get().installed_by_github_id, 1)
        self.assertEqual(mallory.memberships.get().role, "MEMBER")

    def test_installation_webhook_records_who_installed(self):
        from apps.github import processing

        payload = {"action": "created", "sender": {"id": 77}, "installation": installation(800, 600, "initech")}
        processing._process_installation(payload, "created")
        self.assertEqual(GitHubInstallation.objects.get(github_installation_id=800).installed_by_github_id, 77)


@override_settings(GITHUB_CLIENT_ID="cid", GITHUB_CLIENT_SECRET="secret", GITHUB_APP_SLUG="prlens")
class SetupFlowTests(TestCase):
    def test_install_redirects_to_github_with_state_we_can_verify_later(self):
        response = self.client.get("/api/auth/github/install")
        self.assertEqual(response.status_code, 302)
        state = self.client.session["github_oauth_state"]
        self.assertIn("/apps/prlens/installations/new?state=" + state, response["Location"])

    def test_returning_from_install_without_oauth_signs_the_user_in_first(self):
        response = self.client.get("/api/auth/github/callback", {"installation_id": "700", "setup_action": "install"})
        self.assertTrue(response["Location"].endswith("/api/auth/github/login"))
        self.assertEqual(self.client.session["github_pending_installation"], "700")

    def test_organization_choice_is_requested_only_with_several_organizations(self):
        user = f.make_user("alice", 1)
        sync_memberships(user, [installation()])
        client = APIClient()
        client.force_login(user)
        self.assertFalse(client.get("/api/auth/me").json()["needs_organization_choice"])
        sync_memberships(user, [installation(), installation(701, 501, "globex")])
        client = APIClient()
        client.force_login(user)
        me = client.get("/api/auth/me").json()
        self.assertTrue(me["needs_organization_choice"])
        org_id = me["organizations"][1]["id"]
        client.post("/api/auth/organization", {"organization_id": org_id}, format="json")
        me = client.get("/api/auth/me").json()
        self.assertFalse(me["needs_organization_choice"])
        self.assertEqual(me["organization"]["id"], org_id)
