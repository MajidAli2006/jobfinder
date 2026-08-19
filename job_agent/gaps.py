"""What an advert asks for that the candidate's CV does not evidence.

Two sources answer this. When the judgement layer runs against a real CV it
names the gaps directly, because it has read both. Without it — no CV, no key,
or a rules-only run — the gaps are drawn from the few things that can be
checked mechanically, and the row says plainly that no CV was read rather than
inventing a shortfall.
"""

from __future__ import annotations

from . import profile
from .models import Job

NO_CV = "No CV supplied — gaps not assessed"
NONE_FOUND = "Nothing obvious against the stated requirements"


def _years_gap(job: Job, search) -> str:
    asked, held = job.required_years, search.years_experience
    if not asked:
        return ""
    if not held:
        return f"Asks for {asked}+ years; your experience is not stated"
    if asked > held:
        return f"Asks for {asked}+ years against about {held}"
    return ""


def _core_skill_gap(job: Job, search) -> str:
    if not search.core_terms or job.core_skill_required != "No":
        return ""
    return f"{search.core_terms[0].title()} is not named in the advert"


def _seniority_gap(job: Job, search) -> str:
    advertised = (job.seniority or "").lower()
    held = (search.seniority or "").lower()
    if not advertised or advertised == "unspecified" or not held:
        return ""
    ladder = ("junior", "mid", "senior", "lead", "principal")
    try:
        rungs = ladder.index(advertised) - ladder.index(held)
    except ValueError:
        return ""
    if rungs > 0:
        return f"Advertised at {job.seniority}, above your stated {search.seniority}"
    if rungs < -1:
        return f"Advertised at {job.seniority}, well below your stated {search.seniority}"
    return ""


def _eligibility_gap(job: Job, search) -> str:
    eligibility = (job.eligibility or "").lower()
    if "not eligible" in eligibility or "unconfirmed" in eligibility:
        return job.eligibility
    return ""


RULES = (_years_gap, _core_skill_gap, _seniority_gap, _eligibility_gap)


def describe(job: Job) -> str:
    """One readable line of what stands between this CV and this advert."""
    search = profile.active()
    if not search.has_cv:
        return NO_CV
    found = [text for text in (rule(job, search) for rule in RULES) if text]
    return " · ".join(found) if found else NONE_FOUND
