"""Minimal Slack Web API client."""
import requests
from django.conf import settings

API = "https://slack.com/api/"
AUTH_ERRORS = {"invalid_auth", "token_revoked", "account_inactive", "not_authed", "token_expired", "team_disabled"}


class SlackError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code

    @property
    def is_auth_error(self):
        return self.code in AUTH_ERRORS


class SlackRateLimited(SlackError):
    def __init__(self, retry_after=30):
        super().__init__("ratelimited")
        self.retry_after = retry_after


def configured():
    return bool(settings.SLACK_CLIENT_ID and settings.SLACK_CLIENT_SECRET)


def redirect_uri():
    return (settings.SLACK_REDIRECT_BASE or settings.PUBLIC_URL) + "/api/slack/callback"


def authorize_url(state):
    from urllib.parse import urlencode

    query = urlencode(
        {
            "client_id": settings.SLACK_CLIENT_ID,
            "scope": "chat:write,im:write,users:read,users:read.email",
            "redirect_uri": redirect_uri(),
            "state": state,
        }
    )
    return "https://slack.com/oauth/v2/authorize?" + query


def _handle(response):
    if response.status_code == 429:
        raise SlackRateLimited(int(response.headers.get("Retry-After", 30)))
    try:
        data = response.json()
    except ValueError as exc:
        raise SlackError("bad_response") from exc
    if not data.get("ok"):
        raise SlackError(data.get("error", "unknown_error"))
    return data


def exchange_code(code):
    """Trade the OAuth code for the workspace's bot token."""
    response = requests.post(
        API + "oauth.v2.access",
        data={"code": code, "redirect_uri": redirect_uri()},
        auth=(settings.SLACK_CLIENT_ID, settings.SLACK_CLIENT_SECRET),
        timeout=15,
    )
    return _handle(response)


class SlackClient:
    def __init__(self, token):
        self.token = token

    def call(self, method, **payload):
        response = requests.post(
            API + method,
            json=payload,
            headers={"Authorization": "Bearer " + self.token},
            timeout=15,
        )
        return _handle(response)

    def users(self):
        """Real, active people in the workspace (no bots, no deactivated accounts)."""
        people, cursor = [], None
        for _ in range(20):
            data = self.call("users.list", limit=200, **({"cursor": cursor} if cursor else {}))
            for member in data.get("members", []):
                if member.get("deleted") or member.get("is_bot") or member.get("id") == "USLACKBOT":
                    continue
                profile = member.get("profile") or {}
                people.append(
                    {
                        "id": member["id"],
                        "name": member.get("name", ""),
                        "real_name": member.get("real_name") or profile.get("real_name") or "",
                        "display_name": profile.get("display_name") or "",
                        "email": (profile.get("email") or "").lower(),
                        "avatar_url": profile.get("image_48") or "",
                    }
                )
            cursor = (data.get("response_metadata") or {}).get("next_cursor")
            if not cursor:
                break
        people.sort(key=lambda p: (p["real_name"] or p["name"]).lower())
        return people

    def post(self, slack_user_id, text, blocks=None):
        # Posting to a user id opens (or reuses) the bot's direct message with them.
        payload = {"channel": slack_user_id, "text": text, "unfurl_links": False}
        if blocks:
            payload["blocks"] = blocks
        return self.call("chat.postMessage", **payload)

    def revoke(self):
        try:
            self.call("auth.revoke")
        except SlackError:
            pass
