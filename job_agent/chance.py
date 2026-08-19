"""Estimated chance of being shortlisted, as opposed to raw CV overlap.

CV fit sets the ceiling; the contest moves the number from there — applicants
already in, advert age, size of the eligible pool, and whether the application
reaches the employer or an agency. Each adjustment carries its own reason so a
row can explain its ranking.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import profile
from .models import Job
from .utils import normalize, word_present

AGENCY_NAME = re.compile(
    r"recruit|resourcing|staffing|consultanc|agency|talent partner|search & selection",
    re.IGNORECASE)

BOARD_AS_EMPLOYER = re.compile(
    r"^(jobgether|hire feed|lensa|talentify|jobot|get it recruit|jobleads|"
    r"whatjobs|talent\.com|dice|ziprecruiter|indeed)$", re.IGNORECASE)

DIRECT_ATS_SOURCES = ("greenhouse", "lever", "ashby", "workable", "recruitee",
                      "smartrecruiters", "company_boards", "teamtailor", "personio")

AGGREGATOR_SOURCES = ("jobgether", "jobicy", "remoteok", "remotive", "weworkremotely",
                      "workingnomads", "himalayas", "jobspresso", "arbeitnow",
                      "remoterocks", "careerjet", "jooble", "adzuna")

BID_MARKETPLACE_SOURCES = ("freelancer", "upwork", "peopleperhour", "guru")

FLOOR = 0.22
CEILING = 1.05
MAX_SCORE = 96


@dataclass
class Adjustment:
    """One multiplier and the reason it applies."""

    factor: float
    reason: str

    def describe(self) -> str:
        percent = round((self.factor - 1) * 100)
        sign = "+" if percent >= 0 else ""
        return f"{self.reason} ({sign}{percent}%)"


@dataclass
class Estimate:
    """A chance of being shortlisted, and the arithmetic behind it."""

    score: int
    fit: int
    multiplier: float
    adjustments: list[Adjustment] = field(default_factory=list)

    def explain(self) -> str:
        steps = " · ".join(a.describe() for a in self.adjustments)
        return f"fit {self.fit} × {self.multiplier:.2f} = {self.score}" + (
            f" — {steps}" if steps else "")


def applicant_count(caption: str) -> int | None:
    """Read a board's applicant caption as a number."""
    if not caption:
        return None
    low = caption.lower()
    if "among the first" in low or "first 25" in low:
        return 10
    if "over 200" in low:
        return 250
    match = re.search(r"(\d[\d,]*)", low)
    return int(match.group(1).replace(",", "")) if match else None


def _competition(job: Job, search) -> Adjustment | None:
    applied = applicant_count(job.applicants)
    if applied is None:
        return Adjustment(0.96, "applicant count not published")
    if applied <= 15:
        return Adjustment(1.05, f"early applicant window — {job.applicants}")
    if applied <= 25:
        return Adjustment(1.03, f"only {applied} applicants so far")
    if applied <= 75:
        return Adjustment(0.98, f"{applied} applicants")
    if applied <= 150:
        return Adjustment(0.85, f"{applied} applicants — crowded")
    if applied <= 200:
        return Adjustment(0.74, f"{applied} applicants — very crowded")
    return Adjustment(0.60, "200+ applicants — heavily oversubscribed")


def _advert_age(job: Job, search) -> Adjustment | None:
    days = job.job_age_days
    if days is None:
        return Adjustment(0.90, "posting date unknown")
    if days <= 1:
        return Adjustment(1.03, "posted in the last 24 hours")
    if days <= 7:
        return Adjustment(1.00, "posted this week")
    if days <= 21:
        return Adjustment(0.90, f"{days} days old")
    if days <= 45:
        return Adjustment(0.76, f"{days} days old — shortlist likely forming")
    return Adjustment(0.58, f"{days} days old — probably late in the process")


def _eligible_pool(job: Job, search) -> Adjustment | None:
    """A narrower eligible pool is fewer rivals and an unambiguous right to work."""
    status = job.remote_status or ""
    home = search.home_country
    if home and home in status:
        return Adjustment(1.06, f"scoped to {home} — smaller pool, your right to work is clear")
    if "Worldwide" in status:
        return Adjustment(0.85, "worldwide pool — global competition, often on rate")
    if "regional" in status.lower():
        return Adjustment(0.98, "regional pool")
    if "On-site" in status or "Hybrid" in status:
        return Adjustment(1.04, "local role — the pool is people who can travel to it")
    if not status or status == "Not stated":
        return Adjustment(0.70, "eligibility unconfirmed — they may not accept you")
    return None


def _level_alignment(job: Job, search) -> Adjustment | None:
    wanted = (search.seniority or "").lower()
    advertised = (job.seniority or "").lower()
    if not advertised or advertised == "unspecified":
        return None
    if wanted and advertised in wanted:
        return Adjustment(1.04, "pitched at your level")
    if advertised in ("junior", "graduate", "intern") and wanted in ("senior", "lead", "principal"):
        return Adjustment(0.50, f"{job.seniority} — likely screened out as over-qualified")
    if advertised in ("lead", "principal", "head") and wanted in ("mid", "junior"):
        return Adjustment(0.72, f"{job.seniority} — above what your CV evidences")
    if advertised == "mid" and wanted in ("senior", "lead"):
        return Adjustment(0.94, "mid-level — you sit above the band")
    return None


def _core_skill_centrality(job: Job, search) -> Adjustment | None:
    if not search.core_terms:
        return None
    primary = search.core_terms[0].title()
    if job.core_skill_required == "Yes":
        return Adjustment(1.08, f"{primary} is the headline skill")
    if job.core_skill_required in ("Preferred", "Mentioned"):
        return Adjustment(1.00, f"{primary} mentioned in the body")
    return Adjustment(0.78, f"no {primary} — you compete as a generalist")


def _application_channel(job: Job, search) -> Adjustment | None:
    """Applying into an employer's own system beats an agency's inbox."""
    source = (job.source or "").lower()
    company = (job.company or "").strip()

    if any(bid in source for bid in BID_MARKETPLACE_SOURCES):
        return Adjustment(0.40, "open-bid marketplace — rate-driven, low conversion")
    if company and BOARD_AS_EMPLOYER.match(company):
        return Adjustment(0.75, "employer not disclosed — advertised by the board itself")
    if company and AGENCY_NAME.search(company):
        return Adjustment(0.84, "agency-posted — an extra gatekeeper, client undisclosed")
    if any(ats in source for ats in DIRECT_ATS_SOURCES):
        return Adjustment(1.05, "applying straight into the employer's own system")
    if any(agg in source for agg in AGGREGATOR_SOURCES):
        return Adjustment(0.88, "aggregator repost — the original channel may be ahead of you")
    return None


def _domain_overlap(job: Job, search) -> Adjustment | None:
    """Industry experience the candidate actually has is a real edge."""
    if not search.domain_keywords:
        return None
    haystack = normalize(f"{job.title} {job.industry} {job.description_excerpt}")
    hits = [term for term in search.domain_keywords if word_present(term, haystack)]
    if not hits:
        return None
    return Adjustment(1.06, f"your {hits[0]} background matches the employer's")


def _reachability(job: Job, search) -> Adjustment | None:
    if job.public_email or job.best_contact_name:
        return Adjustment(1.04, "a real contact is published")
    return None


def _years_gap(job: Job, search) -> Adjustment | None:
    held = search.years_experience
    asked = job.required_years
    if not asked or not held or asked <= held:
        return None
    if asked > held + 3:
        return Adjustment(0.82, f"asks for {asked}+ years against your ~{held}")
    return Adjustment(0.90, f"asks for {asked}+ years")


def _employer_scale(job: Job, search) -> Adjustment | None:
    from . import config
    if job.company_size in ("1-10", "11-50", "Startup (small)"):
        return Adjustment(1.05, "small employer — a person reads every application")
    if job.company_size in config.LARGE_SIZE_LABELS:
        return Adjustment(0.88, "large employer — your application joins a queue")
    return None


RULES = (
    _competition,
    _advert_age,
    _eligible_pool,
    _level_alignment,
    _core_skill_centrality,
    _application_channel,
    _domain_overlap,
    _reachability,
    _years_gap,
    _employer_scale,
)


def estimate(job: Job, fit: int) -> Estimate:
    """Turn a CV-fit score into a chance of being shortlisted."""
    search = profile.active()
    multiplier = 1.0
    adjustments: list[Adjustment] = []
    for rule in RULES:
        adjustment = rule(job, search)
        if adjustment is None:
            continue
        multiplier *= adjustment.factor
        adjustments.append(adjustment)

    multiplier = max(FLOOR, min(CEILING, multiplier))
    score = max(1, min(MAX_SCORE, round(fit * multiplier)))
    return Estimate(score=score, fit=fit, multiplier=multiplier, adjustments=adjustments)
