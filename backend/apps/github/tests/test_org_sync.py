from unittest import mock

from django.test import TestCase

from apps.tracker.models import PullRequest, Repository

from .. import sync, tasks
from ..models import SyncJob
from ..views import summarize_sync
from . import factories as f


class OrgClient(f.FakeClient):
    """FakeClient that also lists the installation's repositories."""

    def __init__(self, repositories, **kwargs):
        super().__init__(**kwargs)
        self.repositories = repositories

    def list_installation_repositories(self):
        return self.repositories


def repo_payload(repo_id, full_name):
    return {"id": repo_id, "full_name": full_name, "html_url": "https://github.com/" + full_name,
            "private": True, "default_branch": "main", "owner": {"login": full_name.split("/")[0]}}


class RepositoryDiscoveryTests(TestCase):
    def setUp(self):
        self.org = f.make_org()

    def test_discovery_creates_repositories_and_hides_removed_ones(self):
        gone = f.make_repo(self.org, github_id=1, full_name="acme/old")
        client = OrgClient([repo_payload(2, "acme/new")])
        created, updated = sync.refresh_repositories(self.org, client=client)
        self.assertEqual([r.full_name for r in created], ["acme/new"])
        self.assertEqual(updated, [])
        gone.refresh_from_db()
        self.assertFalse(gone.is_accessible)  # kept, but hidden: GitHub no longer exposes it
        self.assertTrue(Repository.objects.get(full_name="acme/new").is_active)

    def test_rediscovery_restores_access_but_keeps_admin_choice_to_stop_tracking(self):
        repo = f.make_repo(self.org, github_id=2, full_name="acme/new", active=False, is_accessible=False)
        sync.refresh_repositories(self.org, client=OrgClient([repo_payload(2, "acme/new")]))
        repo.refresh_from_db()
        self.assertTrue(repo.is_accessible)
        self.assertFalse(repo.is_active)

    def test_repositories_are_scoped_to_their_organization(self):
        other = f.make_org(login="globex", org_id=501, installation_id=701)
        sync.refresh_repositories(self.org, client=OrgClient([repo_payload(2, "acme/new")]))
        sync.refresh_repositories(other, client=OrgClient([repo_payload(2, "acme/new")]))
        self.assertEqual(Repository.objects.filter(github_repository_id=2).count(), 2)


class OrganizationSyncTests(TestCase):
    def setUp(self):
        self.org = f.make_org()
        self.items = []
        prs = {}
        for n in (1, 2):
            created = "2026-0%d-10T10:00:00Z" % n
            prs[n] = f.pr_payload(number=n, created=created, author=f.JOHN if n == 1 else f.SARAH)
            prs[n]["id"] = 9000 + n
            self.items.append({"number": n, "created_at": created, "updated_at": created})
        self.client = OrgClient([repo_payload(100, "acme/helpdesk")], prs=prs, search_items=self.items)
        patcher = mock.patch("apps.github.auth.client_for_organization", return_value=self.client)
        repo_patcher = mock.patch("apps.github.sync.auth.client_for_repository", return_value=self.client)
        self.addCleanup(patcher.stop)
        self.addCleanup(repo_patcher.stop)
        patcher.start()
        repo_patcher.start()

    def test_first_sync_imports_every_authors_prs_and_records_jobs(self):
        batch = tasks.start_organization_sync(self.org)
        self.assertIsNotNone(batch)
        self.assertEqual(PullRequest.objects.count(), 2)
        self.assertEqual({pr.author_login for pr in PullRequest.objects.all()}, {"john-doe", "sarah"})
        jobs = SyncJob.objects.filter(batch=batch)
        self.assertEqual({j.type for j in jobs}, {SyncJob.TYPE_REPOSITORIES, SyncJob.TYPE_INITIAL})
        self.assertTrue(all(j.status == SyncJob.COMPLETED for j in jobs))
        initial = jobs.get(type=SyncJob.TYPE_INITIAL)
        self.assertEqual((initial.records_processed, initial.records_created, initial.records_updated), (2, 2, 0))

        summary = summarize_sync(self.org)
        self.assertEqual(summary["status"], SyncJob.COMPLETED)
        self.assertEqual(summary["repositories_updated"], 1)
        self.assertEqual(summary["pull_requests_updated"], 2)
        self.assertIsNotNone(summary["last_synced_at"])

    def test_second_sync_is_incremental(self):
        tasks.start_organization_sync(self.org)
        batch = tasks.start_organization_sync(self.org)
        types = set(SyncJob.objects.filter(batch=batch).values_list("type", flat=True))
        self.assertEqual(types, {SyncJob.TYPE_REPOSITORIES, SyncJob.TYPE_INCREMENTAL})

    def test_sync_is_not_started_twice_concurrently(self):
        SyncJob.objects.create(organization=self.org, type=SyncJob.TYPE_REPOSITORIES, status=SyncJob.RUNNING)
        self.assertIsNone(tasks.start_organization_sync(self.org))

    def test_stale_running_jobs_do_not_block_forever(self):
        job = SyncJob.objects.create(organization=self.org, type=SyncJob.TYPE_REPOSITORIES, status=SyncJob.RUNNING)
        SyncJob.objects.filter(pk=job.pk).update(created_at=job.created_at - tasks.JOB_STALE_AFTER * 2)
        self.assertIsNotNone(tasks.start_organization_sync(self.org))

    def test_failure_to_list_repositories_is_recorded(self):
        from ..client import GitHubError

        self.client.list_installation_repositories = mock.Mock(side_effect=GitHubError("boom"))
        batch = tasks.start_organization_sync(self.org)
        job = SyncJob.objects.get(batch=batch)
        self.assertEqual(job.status, SyncJob.FAILED)
        self.assertIn("boom", job.error_message)
        self.assertEqual(summarize_sync(self.org)["status"], SyncJob.FAILED)

    def test_partial_when_import_fails_after_some_progress(self):
        calls = {"n": 0}
        original = self.client.get_pull

        def flaky(full_name, number):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("kaput")
            return original(full_name, number)

        self.client.get_pull = flaky
        batch = tasks.start_organization_sync(self.org)
        job = SyncJob.objects.get(batch=batch, type=SyncJob.TYPE_INITIAL)
        self.assertEqual(job.status, SyncJob.PARTIAL)
        self.assertEqual(job.records_processed, 1)
        self.assertEqual(summarize_sync(self.org)["status"], SyncJob.PARTIAL)

    def test_suspended_organizations_are_skipped_by_the_schedule(self):
        installation = self.org.installation
        installation.active = False
        installation.save()
        self.assertEqual(tasks.reconcile_all(), 0)
        self.assertFalse(SyncJob.objects.exists())
