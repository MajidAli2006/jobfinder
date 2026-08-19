"""Match scoring (how likely the candidate is to win the work) and networking scoring (how
reachable the hiring side is).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config, profile
from .models import Job
from .utils import normalize, word_present

AGGREGATOR_DOMAINS = (
    "remoteok.com", "remoteok.io", "remotive.com", "weworkremotely.com",
    "arbeitnow.com", "jobicy.com", "himalayas.app", "workingnomads.com",
    "indeed.com", "linkedin.com", "glassdoor", "ziprecruiter", "adzuna",
    "reed.co.uk", "totaljobs", "cv-library", "jooble", "talent.com",
    "jobs.lever.co", "boards.greenhouse.io", "workable.com", "smartrecruiters",
)


def _cap(value: float, limit: float) -> float:
    return min(value, limit)


def applicant_count(caption: str) -> int | None:
    """Parse LinkedIn's applicant caption into a number."""
    if not caption:
        return None
    low = caption.lower()
    if "among the first" in low:
        return 10
    if "over 200" in low:
        return 250
    match = re.search(r"(\d[\d,]*)", low)
    return int(match.group(1).replace(",", "")) if match else None


@dataclass
class Contribution:
    """One rule's effect on the score, with the wording it justifies."""

    points: float = 0.0
    reasons: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchContext:
    """Everything a scoring rule needs that is not the advert itself."""

    search: profile.SearchProfile
    primary: str
    secondary: str
    primary_in_title: bool
    required_years: int | None

    @classmethod
    def build(cls, job: Job, required_years: int | None) -> MatchContext:
        active = profile.active()
        core = active.core_terms
        primary = core[0] if core else ""
        secondary = core[1] if len(core) > 1 else primary
        return cls(
            search=active,
            primary=primary,
            secondary=secondary,
            primary_in_title=bool(primary) and word_present(primary, normalize(job.title)),
            required_years=required_years,
        )

    @property
    def home(self) -> str:
        return self.search.home_country or "your country"


CORE_POINTS = {"Yes": 26.0, "Preferred": 16.0, "Mentioned": 10.0, "No": 0.0}
SECONDARY_POINTS = {"Yes": 8.0, "Preferred": 5.0, "Mentioned": 3.0, "No": 0.0}
SENIORITY_POINTS = {
    "Senior": 8.0, "Lead": 8.0, "Principal": 6.0, "Mid": 5.0,
    "Unspecified": 4.0, "Junior": -3.0,
}


def _core_fit(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """How firmly the advert asks for the thing being searched for."""
    out = Contribution()
    if not ctx.primary:
        return out

    label = ctx.primary.title()
    out.points += CORE_POINTS.get(job.core_skill_required, 0.0)
    if ctx.primary_in_title:
        out.points += 4.0
        out.reasons.append(f"{label} is in the job title — a direct match")
    elif job.core_skill_required == "Yes":
        out.reasons.append(f"{label} is a stated requirement")
    elif job.core_skill_required == "Preferred":
        out.reasons.append(f"{label} listed as preferred/nice-to-have")
    elif job.core_skill_required == "No":
        out.concerns.append(
            f"{label} is not mentioned — adjacent experience must carry the application")

    if ctx.secondary and ctx.secondary != ctx.primary:
        out.points += SECONDARY_POINTS.get(job.secondary_skill_required, 0.0)
        if job.secondary_skill_required == "Yes":
            out.reasons.append(f"{ctx.secondary.title()} explicitly required")
    return out


def _skill_overlap(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """Reward the candidate's other skills, without paying twice for core terms."""
    out = Contribution()
    skills = ctx.search.skills
    if not skills:
        return out

    overlap = 0.0
    matched: list[str] = []
    for skill, weight in skills.items():
        if ctx.search.is_core(skill):
            continue
        if word_present(skill, text):
            overlap += weight
            matched.append(skill)
    out.points += _cap(overlap, 22.0)
    if matched:
        top = sorted(matched, key=lambda s: -skills[s])[:6]
        out.reasons.append("Skill overlap: " + ", ".join(top))
    return out


def _domain_experience(job: Job, text: str, ctx: MatchContext) -> Contribution:
    out = Contribution()
    domain = 0.0
    hits: list[str] = []
    for keyword, weight in ctx.search.domain_keywords.items():
        if word_present(keyword, text):
            domain += weight
            hits.append(keyword)
    out.points += _cap(domain, 8.0)
    if hits:
        out.reasons.append("Industry match: " + ", ".join(hits[:4]))
    return out


def _seniority_fit(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """Rank by seniority without discarding winnable junior and mid roles."""
    out = Contribution()
    out.points += SENIORITY_POINTS.get(job.seniority, 3.0)

    years = ctx.search.years_experience
    if job.seniority in ("Senior", "Lead"):
        if years:
            out.reasons.append(f"{job.seniority} level matches ~{years} years of experience")
        else:
            out.reasons.append(f"{job.seniority} level role")
    elif job.seniority == "Junior":
        out.concerns.append("Beginner level — lead the CV with breadth, expect a lower band")

    if job.experience_level:
        out.reasons.append(f"Pitch the CV at {job.experience_level} level")
    return out


def _years_requirement(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """Compare the advert's stated years against what the candidate has."""
    out = Contribution()
    asked = ctx.required_years
    if not asked:
        return out

    held = ctx.search.years_experience
    if not held:
        if asked > 10:
            out.points -= 4.0
            out.concerns.append(f"Asks for {asked}+ years of experience")
        return out

    if asked <= held:
        out.points += 4.0
        out.reasons.append(f"Asks for {asked}+ years — you have ~{held}")
    elif asked <= held + 3:
        out.points += 2.0
    else:
        out.points -= 4.0
        out.concerns.append(f"Asks for {asked}+ years of experience")
    return out


def _remote_quality(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """Prefer arrangements that are unambiguously open to where they live."""
    out = Contribution()
    status = job.remote_status
    home = ctx.home
    points = 0.0
    if status.startswith(f"Remote — {home}"):
        points = 10.0
    elif ("Worldwide" in status
          or "Europe/EMEA" in status
          or any(region in status for region in ctx.search.region_terms[:1])):
        points = 8.0
    elif "International contractor" in status:
        points = 6.0
    out.points += points
    if points >= 8:
        out.reasons.append(f"{status} — eligibility is clear")
    return out


def _freshness(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """An advert's best day is the day it is posted."""
    out = Contribution()
    age = job.job_age_days
    if age is None:
        return out
    if age <= 0:
        out.points += 12.0
        out.reasons.append("Posted today — apply before the queue builds")
    elif age == 1:
        out.points += 9.0
        out.reasons.append("Posted yesterday")
    elif age <= 3:
        out.points += 6.0
    else:
        out.points += 3.0
    return out


def _competition(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """Fit is what you bring; this is what you are up against."""
    out = Contribution()
    applied = applicant_count(job.applicants)
    if applied is None:
        return out
    if applied <= 25:
        out.points += 8.0
        out.reasons.append(f"Early applicant window — {job.applicants}")
    elif applied <= 75:
        out.points += 2.0
        out.reasons.append(f"{job.applicants} so far — normal competition")
    elif applied <= 150:
        out.points -= 6.0
        out.concerns.append(f"{job.applicants} — crowded, apply today or not at all")
    elif applied <= 200:
        out.points -= 10.0
        out.concerns.append(f"{job.applicants} — very crowded")
    else:
        out.points -= 14.0
        out.concerns.append(f"{job.applicants} — heavily oversubscribed, expect a low reply rate")
    return out


SMALL_SIZE_LABELS = ("1-10", "11-50", "51-200", "Startup (small)", "Scale-up")


def _employer_size(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """Smaller employers rank higher: a person reads the application.

    Size stopped being a rejection so that nothing is hidden. It stays here
    because at a 10,000-person employer an application joins a queue, and that
    genuinely lowers the chance of a reply.
    """
    out = Contribution()
    if job.company_size in SMALL_SIZE_LABELS:
        out.points += 6.0
        out.reasons.append(f"{job.company_size} employer — a person reads applications")
    elif job.company_size in config.LARGE_SIZE_LABELS:
        out.points -= 6.0
        out.concerns.append(f"{job.company_size} employer — expect an applicant queue")
    return out


def _pay_transparency(job: Job, text: str, ctx: MatchContext) -> Contribution:
    out = Contribution()
    if job.salary_min or job.salary_max or job.day_rate_min or job.day_rate_max:
        out.points += 3.0
        out.reasons.append("Compensation is published")
    else:
        out.concerns.append("No published salary or day rate")
    return out


def _competing_disciplines(job: Job, text: str, ctx: MatchContext) -> Contribution:
    """Adverts that target several disciplines at once dilute the application."""
    out = Contribution()
    competing = 0.0
    for discipline, weight in ctx.search.competing_stacks.items():
        if word_present(discipline, text) and not ctx.primary_in_title:
            competing += weight
            out.concerns.append(f"Posting also targets {discipline}")
    out.points -= _cap(competing, 12.0)
    return out


def _standing_penalties(job: Job, text: str, ctx: MatchContext) -> Contribution:
    out = Contribution()
    if "recruitment agency" in text or "our client" in text:
        out.points -= 3.0
    if any("working hours" in concern for concern in job.concerns):
        out.points -= 5.0
    if job.employment_type == "Part Time":
        out.points -= 2.0
    return out


MATCH_RULES = (
    _core_fit,
    _skill_overlap,
    _domain_experience,
    _seniority_fit,
    _years_requirement,
    _remote_quality,
    _freshness,
    _competition,
    _employer_size,
    _pay_transparency,
    _competing_disciplines,
    _standing_penalties,
)


def score_match(job: Job, text: str,
                required_years: int | None = None) -> tuple[int, list[str], list[str]]:
    """Return (score 0-100, reasons, concerns) for one advert."""
    ctx = MatchContext.build(job, required_years)
    score = 0.0
    reasons: list[str] = []
    concerns: list[str] = []
    for rule in MATCH_RULES:
        part = rule(job, text, ctx)
        score += part.points
        reasons.extend(part.reasons)
        concerns.extend(part.concerns)
    return int(max(0, min(100, round(score)))), reasons, concerns


def score_networking(job: Job, text: str) -> tuple[int, list[str]]:
    """How easy is it to reach a human and bypass the application queue?"""
    score = 0.0
    notes: list[str] = []

    if job.public_email:
        score += 25.0
        notes.append(f"Public contact email available ({job.public_email})")
    if job.best_contact_name:
        score += 15.0
        notes.append(f"Named contact: {job.best_contact_name}")
    if job.public_phone:
        score += 10.0
    if job.careers_page:
        score += 10.0
    if job.company_website:
        score += 8.0
    if job.linkedin:
        score += 7.0

    apply_url = (job.application_url or "").lower()
    if apply_url and not any(domain in apply_url for domain in AGGREGATOR_DOMAINS):
        score += 20.0
        notes.append("Applies directly on the company's own site")
    elif apply_url:
        score += 5.0

    if job.is_startup:
        score += 10.0
        notes.append("Startup — founders are usually directly reachable")
    if job.company_size in ("1-10", "11-50", "Startup (small)"):
        score += 5.0
    if job.is_partnership:
        score += 5.0

    if "hiring manager" in text or "founder" in text or "cto" in text:
        score += 5.0

    return int(max(0, min(100, round(score)))), notes
