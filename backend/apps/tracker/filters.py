"""Combinable PR filters shared by the list, statistics and reviewer endpoints.

``params`` is a ``{name: [values]}`` dict (see ``parse_params``). Every filter is
optional and filters AND together; multi-valued filters (status, repository,
label) match any of their values.
"""
import shlex
from datetime import date

from django.db.models import Exists, OuterRef, Q

from .models import PullRequest, PullRequestLabel, PullRequestReview, PullRequestReviewer

DATE_KEYS = ("year", "month", "quarter", "date_from", "date_to")
DATE_FIELDS = {
    "created": "created_at_github",
    "merged": "merged_at",
    "updated": "updated_at_github",
    "closed": "closed_at",
}


def parse_params(query_dict):
    return {key: [v for v in query_dict.getlist(key) if v != ""] for key in query_dict}


def first(params, key, default=None):
    values = params.get(key) or []
    return values[0] if values else default


def multi(params, key):
    out = []
    for value in params.get(key) or []:
        out.extend(part.strip() for part in value.split(",") if part.strip())
    return out


def to_int(value, low=None, high=None):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if (low is not None and number < low) or (high is not None and number > high):
        return None
    return number


def to_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def date_field(params, override=None):
    return DATE_FIELDS.get(override or first(params, "date_field", "created"), "created_at_github")


def apply_date_filters(qs, params, field):
    year = to_int(first(params, "year"), 1970, 2200)
    month = to_int(first(params, "month"), 1, 12)
    quarter = to_int(first(params, "quarter"), 1, 4)
    date_from = to_date(first(params, "date_from"))
    date_to = to_date(first(params, "date_to"))
    if year:
        qs = qs.filter(**{field + "__year": year})
    if month:
        qs = qs.filter(**{field + "__month": month})
    if quarter:
        qs = qs.filter(**{field + "__month__gte": quarter * 3 - 2, field + "__month__lte": quarter * 3})
    if date_from:
        qs = qs.filter(**{field + "__date__gte": date_from})
    if date_to:
        qs = qs.filter(**{field + "__date__lte": date_to})
    return qs


def _reviewer_exists(name, exact=True):
    lookup = "iexact" if exact else "icontains"
    as_requested = PullRequestReviewer.objects.filter(
        pull_request=OuterRef("pk"), **{"username__" + lookup: name}
    )
    as_reviewer = PullRequestReview.objects.filter(pull_request=OuterRef("pk"), **{"reviewer__" + lookup: name})
    return Exists(as_requested), Exists(as_reviewer)


def search_terms(text):
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def apply_search(qs, text):
    for term in search_terms(text):
        cleaned = term.lstrip("#")
        q = (
            Q(title__icontains=term)
            | Q(description__icontains=term)
            | Q(repository__full_name__icontains=term)
            | Q(head_branch__icontains=term)
        )
        if cleaned.isdigit():
            q |= Q(number=int(cleaned))
        label_hit, requested_hit, reviewed_hit = (
            Exists(PullRequestLabel.objects.filter(pull_request=OuterRef("pk"), name__icontains=term)),
            *_reviewer_exists(term, exact=False),
        )
        qs = qs.filter(q | Q(label_hit) | Q(requested_hit) | Q(reviewed_hit))
    return qs


def apply_filters(qs, params, skip=(), date_field_override=None):
    if "date" not in skip:
        qs = apply_date_filters(
            qs, {k: v for k, v in params.items() if k not in skip}, date_field(params, date_field_override)
        )

    repositories = multi(params, "repository")
    if repositories and "repository" not in skip:
        q = Q()
        for value in repositories:
            q |= Q(repository_id=int(value)) if value.isdigit() else Q(repository__full_name__iexact=value)
        qs = qs.filter(q)

    statuses = [s for s in multi(params, "status") if s in dict(PullRequest.STATUS_CHOICES)]
    if statuses and "status" not in skip:
        qs = qs.filter(status__in=statuses)

    reviewer = first(params, "reviewer")
    if reviewer and "reviewer" not in skip:
        requested, reviewed = _reviewer_exists(reviewer)
        qs = qs.filter(Q(requested) | Q(reviewed))

    labels = multi(params, "label")
    if labels and "label" not in skip:
        label_q = Q()
        for name in labels:
            label_q |= Q(Exists(PullRequestLabel.objects.filter(pull_request=OuterRef("pk"), name__iexact=name)))
        qs = qs.filter(label_q)

    authors = multi(params, "author")
    if authors and "author" not in skip:
        author_q = Q()
        for login in authors:
            author_q |= Q(author_login__iexact=login)
        qs = qs.filter(author_q)

    draft = first(params, "draft")
    if draft in ("true", "false") and "draft" not in skip:
        qs = qs.filter(draft=(draft == "true"))

    text = (first(params, "q") or "").strip()
    if text and "q" not in skip:
        qs = apply_search(qs, text)
    return qs
