"""Translate GitHub payloads (REST and webhook) into plain internal dicts.

The rest of the application only ever sees the shapes returned here, so a change
in GitHub's payloads is contained to this module.
"""
from datetime import datetime, timezone


def parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _user(payload):
    payload = payload or {}
    return {
        "login": payload.get("login") or "",
        "github_id": payload.get("id"),
        "avatar_url": payload.get("avatar_url") or "",
    }


def team_username(owner, team):
    return "%s/%s" % (owner, team.get("slug") or team.get("name") or team.get("id"))


def normalize_repository(payload):
    full_name = payload["full_name"]
    owner, _, name = full_name.partition("/")
    return {
        "github_repository_id": payload["id"],
        "owner": (payload.get("owner") or {}).get("login") or owner,
        "name": payload.get("name") or name,
        "full_name": full_name,
        "url": payload.get("html_url") or "",
        "default_branch": payload.get("default_branch") or "",
        "private": bool(payload.get("private")),
    }


def normalize_pull_request(payload, owner=None):
    """Works for ``GET /pulls/:n`` and the ``pull_request`` webhook object."""
    author = _user(payload.get("user"))
    owner = owner or ((payload.get("base") or {}).get("repo") or {}).get("owner", {}).get("login", "")
    reviewers = []
    for user in payload.get("requested_reviewers") or []:
        reviewers.append({**_user(user), "is_team": False})
    for team in payload.get("requested_teams") or []:
        reviewers.append(
            {"login": team_username(owner, team), "github_id": team.get("id"), "avatar_url": "", "is_team": True}
        )
    return {
        "github_pr_id": payload["id"],
        "number": payload["number"],
        "title": payload.get("title") or "",
        "description": payload.get("body") or "",
        "author_login": author["login"],
        "author_github_id": author["github_id"],
        "author_avatar_url": author["avatar_url"],
        "state": payload.get("state") or "open",
        "draft": bool(payload.get("draft")),
        "url": payload.get("html_url") or "",
        "base_branch": (payload.get("base") or {}).get("ref") or "",
        "head_branch": (payload.get("head") or {}).get("ref") or "",
        "merge_commit_sha": payload.get("merge_commit_sha") if payload.get("merged_at") else "",
        "created_at_github": parse_dt(payload.get("created_at")),
        "updated_at_github": parse_dt(payload.get("updated_at")),
        "closed_at": parse_dt(payload.get("closed_at")),
        "merged_at": parse_dt(payload.get("merged_at")),
        "additions": payload.get("additions") or 0,
        "deletions": payload.get("deletions") or 0,
        "changed_files": payload.get("changed_files") or 0,
        "commits_count": payload.get("commits") or 0,
        "comments_count": (payload.get("comments") or 0) + (payload.get("review_comments") or 0),
        "labels": [
            {"name": label["name"], "color": label.get("color") or ""}
            for label in payload.get("labels") or []
            if label.get("name")
        ],
        "requested_reviewers": [r for r in reviewers if r["login"]],
    }


def normalize_review(payload):
    """Returns ``None`` for reviews that are not submitted (pending drafts)."""
    state = (payload.get("state") or "").upper()
    submitted_at = parse_dt(payload.get("submitted_at"))
    if not payload.get("id") or state == "PENDING" or submitted_at is None:
        return None
    reviewer = _user(payload.get("user"))
    return {
        "github_review_id": payload["id"],
        "reviewer": reviewer["login"],
        "reviewer_github_id": reviewer["github_id"],
        "reviewer_avatar_url": reviewer["avatar_url"],
        "state": state,
        "body": payload.get("body") or "",
        "submitted_at": submitted_at,
        "commit_id": payload.get("commit_id") or "",
    }


def normalize_commits(payload):
    commits = []
    for item in payload:
        commit = item.get("commit") or {}
        date = (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date")
        if not item.get("sha") or not date:
            continue
        commits.append(
            {
                "sha": item["sha"],
                "committed_at": parse_dt(date),
                "author": (item.get("author") or {}).get("login") or (commit.get("author") or {}).get("name") or "",
                "message": (commit.get("message") or "").split("\n", 1)[0][:300],
            }
        )
    return commits


def normalize_timeline(payload, owner):
    """Extract review-request history and lifecycle transitions from an issue timeline."""
    reviewer_events = []
    events = []
    for item in payload:
        kind = item.get("event")
        at = parse_dt(item.get("created_at"))
        if not kind or at is None:
            continue
        if kind in ("review_requested", "review_request_removed"):
            if item.get("requested_reviewer"):
                person = {**_user(item["requested_reviewer"]), "is_team": False}
            elif item.get("requested_team"):
                team = item["requested_team"]
                person = {
                    "login": team_username(owner, team),
                    "github_id": team.get("id"),
                    "avatar_url": "",
                    "is_team": True,
                }
            else:
                continue
            reviewer_events.append(
                {"type": "requested" if kind == "review_requested" else "removed", "at": at, **person}
            )
        elif kind in ("reopened", "ready_for_review", "convert_to_draft"):
            event_type = "converted_to_draft" if kind == "convert_to_draft" else kind
            events.append(
                {
                    "event_type": event_type,
                    "actor": (item.get("actor") or {}).get("login") or "",
                    "at": at,
                    "key": "%s:%s" % (event_type, item.get("id") or at.isoformat()),
                }
            )
    reviewer_events.sort(key=lambda e: e["at"])
    return {"reviewer_events": reviewer_events, "events": events}
