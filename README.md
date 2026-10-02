# PR Tracker

A personal GitHub pull-request tracker and analytics dashboard.

It imports the PRs **you authored**, keeps them in sync through GitHub webhooks (plus a periodic safety-net
reconciliation), and gives you a searchable, filterable history with reviewer information, per-PR timelines and
engineering metrics. **GitHub is the source of truth**; this app only reads from it and never modifies anything on
GitHub.

| Layer | Tech |
|---|---|
| Backend | Python, Django 4.2, Django REST Framework |
| Database | PostgreSQL |
| Background jobs | Celery + Redis (import, webhook processing, reconciliation) |
| Frontend | React 19 (JavaScript), Vite, Tailwind v4, shadcn/ui, TanStack Query, Recharts |

## What is in the box

- **Multi-user organizations powered by a GitHub App.** An organization installs the app once; everyone who can see that installation on GitHub signs in with GitHub and lands in the organization. GitHub stays the source of truth: PR Tracker never copies repository permissions, it only syncs and shows repositories the installation can access. There are no personal access tokens.
- **Roles**: the person who installs the GitHub App on an organization becomes its **Admin** (if the installer is unknown, the first person to sign in does); everyone after is a **Member**. A user in several organizations picks one after signing in, and anyone can use **Set up PRLens** (login screens) to install the App on another organization; GitHub decides who may install.
  - **Members** see only their own PRs, reviews and analytics (Analytics, Pull Requests, My Reviews) and can press *Sync GitHub*.
  - **Admins** see all of that, plus a **Viewing** filter on Analytics, Pull Requests and My Reviews: *Me*, *Everyone*, or any single member (their dashboard, PRs and pending reviews as they would see them). The **Admin** sidebar group adds an **Organization** dashboard (organization-wide numbers and a per-member table of PRs created / merged / open, reviews given and pending reviews) and a **Members** page (promote or demote admins, remove or restore someone's access). Admins also choose which repositories are tracked, re-import history and see the GitHub connection / webhook status in Settings.
  - This is enforced on the server: a member asking for `scope=all` or another person's `member=` gets 403. An organization always keeps at least one admin, and nobody can remove their own access. Removing someone is sticky (signing in again does not bring them back); losing access on GitHub revokes automatically and regaining it restores automatically.
- **No access?** A user whose GitHub account is not part of any installation signs in but sees: "PRLens does not currently have access to the repositories available to you. Please contact your GitHub organization administrator."
- **Repository selection** (admins): every repository the installation can access starts tracked; admins can switch some off.
- **Resumable historical import** - progress is checkpointed after every PR, so a crash, rate limit or restart resumes where it stopped.
- **Webhooks** (`pull_request`, `pull_request_review`, `installation`, `installation_repositories`): signature-checked, stored, queued, processed asynchronously and idempotently (per delivery ID).
- **Sync jobs**: every sync (the first import, "Sync GitHub", the scheduled run every 6 hours, installation events) is recorded as a `SyncJob` with its status (`PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `PARTIAL`) and counts; Settings summarises the latest sync and the header shows "Last synced".
- **Filters** that combine freely and live in the URL: one **Date** control (quick presets such as Last 7 / 30 / 365 Days, This / Last Week, Month and Year, or a custom start-end range), repository, reviewer, status, and free-text search on the PR list (title, description, repository, labels, reviewers). Every page opens on *This Year*; pick another preset or *All Time* to change it.
- **PR list** (sort, search, paginate), **PR detail** with reviews, reviewer history, labels and a full **timeline**.
- **Analytics** (the home page): headline cards (PRs, open, merged, waiting for review), activity over the selected dates (daily, weekly, monthly or yearly bars depending on the range; click a bar to zoom in), status breakdown, time to first review, time to merge, review cycles, year-over-year, per-repository stats and recent PRs. Calculated numbers are labelled as such and their definitions are shown on the page.
- **My Reviews**: pull requests by *other people* that you were asked to review or have reviewed. A "Pending reviews" card (open PRs waiting on you, with how long they have waited), review activity (PRs reviewed, approvals, changes requested, pending) and a table of your reviews, filterable by date, repository and status (Pending / Approved / Changes requested / Commented). Your status on a PR is your latest approve / request-changes review; if you only left comments it is *Commented*. Pending PRs are "right now", so the date range does not apply to them. Also filterable by author. Your own PRs never appear here. Admins can switch the *Viewing* filter to *Everyone* (and filter by author) to browse the whole organization.
- **Repositories** view. The older **Reviewers** page (people who review *your* PRs) is hidden from the sidebar for now but still reachable at `/reviewers`.

## Quick start (local development)

Prerequisites: Python 3.9+ (3.11/3.12 recommended), Node 20+, PostgreSQL 14+, Redis.

```bash
cp .env.example .env              # then edit - see "GitHub setup" below

# 1. Backend
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
createdb prtracker                # or point DATABASE_URL at your database
python manage.py migrate
python manage.py runserver 8000   # use another port if 8000 is taken, e.g. 8010

# 2. Worker (separate terminals; or set CELERY_EAGER=1 to run jobs inline without Redis)
celery -A config worker -l info
celery -A config beat -l info     # schedules reconciliation every 6h

# 3. Frontend
cd ../frontend
npm install
VITE_BACKEND_URL=http://localhost:8000 npm run dev    # http://localhost:5173
```

Open <http://localhost:5173>. The Vite dev server proxies `/api` to Django, so the browser only ever talks to one
origin (cookies, CSRF and OAuth callbacks behave exactly like production).

### Try it without GitHub

```bash
cd backend && python manage.py seed_demo      # ~90 realistic PRs through the real sync pipeline
echo "ALLOW_DEMO_LOGIN=1" >> ../.env          # restart the server, then use the "Admin" or "Developer" demo button on the login page (Admin = Demo Developer, who administers Acme; Developer = sarah-smith, a plain member)
```

### Docker

```bash
cp .env.example .env              # set DJANGO_SECRET_KEY and the GitHub App values
docker compose up --build         # app on http://localhost:8080
```

Starts Postgres, Redis, the Django API (gunicorn), a Celery worker, Celery beat and an nginx container serving the
React build and proxying `/api`. Put a TLS-terminating proxy in front of it in production; with plain
`http://localhost:8080` set `HTTPS_ONLY=0` so secure cookies and the HTTPS redirect do not get in the way.

## GitHub setup

Create one GitHub App at <https://github.com/settings/apps/new> (PR Tracker has no other way to read GitHub):

| Setting | Value |
|---|---|
| Callback URL | `${PUBLIC_URL}/api/auth/github/callback` (also the Setup URL; tick *Request user authorization (OAuth) during installation*) |
| Request user authorization (OAuth) during installation | ✅ |
| Webhook URL | `${PUBLIC_URL}/api/webhooks/github` (must be publicly reachable - use ngrok / cloudflared in dev) |
| Webhook secret | a long random string → `GITHUB_WEBHOOK_SECRET` |
| Repository permissions | **Pull requests: Read-only**, **Metadata: Read-only** (nothing else) |
| Subscribe to events | **Pull request**, **Pull request review** (installation events are delivered automatically) |

Then generate a private key and fill in `.env` (keep the key in the environment or a secret manager, never in the repo):

```
GITHUB_APP_ID=…            GITHUB_APP_SLUG=your-app-slug
GITHUB_CLIENT_ID=…         GITHUB_CLIENT_SECRET=…
GITHUB_PRIVATE_KEY_PATH=/path/to/app.private-key.pem
GITHUB_WEBHOOK_SECRET=…
```

An organization owner installs the app on the organization and picks the repositories it may access. Anyone in the
organization can then sign in; the first person becomes the organization's Admin. Installation and sign-in are
separate things: installing connects the *organization*, signing in identifies the *person*.

- Access to GitHub uses **short-lived installation tokens** minted from the app's key. They are cached for under an hour and never stored.
- The user's own token from sign-in is used once, to learn who they are and which installations they can see, and is then discarded.
- Repositories added to / removed from the installation are picked up through `installation_repositories` webhooks, or on the next sync.

## Slack setup

Slack sends each reviewer a direct message when someone asks for their review, plus reminders when it has been waiting
(default: after 24 h, at most twice, weekdays 09:00-18:00). Reviewers who already reviewed a PR get a "Review requested
again" message when asked again. The author gets "PR approved" / "Changes requested", and a reviewer who asked for
changes gets "Changes pushed" when the author pushes after their feedback (switch these off with **Review outcomes**
under **Slack -> What gets sent**). Admins connect it under
**Organization -> Slack**. The GitHub App must be subscribed to the **Pull request** and **Pull request review** events.

1. Create a Slack App at <https://api.slack.com/apps> (*From scratch*) in your workspace.
2. **OAuth & Permissions** -> *Bot Token Scopes*: `chat:write`, `im:write`, `users:read`, `users:read.email`.
3. **OAuth & Permissions** -> *Redirect URLs*: `https://<your-public-origin>/api/slack/callback` (Slack requires https;
   in development use a tunnel: `ngrok http 5173`, and set `PUBLIC_URL` or `SLACK_REDIRECT_BASE` to it).
4. Put the App's *Client ID* / *Client Secret* in `.env` (`SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET`) and restart.
5. Run the scheduler too (`celery -A config beat -l info`): it checks for due messages every minute. Webhooks make
   review-request DMs instant; without them the next scheduled check or sync picks them up.
6. As an admin open **Admin -> Slack**, click **Add to Slack**, then check the People table (matched by email, then by
   username / full name; fix any by hand) and press **Send me a test**.

The bot token is stored encrypted (Fernet; key from `SECRETS_ENCRYPTION_KEY` or derived from `DJANGO_SECRET_KEY`) and is
never returned by the API. Only requests made after Slack was connected trigger messages, and each message is sent once.

## How it works

```
GitHub ──webhook──▶ POST /api/webhooks/github
                      │ verify HMAC signature · validate headers · de-duplicate by delivery ID
                      ▼
                 WebhookEvent (stored) ──▶ Celery ──▶ apply to PR / reviews / reviewers / events ─┐
GitHub ◀─REST── historical import (resumable batches) & reconciliation (every 6h) ───────────────┤
                                                                                                    ▼
                                                                            PostgreSQL ◀── DRF API ◀── React
```

- `apps/github/normalize.py` is the only place that knows GitHub's payload shapes; internal models are independent of them.
- Every apply function is idempotent. Out-of-order webhook deliveries never regress newer data.
- Failed deliveries are retried with backoff, and GitHub's redelivery of a failed delivery ID is re-processed.
- Rate limits (primary and secondary) pause the import until GitHub's reset time instead of failing it.

### Metric definitions

Metrics are **calculated by the app**, not GitHub fields, and are defined in one place
(`backend/apps/tracker/metrics.py`, surfaced in the UI):

| Metric | Definition |
|---|---|
| Time to first review | first submitted review by someone other than the author − PR created |
| Time to merge | merged − created (merged PRs only) |
| Review cycles | changes requested → new commits pushed → reviewed again |
| Waiting for review | open PR in the review process: review requested, being reviewed, or changes requested (approved PRs, drafts and PRs nobody has asked to review are excluded) |
| Status | Merged / Closed; otherwise *Changes Requested* › *Approved* › *In Review* › *Open* (draft stays Open) |

## API

All endpoints require a signed-in session and are scoped to the user's active organization. PR endpoints return your own PRs by default; `scope=all` (whole organization) and `member=<user id>` (one person's data) are admin-only, as are the endpoints marked below.

| Endpoint | |
|---|---|
| `GET /api/prs?scope=&author=&date_from=&date_to=&repository=&status=&reviewer=&q=&ordering=&page=` (the API also still accepts `year`, `month`, `quarter` and `label`) | Paginated PR list |
| `GET /api/prs/:id` · `GET /api/timeline/:pr_id` | PR detail (reviews, reviewer history, timeline) |
| `GET /api/statistics` | Counts, status, review/merge metrics, monthly + yearly series (same filters) |
| `GET /api/repositories` · `POST /api/repositories/select` · `PATCH /api/repositories/:id` | Repository list / choose what to track (select and patch are admin-only) |
| `GET /api/my-reviews?date_from=&date_to=&repository=&status=&author=&q=&page=` | Your pending reviews, review activity and review table |
| `GET /api/reviewers` · `GET /api/labels` · `GET /api/filters` | Reviewer activity, labels, dropdown options |
| `POST /api/github/sync` | "Sync GitHub": discover repositories and refresh now (`{"full": true}` re-imports; admin-only) |
| `GET /api/import/status` · `GET /api/github/webhook-info` | Sync progress, latest sync summary, last synced · GitHub connection and webhook info (admin-only) |
| `GET /api/organization/members` · `PATCH /api/organization/members/:user_id` (`role`, `active`) · `GET /api/organization/overview` | Admin-only: member list and management, per-member activity table |
| `GET /api/auth/me` · `POST /api/auth/organization` | Session, active organization and role · switch organization |
| `POST /api/webhooks/github` | GitHub webhook receiver |

Multi-valued filters take comma-separated values (`status=merged,closed`).

## Security

- Webhook signatures (HMAC-SHA256) are verified in constant time on the raw body; unsigned requests are always rejected, even if no secret is configured.
- Session auth with CSRF protection on every state-changing request; every query is scoped to the active organization, admin actions are role-checked, and tests cover cross-organization isolation.
- No GitHub token is ever stored: installation tokens are short-lived and only cached, and the user's sign-in token is discarded immediately. The app's private key lives in the environment.
- OAuth `state` is verified; sign-ins, repository changes and sync requests are written to an audit log (shown in Settings).
- HTTPS-only cookies / redirect / HSTS are enabled when `DJANGO_DEBUG=0`.

## Tests

```bash
cd backend && python manage.py test --settings=config.test_settings
```

Covers the sync engine (idempotency, reviewer history, status & cycle rules), resumable import and reconciliation,
the webhook endpoint (signatures, duplicates, redelivery, ordering, ignored events), the filter / search / statistics
APIs, GitHub sign-in and membership / role rules, organization sync jobs and installation webhooks, and authorisation boundaries.

## Project layout

```
backend/
  config/            settings, URLs, Celery app
  apps/accounts/     User, Organization, memberships & roles, audit log, GitHub sign-in
  apps/github/       API client, auth (App JWT / installation tokens), normalisation,
                     installations, SyncJobs, sync engine, webhook receiver + processing, Celery tasks
  apps/tracker/      models, metrics & status rules, filters, statistics, timeline, REST API
frontend/src/
  components/ui/     shadcn/ui components
  pages/             Analytics (home), PRs, PR detail, My Reviews, Repositories, Reviewers (hidden), Settings
```

## Roadmap

Notifications (Slack / email), PR-aging alerts and an AI query layer are intentionally out of the MVP. The
structured API and filter layer are designed so an assistant can answer questions by calling the same filtered
endpoints rather than touching the database directly.
