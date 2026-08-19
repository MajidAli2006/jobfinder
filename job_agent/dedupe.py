"""Duplicate removal."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import urlparse

from .models import Job
from .utils import slug, title_slug


def fingerprint(company: str, title: str) -> str:
    key = f"{slug(company)}|{title_slug(title)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def same_employer_and_role(a: Job, b: Job) -> bool:
    """True when two rows are the same vacancy under a longer or shorter name."""
    if title_slug(a.title) != title_slug(b.title) or not title_slug(a.title):
        return False
    first, second = slug(a.company), slug(b.company)
    if min(len(first), len(second)) < 5:
        return False
    return first.startswith(second) or second.startswith(first)


def _merge_company_variants(jobs: list[Job]) -> None:
    """Give one fingerprint to employers whose name one board abbreviates."""
    by_title: dict[str, list[Job]] = {}
    for job in jobs:
        by_title.setdefault(title_slug(job.title), []).append(job)

    for title_key, group in by_title.items():
        if not title_key or len(group) < 2:
            continue
        ordered = sorted(group, key=lambda j: len(slug(j.company)))
        for index, job in enumerate(ordered):
            short = slug(job.company)
            if len(short) < 5:
                continue
            for other in ordered[index + 1:]:
                other_slug = slug(other.company)
                if other_slug != short and other_slug.startswith(short):
                    other.fingerprint = job.fingerprint


def _url_key(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return ""
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", parsed.path or "")
    return f"{host}{path}".lower()


def _completeness(job: Job) -> tuple:
    """Higher is better. Used to choose which copy of a duplicate survives."""
    return (
        1 if job.public_email else 0,
        1 if job.company_website else 0,
        1 if (job.salary_min or job.day_rate_min) else 0,
        1 if job.application_url and "remoteok" not in job.application_url else 0,
        job.match_score,
        len(job.description or ""),
    )


def _resolve_key(job: Job, url_index: dict[str, str]) -> str:
    """The fingerprint this advert belongs under, following any URL alias.

    Two boards can title the same role differently and still point at one
    advert, so a shared URL wins over a fingerprint drawn from the wording.
    """
    key = job.fingerprint or fingerprint(job.company, job.title)
    alt = _url_key(job.original_job_url) or _url_key(job.application_url)
    if alt and alt in url_index and url_index[alt] != key:
        return url_index[alt]
    if alt:
        url_index[alt] = key
    return key


def _take_earliest_posting(winner: Job, loser: Job) -> None:
    """The earlier sighting is the truer posting date, whichever advert won."""
    if loser.posted_at and winner.posted_at and loser.posted_at < winner.posted_at:
        winner.posted_at = loser.posted_at
        winner.posted_date = loser.posted_date
        winner.job_age_days = loser.job_age_days
        winner.job_age_label = loser.job_age_label


def _fill_gaps_from(winner: Job, loser: Job) -> None:
    """Anything the winner never published, take from the copy that did."""
    for field in ("public_email", "public_phone", "best_contact_name", "contact_role",
                  "company_website", "careers_page"):
        if not getattr(winner, field) and getattr(loser, field):
            setattr(winner, field, getattr(loser, field))
    if not (winner.salary_min or winner.salary_max) and (loser.salary_min or loser.salary_max):
        winner.salary_min, winner.salary_max = loser.salary_min, loser.salary_max
        winner.salary_currency = winner.salary_currency or loser.salary_currency


def _merge_duplicate(existing: Job, job: Job, key: str) -> Job:
    """Fold two adverts for one role into whichever says more."""
    merged_sources = sorted({*existing.sources, *job.sources, existing.source, job.source} - {""})
    if _completeness(job) > _completeness(existing):
        winner, loser = job, existing
    else:
        winner, loser = existing, job

    _take_earliest_posting(winner, loser)
    _fill_gaps_from(winner, loser)
    winner.sources = merged_sources
    winner.fingerprint = key
    return winner


def deduplicate(jobs: list[Job]) -> tuple[list[Job], int]:
    """Collapse duplicates, merging source attribution. Returns (jobs, removed)."""
    by_key: dict[str, Job] = {}
    url_index: dict[str, str] = {}
    removed = 0

    for job in jobs:
        job.fingerprint = job.fingerprint or fingerprint(job.company, job.title)
    _merge_company_variants(jobs)

    for job in jobs:
        key = _resolve_key(job, url_index)
        job.fingerprint = key

        existing = by_key.get(key)
        if existing is None:
            job.sources = sorted({job.source, *job.sources}) if job.source else job.sources
            by_key[key] = job
            continue

        removed += 1
        by_key[key] = _merge_duplicate(existing, job, key)

    return list(by_key.values()), removed
