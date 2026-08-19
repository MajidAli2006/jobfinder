"""Advert filtering: is this the right kind of work, fresh, paid and worth applying to?

Remote status and work eligibility are a separate question, answered in
`remote.py`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from . import cache, config, profile
from .models import RawJob
from .geography import worldwide, location_country
from .money import annual_usd
from .utils import (
    any_regex, http_get, is_negated, local_timezone, normalize, word_present,
)


@dataclass
class Verdict:
    """Outcome of a filter stage."""

    passed: bool
    reason: str = ""
    category: str = ""
    details: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


@dataclass
class Relevance:
    """Everything the relevance gates read, computed once from one advert."""

    search: profile.SearchProfile
    primary: str
    secondary: str
    title: str
    hay: str
    tags: str
    details: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)

    @classmethod
    def read(cls, raw: RawJob, search) -> Relevance:
        core = search.core_terms
        secondary = core[1] if len(core) > 1 else core[0]
        return cls(
            search=search, primary=core[0], secondary=secondary,
            title=normalize(raw.title), hay=normalize(raw.haystack()),
            tags=normalize(" ".join(raw.tags)),
        )

    @property
    def label(self) -> str:
        return self.primary.title()

    @property
    def has_secondary(self) -> bool:
        return self.secondary != self.primary

    @property
    def core_in_title(self) -> bool:
        return word_present(self.primary, self.title)

    @property
    def core_in_body(self) -> bool:
        return word_present(self.primary, self.hay) or self.primary in self.tags

    @property
    def secondary_in_title(self) -> bool:
        return self.has_secondary and word_present(self.secondary, self.title)

    @property
    def secondary_in_body(self) -> bool:
        return self.has_secondary and (word_present(self.secondary, self.hay)
                                       or self.secondary in self.tags)

    @property
    def adjacent_title(self) -> bool:
        return any(term in self.title for term in self.search.secondary_terms)

    @property
    def adjacent_body(self) -> bool:
        return any(term in self.hay for term in self.search.secondary_terms)

    @property
    def title_names_the_work(self) -> bool:
        return self.core_in_title or self.secondary_in_title or self.adjacent_title

    def core_mentions(self) -> int:
        count = self.hay.count(self.primary)
        if self.has_secondary:
            count += len(re.findall(rf"\b{re.escape(self.secondary)}\b", self.hay))
        return count


def _excluded_title(r: Relevance) -> Verdict | None:
    for bad in r.search.hard_title_exclusions:
        if word_present(bad, r.title):
            return Verdict(False, f"Title is out of scope ({bad})", "irrelevant")
    return None


def _any_signal_at_all(r: Relevance) -> Verdict | None:
    if r.core_in_title:
        r.details.append(f"{r.label} named in the job title")
    elif r.core_in_body:
        r.details.append(f"{r.label} named in the job description")
    if r.core_in_body or r.secondary_in_body or r.adjacent_title:
        return None
    return Verdict(False, f"No {'/'.join(r.search.core_terms)} or related signal",
                   "irrelevant")


def _hands_on_title(r: Relevance) -> Verdict | None:
    """The title must read as someone doing the work, not a role about the work."""
    tokens = r.search.hands_on_title_tokens
    if not tokens or r.core_in_title or r.secondary_in_title:
        return None
    if any(word_present(token, r.title) for token in tokens):
        return None
    return Verdict(False, "Title is not a hands-on role in this trade", "irrelevant")


def _not_another_discipline(r: Relevance) -> Verdict | None:
    if r.title_names_the_work:
        return None
    other = any_regex(profile.title_exclusion_patterns(), r.title)
    if other:
        return Verdict(
            False,
            f"Title is a {other.strip()} role — {r.label} only appears in the "
            f"company's stack boilerplate",
            "irrelevant",
        )
    return None


def _more_than_boilerplate(r: Relevance) -> Verdict | None:
    """A passing mention in a stack list is not what the job is."""
    if r.title_names_the_work:
        return None
    mentions = r.core_mentions()
    if mentions >= r.search.min_body_core_mentions:
        return None
    named = "/".join(c.title() for c in r.search.core_terms[:2])
    return Verdict(
        False,
        f"{named} mentioned only {mentions}x and not in the title — reads as "
        f"boilerplate, not the role",
        "irrelevant",
    )


def _strong_enough(r: Relevance) -> Verdict | None:
    if r.core_in_body or r.secondary_in_body:
        return None
    if r.adjacent_title and r.adjacent_body:
        r.concerns.append(f"No {r.label} mention — adjacent role")
        r.details.append(f"{r.search.label} role matching the candidate's background")
        return None
    return Verdict(False, "Signal too weak", "irrelevant")


def _competing_discipline(r: Relevance) -> Verdict | None:
    for stack in r.search.competing_stacks:
        if r.core_in_title:
            continue
        if stack in r.title:
            return Verdict(False, f"Role targets a different stack ({stack})",
                           "irrelevant")
        if stack in r.hay:
            r.concerns.append(f"Mentions {stack}")
    return None


RELEVANCE_GATES = (
    _excluded_title,
    _any_signal_at_all,
    _hands_on_title,
    _not_another_discipline,
    _more_than_boilerplate,
    _strong_enough,
    _competing_discipline,
)


def check_relevance(raw: RawJob) -> Verdict:
    """Is this advert the kind of work the active search is looking for?"""
    search = profile.active()
    if not search.core_terms:
        return Verdict(True)

    reading = Relevance.read(raw, search)
    for gate in RELEVANCE_GATES:
        verdict = gate(reading)
        if verdict is not None:
            return verdict

    if reading.secondary_in_title or reading.secondary_in_body:
        reading.details.append(f"{reading.secondary.title()} named in the posting")
    return Verdict(True, "", "", reading.details, reading.concerns)


def check_freshness(raw: RawJob, cutoff: datetime) -> Verdict:
    """Posted within the last N calendar days, Europe/London."""
    if raw.posted_at is None:
        if not config.KEEP_UNDATED:
            return Verdict(False,
                           f"No posting date published — cannot verify it is within "
                           f"{config.FRESHNESS_DAYS} days", "stale")
        return Verdict(True, "", "", [],
                       ["Posting date not published — age unknown, confirm the advert is current"])
    # The cutoff was built in the candidate's timezone, so the advert has to be
    # read in the same one or a posting can fall a day either side of the window.
    here = local_timezone()
    posted = raw.posted_at.astimezone(here)
    if posted < cutoff:
        age = (datetime.now(here).date() - posted.date()).days
        return Verdict(
            False,
            f"Posted {age} days ago (outside the {config.FRESHNESS_DAYS}-day window)",
            "stale")
    if posted > datetime.now(here).replace(hour=23, minute=59):
        return Verdict(True, "", "", [], ["Posting date is in the future — treat with caution"])
    return Verdict(True)


def low_rate_markets() -> tuple[str, ...]:
    """Markets to treat as low-rate for the active search."""
    active = profile.active()
    theirs = {c.strip().lower() for c in
              (active.target_regions + (active.home_country,)) if c and c.strip()}
    theirs.update(t.lower() for t in active.home_terms)
    return tuple(m for m in config.LOW_RATE_MARKETS if m not in theirs)


def check_market(raw: RawJob) -> Verdict:
    """Reject postings scoped to a market whose pay bands sit far below target."""
    location = normalize(raw.location_raw)
    body = normalize(raw.description)
    low_rate = low_rate_markets()
    if not low_rate:
        return Verdict(True)
    markets = "|".join(low_rate)

    if location and not worldwide(location):
        for market in low_rate:
            if word_present(market, location):
                return Verdict(
                    False,
                    f"Scoped to a low-rate market ({market.title()}) — pay bands sit far "
                    f"below the target rate",
                    "low_rate_market",
                )
        city_country = location_country(raw.location_raw)
        if city_country in low_rate:
            return Verdict(
                False,
                f"Scoped to a low-rate market ({city_country.title()}) — pay bands sit "
                f"far below the target rate",
                "low_rate_market",
            )

    if worldwide(f"{location} {body}"):
        return Verdict(True)

    for template in config.MARKET_SCOPE_PATTERNS:
        pattern = template.format(markets=markets)
        for m in re.finditer(pattern, body, re.IGNORECASE):
            if is_negated(body, m.start()):
                continue
            market = normalize(m.group(1))
            return Verdict(
                False,
                f"Restricted to {market.title()} — a market whose pay bands sit far "
                f"below the target rate",
                "low_rate_market",
            )
    return Verdict(True)


_LARGE_EMPLOYER_RE = re.compile("|".join(config.LARGE_EMPLOYER_NAMES), re.IGNORECASE)

_LI_COMPANY_RE = re.compile(r"linkedin\.com/company/([A-Za-z0-9\-_%.]+)", re.IGNORECASE)
_LI_SIZE_RE = re.compile(
    r"Company size</dt>\s*<dd[^>]*>\s*([^<]+?)\s*<"
    r"|([\d,]+[\-–][\d,+]+|\d[\d,]*\+)\s*employees", re.IGNORECASE | re.S)


def _headcount_ceiling(size_text: str) -> int | None:
    """Upper bound of a size band such as "1,001-5,000 employees"."""
    if not size_text:
        return None
    cleaned = size_text.replace(",", "").replace("–", "-")
    numbers = re.findall(r"\d+", cleaned)
    if not numbers:
        return None
    if "+" in cleaned and len(numbers) == 1:
        return int(numbers[0]) * 2
    return int(numbers[-1])


def linkedin_headcount(company_url: str) -> int | None:
    """Employee-count ceiling from a public LinkedIn company page, or None."""
    if not company_url:
        return None
    match = _LI_COMPANY_RE.search(company_url)
    if not match:
        return None
    slug = match.group(1).rstrip("/").lower()

    cached = cache.get(f"linkedin:size:{slug}", config.COMPANY_SIZE_CACHE_DAYS)
    if cached is not None:
        return cached.get("headcount")

    resp = http_get(f"https://www.linkedin.com/company/{slug}",
                    headers={"User-Agent": config.USER_AGENT,
                             "Accept-Language": "en-GB,en;q=0.9"},
                    timeout=15, retries=1)
    headcount = None
    if resp is not None:
        found = _LI_SIZE_RE.search(resp.text)
        if found:
            headcount = _headcount_ceiling(found.group(1) or found.group(2) or "")
    cache.put(f"linkedin:size:{slug}", {"headcount": headcount})
    return headcount


def check_engagement(job) -> Verdict:
    """Is this the kind of engagement the person asked for?"""
    active = profile.active()
    wanted = active.employment_types
    if wanted and job.employment_type not in wanted:
        if job.employment_type != "Unknown":
            return Verdict(
                False,
                f"{job.employment_type} role — you asked for "
                f"{' or '.join(w.lower() for w in wanted)}",
                "wrong_engagement",
            )

    if active.startups_only and not job.is_startup:
        return Verdict(False, "Not a startup — you asked for small companies only",
                       "not_startup")

    return Verdict(True)


def employer_is_large(raw: RawJob, size_label: str = "Unknown") -> bool:
    """Does the evidence put this employer above the large-enterprise threshold?"""
    company = (raw.company or "").strip()
    if company and _LARGE_EMPLOYER_RE.search(company):
        return True
    if size_label in config.LARGE_SIZE_LABELS:
        return True
    if config.CHECK_COMPANY_SIZE_ONLINE:
        headcount = linkedin_headcount(raw.extra.get("company_linkedin") or "")
        if headcount and headcount > config.LARGE_EMPLOYER_HEADCOUNT:
            return True
    return False


def check_employer_size(raw: RawJob, size_label: str = "Unknown") -> Verdict:
    """Reject on employer size only when the search asked for small firms.

    Size is otherwise a ranking signal, not a gate: a role at a 5,000-person
    employer is still a real job, and hiding it means the person never learns
    it existed. `scoring` ranks smaller employers above corporate queues.
    """
    if not profile.active().small_employers_only:
        return Verdict(True)

    company = (raw.company or "").strip()
    if company and _LARGE_EMPLOYER_RE.search(company):
        return Verdict(
            False,
            f"{company} is a large enterprise, and this search asked for small employers",
            "large_employer",
        )

    if size_label in config.LARGE_SIZE_LABELS:
        return Verdict(
            False,
            f"Advert indicates a {size_label} employer, and this search asked "
            f"for small employers",
            "large_employer",
        )

    if config.CHECK_COMPANY_SIZE_ONLINE:
        headcount = linkedin_headcount(raw.extra.get("company_linkedin") or "")
        if headcount and headcount > config.LARGE_EMPLOYER_HEADCOUNT:
            return Verdict(
                False,
                f"LinkedIn lists {company or 'this employer'} at over "
                f"{config.LARGE_EMPLOYER_HEADCOUNT:,} staff, and this search asked "
                f"for small employers",
                "large_employer",
            )

    return Verdict(True)


def check_pay_floor(job, floor_usd: float = config.SALARY_FLOOR_USD,
                    require_salary: bool = False) -> Verdict:
    """Enforce the minimum pay expectation."""
    value, basis = annual_usd(job)

    if value is None:
        if require_salary:
            return Verdict(False, "No published pay and --require-salary was set", "low_pay")
        if profile.active().pay_floor_stated and floor_usd > 0:
            return Verdict(
                False,
                f"No published pay, so it cannot be shown to meet your "
                f"${floor_usd:,.0f} minimum — check the advert",
                "pay_unstated",
            )
        return Verdict(True, "", "", [], ["Pay not published — confirm the band early"])

    if value < floor_usd:
        return Verdict(
            False,
            f"Pay tops out near ${value:,.0f}/yr ({basis}), below the "
            f"${floor_usd:,.0f} floor",
            "low_pay",
        )

    detail = f"Pay ≈ ${value:,.0f}/yr equivalent ({basis}) — at or above your ${floor_usd:,.0f} floor"
    return Verdict(True, "", "", [detail], [])


CLOSED_MARKERS = (
    "no longer accepting applications",
    "no longer available",
    "this job is closed",
    "position has been filled",
    "this position is closed",
    "applications are closed",
    "vacancy has expired",
    "job has expired",
    "this job posting has expired",
    "we are no longer hiring",
    "role has been filled",
    "sorry, this job is no longer",
    "job not found",
    "page not found",
)


def verify_live(job) -> tuple[bool, str]:
    """Open the advert and confirm the employer is still taking applications."""
    url = job.application_url or job.original_job_url
    if not url:
        return True, ""

    resp = http_get(url, timeout=15, retries=0)
    if resp is None:
        return True, "Could not re-check the advert — verify it is still open"
    if resp.status_code in (404, 410):
        return False, f"Advert returns HTTP {resp.status_code} — the vacancy is gone"

    body = normalize(resp.text[:60000])
    for marker in CLOSED_MARKERS:
        if marker in body:
            return False, f"Advert says \"{marker}\" — no longer hiring"
    return True, ""
