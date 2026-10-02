"""Credential handling: GitHub App JWTs, installation tokens, OAuth.

Installation tokens are short lived (1 hour) and cached only until shortly
before they expire. They are never stored in the database or sent to the frontend.
The signed-in user's OAuth token is used once at login and then discarded.
"""
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import jwt
import requests
from django.conf import settings
from django.core.cache import cache

from .client import GitHubAuthError, GitHubClient, GitHubError


def app_configured():
    return bool(settings.GITHUB_APP_ID and _private_key())


def oauth_configured():
    return bool(settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET)


def _private_key():
    if settings.GITHUB_PRIVATE_KEY:
        return settings.GITHUB_PRIVATE_KEY
    if settings.GITHUB_PRIVATE_KEY_PATH:
        try:
            with open(settings.GITHUB_PRIVATE_KEY_PATH) as handle:
                return handle.read()
        except OSError:
            return ""
    return ""


def app_jwt():
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 9 * 60, "iss": settings.GITHUB_APP_ID}
    return jwt.encode(payload, _private_key(), algorithm="RS256")


def get_installation_token(installation_id):
    cache_key = "gh-installation-token:%s" % installation_id
    token = cache.get(cache_key)
    if token:
        return token
    client = GitHubClient(token=app_jwt())
    data = client.request("POST", "/app/installations/%s/access_tokens" % installation_id).json()
    token = data["token"]
    expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    ttl = int((expires_at - datetime.now(timezone.utc)).total_seconds()) - 300
    if ttl > 0:
        cache.set(cache_key, token, ttl)
    return token


def client_for_installation(installation):
    if not installation.usable:
        raise GitHubAuthError("The GitHub App installation for %s is not active." % installation.github_account_login)
    return GitHubClient(token=get_installation_token(installation.github_installation_id))


def client_for_organization(organization):
    installation = getattr(organization, "installation", None)
    if installation is None:
        raise GitHubAuthError("%s has no GitHub App installation." % organization)
    return client_for_installation(installation)


def client_for_repository(repo):
    return client_for_organization(repo.organization)


# -- OAuth --------------------------------------------------------------------
def authorize_url(redirect_uri, state, scope=None):
    params = {"client_id": settings.GITHUB_CLIENT_ID, "redirect_uri": redirect_uri, "state": state}
    if scope:
        params["scope"] = scope
    query = urlencode(params)
    return "%s/login/oauth/authorize?%s" % (settings.GITHUB_WEB_URL, query)


def exchange_code(code, redirect_uri):
    try:
        response = requests.post(
            "%s/login/oauth/access_token" % settings.GITHUB_WEB_URL,
            data={
                "client_id": settings.GITHUB_CLIENT_ID,
                "client_secret": settings.GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            headers={"Accept": "application/json"},
            timeout=(10, 30),
        )
        data = response.json() if response.content else {}
    except (requests.RequestException, ValueError) as exc:
        raise GitHubError("Could not complete the GitHub sign-in.") from exc
    token = data.get("access_token")
    if not token:
        raise GitHubError("GitHub did not return an access token: %s" % data.get("error_description", "unknown error"))
    return token


def install_url():
    if settings.GITHUB_APP_SLUG:
        return "%s/apps/%s/installations/new" % (settings.GITHUB_WEB_URL, settings.GITHUB_APP_SLUG)
    return ""
