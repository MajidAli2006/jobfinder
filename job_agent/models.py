"""Data models shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Any
from urllib.parse import urlsplit


def _url_path(url: str) -> str:
    """Host and path of a URL, without the query string."""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    return f"{parts.netloc}{parts.path}"


@dataclass
class RawJob:
    """A posting exactly as returned by a source, before any filtering."""

    source: str
    source_id: str
    title: str
    company: str
    url: str
    description: str = ""
    location_raw: str = ""
    apply_url: str = ""
    posted_at: datetime | None = None
    employment_type_raw: str = ""
    salary_raw: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    company_website: str = ""
    company_logo: str = ""
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def haystack(self) -> str:
        """All searchable text for this posting, lower-cased."""
        parts = [
            self.title, self.company, self.location_raw, self.description,
            self.employment_type_raw, " ".join(self.tags), _url_path(self.url),
        ]
        return " \n ".join(p for p in parts if p).lower()


@dataclass
class Job:
    """A fully processed opportunity ready for reporting."""

    fingerprint: str = ""
    source: str = ""
    sources: list[str] = field(default_factory=list)

    #: Estimated chance of being shortlisted. This is what the report ranks on.
    match_score: int = 0
    #: The CV/keyword overlap the chance estimate starts from, kept so both
    #: numbers are visible and a low rank can be traced to its cause.
    cv_fit_score: int = 0
    #: The arithmetic behind match_score, one clause per adjustment.
    chance_explained: str = ""
    #: What the advert asks for that the CV does not evidence.
    potential_gaps: str = ""
    networking_score: int = 0

    posted_at: datetime | None = None
    posted_date: date | None = None
    job_age_days: int | None = None
    job_age_label: str = "Unknown"
    discovered_date: date | None = None
    verified_date: date | None = None

    title: str = ""
    company: str = ""
    opportunity_type: str = "Job"
    employment_type: str = "Unknown"
    contract_type: str = "N/A"
    location: str = ""
    remote_status: str = ""
    eligibility: str = ""

    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str = ""
    day_rate_min: float | None = None
    day_rate_max: float | None = None
    salary_usd_equivalent: float | None = None

    #: Years of experience the advert asks for, when it says.
    required_years: int | None = None
    core_skill_required: str = "No"
    secondary_skill_required: str = "No"
    seniority: str = "Unspecified"
    experience_level: str = "Not specified"
    industry: str = "Unknown"
    startup_stage: str = "N/A"
    company_size: str = "Unknown"

    best_contact_name: str = ""
    contact_role: str = ""
    public_email: str = ""
    public_phone: str = ""
    linkedin: str = ""
    company_website: str = ""
    careers_page: str = ""

    application_url: str = ""
    original_job_url: str = ""

    #: A short slice of the advert, for rules that need its wording.
    description_excerpt: str = ""
    match_reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)

    llm_eligibility: str = ""
    llm_eligibility_reason: str = ""
    llm_fit: int = 0
    llm_chance: int = 0
    llm_verdict: str = ""
    llm_promoted: bool = False

    job_status: str = "NEW"
    application_status: str = "Not Applied"

    is_new: bool = True
    careers_page_guessed: bool = False
    description: str = ""
    tags: list[str] = field(default_factory=list)
    applicants: str = ""
    rejected: bool = False
    rejection_reason: str = ""
    rejection_category: str = ""
    is_prospect: bool = False
    is_partnership: bool = False
    is_startup: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for key in ("posted_at", "posted_date", "discovered_date", "verified_date"):
            value = d.get(key)
            d[key] = value.isoformat() if value else ""
        return d


@dataclass
class SourceStat:
    name: str
    raw_count: int = 0
    ok: bool = True
    error: str = ""
    elapsed: float = 0.0


@dataclass
class RunStats:
    """Counters shown on the Search Summary sheet."""

    run_at: datetime | None = None
    period_start: date | None = None
    period_end: date | None = None
    sources_searched: int = 0
    sources: list[SourceStat] = field(default_factory=list)

    profile_label: str = ""
    profile_query: str = ""
    profile_home: str = ""
    profile_has_cv: bool = False
    salary_floor_usd: float = 0.0

    raw_found: int = 0
    descriptions_filled: int = 0
    undated_kept: int = 0
    rejected_stale: int = 0
    rejected_not_remote: int = 0
    rejected_ineligible: int = 0
    region_unknown: int = 0
    rejected_irrelevant: int = 0
    rejected_low_score: int = 0
    rejected_low_pay: int = 0
    rejected_low_rate_market: int = 0
    rejected_large_employer: int = 0
    rejected_expired: int = 0
    duplicates_removed: int = 0

    qualified: int = 0
    hot_leads: int = 0
    full_time: int = 0
    part_time: int = 0
    contract: int = 0
    freelance: int = 0
    startups: int = 0
    partnerships: int = 0
    prospects: int = 0
    level_beginner: int = 0
    level_medium: int = 0
    level_senior: int = 0
    level_unspecified: int = 0
    new_since_last_run: int = 0
    companies: int = 0

    llm_ran: bool = False
    llm_eligibility_calls: int = 0
    llm_fit_calls: int = 0
    llm_cache_hits: int = 0
    llm_promoted: int = 0
    llm_confirmed_ineligible: int = 0
    llm_errors: int = 0
    llm_cost_usd: float = 0.0
    rejected_low_chance: int = 0
    rejected_wrong_engagement: int = 0
    pay_unstated: int = 0
