"""Minimal GitHub REST client with pagination and rate-limit awareness."""
import logging
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

API_VERSION = "2022-11-28"


class GitHubError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class GitHubAuthError(GitHubError):
    """Bad / expired / revoked credentials."""


class GitHubNotFound(GitHubError):
    pass


class GitHubRateLimited(GitHubError):
    def __init__(self, message, reset_at):
        super().__init__(message, status_code=403)
        self.reset_at = reset_at

    @property
    def retry_after_seconds(self):
        return max(int((self.reset_at - datetime.now(timezone.utc)).total_seconds()), 1) + 5


def build_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"]),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


class GitHubClient:
    def __init__(self, token=None, base_url=None, session=None):
        self.token = token
        self.base_url = (base_url or settings.GITHUB_API_URL).rstrip("/")
        self.session = session or build_session()

    # -- plumbing -----------------------------------------------------------
    def _headers(self, extra=None):
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "pr-tracker",
        }
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token
        if extra:
            headers.update(extra)
        return headers

    def request(self, method, path, params=None, json=None, headers=None):
        url = path if path.startswith("http") else self.base_url + path
        try:
            response = self.session.request(
                method, url, params=params, json=json, headers=self._headers(headers), timeout=(10, 30)
            )
        except requests.RequestException as exc:
            raise GitHubError("Could not reach GitHub: %s" % exc.__class__.__name__) from exc
        self._raise_for_status(response)
        return response

    @staticmethod
    def _raise_for_status(response):
        status = response.status_code
        if status < 400:
            return
        try:
            message = response.json().get("message", "")
        except ValueError:
            message = response.text[:200]
        if status in (403, 429):
            lowered = message.lower()
            remaining = response.headers.get("x-ratelimit-remaining")
            retry_after = response.headers.get("retry-after")
            if remaining == "0" or retry_after or "rate limit" in lowered:
                reset_at = None
                if retry_after and retry_after.isdigit():
                    reset_at = datetime.now(timezone.utc) + timedelta(seconds=int(retry_after))
                elif response.headers.get("x-ratelimit-reset", "").isdigit():
                    reset_at = datetime.fromtimestamp(int(response.headers["x-ratelimit-reset"]), tz=timezone.utc)
                else:
                    reset_at = datetime.now(timezone.utc) + timedelta(seconds=60)
                raise GitHubRateLimited(message or "GitHub rate limit reached", reset_at)
        if status == 401:
            raise GitHubAuthError(message or "Bad credentials", status)
        if status == 404:
            raise GitHubNotFound(message or "Not found", status)
        raise GitHubError("GitHub API error %s: %s" % (status, message), status)

    def get_json(self, path, params=None, headers=None):
        return self.request("GET", path, params=params, headers=headers).json()

    def paginate(self, path, params=None, key=None, max_pages=None, per_page=100):
        """Yield items across pages. ``key`` unwraps envelope responses."""
        params = dict(params or {})
        params.setdefault("per_page", per_page)
        url = path
        pages = 0
        while url:
            response = self.request("GET", url, params=params if pages == 0 else None)
            data = response.json()
            items = data.get(key, []) if key else data
            for item in items:
                yield item
            pages += 1
            if max_pages and pages >= max_pages:
                return
            url = response.links.get("next", {}).get("url")

    # -- endpoints ----------------------------------------------------------
    def get_authenticated_user(self):
        return self.get_json("/user")

    def list_user_installations(self):
        return list(self.paginate("/user/installations", key="installations"))

    def list_installation_repositories(self, max_pages=40):
        return list(self.paginate("/installation/repositories", key="repositories", max_pages=max_pages))

    def list_user_repositories(self, max_pages=40):
        params = {"sort": "pushed", "affiliation": "owner,collaborator,organization_member"}
        return list(self.paginate("/user/repos", params=params, max_pages=max_pages))

    def search_issues(self, query, sort="created", order="asc", per_page=50, page=1):
        return self.get_json(
            "/search/issues", params={"q": query, "sort": sort, "order": order, "per_page": per_page, "page": page}
        )

    def get_pull(self, full_name, number):
        return self.get_json("/repos/%s/pulls/%s" % (full_name, number))

    def list_reviews(self, full_name, number):
        return list(self.paginate("/repos/%s/pulls/%s/reviews" % (full_name, number)))

    def list_commits(self, full_name, number):
        return list(self.paginate("/repos/%s/pulls/%s/commits" % (full_name, number), max_pages=3))

    def list_timeline(self, full_name, number):
        return list(self.paginate("/repos/%s/issues/%s/timeline" % (full_name, number), max_pages=10))