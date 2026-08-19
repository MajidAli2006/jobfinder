"""Contact and company enrichment."""

from __future__ import annotations

import re
from urllib.parse import quote_plus, urlparse

from .models import Job, RawJob
from .utils import first_email, first_phone, truncate

AGGREGATOR_HOSTS = (
    "remoteok.com", "remoteok.io", "remotive.com", "weworkremotely.com",
    "arbeitnow.com", "jobicy.com", "himalayas.app", "workingnomads.com",
    "jobspresso.co", "indeed.com", "linkedin.com", "glassdoor.com",
    "ziprecruiter.com", "adzuna.co.uk", "adzuna.com", "reed.co.uk",
    "totaljobs.com", "cv-library.co.uk", "jooble.org", "talent.com",
    "boards.greenhouse.io", "jobs.lever.co", "apply.workable.com",
    "jobs.ashbyhq.com", "smartrecruiters.com", "bamboohr.com", "breezy.hr",
    "recruitee.com", "teamtailor.com", "workday.com", "myworkdayjobs.com",
    "jobviewtrack.com", "careerjet.com", "careerjet.co.uk", "jobgether.com",
)

#: A personal mailbox is never the employer's own domain.
FREE_MAILBOXES = frozenset({
    "gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
    "live.com", "aol.com", "proton.me", "protonmail.com", "gmx.com",
})

ROLE_WORDS = (
    "Head of [A-Za-z ]{2,30}|Chief [A-Za-z ]{2,25}|VP of [A-Za-z ]{2,25}|"
    "CTO|CEO|COO|CPO|Co-?[Ff]ounder|Founder|Founding [A-Za-z]{2,20}|"
    "Engineering Manager|Hiring Manager|Talent (?:Partner|Manager|Acquisition[A-Za-z ]{0,20})|"
    "Delivery Manager|Technical Recruiter|Recruiter|Team Lead|Tech Lead|"
    "Director of [A-Za-z ]{2,25}|People (?:Partner|Manager|Lead)|Managing Director"
)

_NAME = r"[A-Z][a-z]{1,15}(?:\s+[A-Z][a-z'\-]{1,20}){1,2}"

CONTACT_PATTERNS = (
    rf"(?:our|the)\s+(?P<role1>{ROLE_WORDS})[,:]?\s+(?P<name1>{_NAME})",
    rf"(?P<name2>{_NAME}),\s*(?P<role2>{ROLE_WORDS})",
    rf"(?:contact|speak to|reach out to|attention|attn\.?|ask for|please contact)\s+"
    rf"(?:our\s+)?(?P<name3>{_NAME})",
)


def _host(url: str) -> str:
    """Hostname, lower-cased, without a leading "www."."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _is_aggregator(url: str) -> bool:
    host = _host(url)
    return any(agg in host for agg in AGGREGATOR_HOSTS) if host else True


def company_website(raw: RawJob) -> str:
    if raw.company_website:
        return raw.company_website
    for candidate in (raw.apply_url, raw.url):
        if candidate and not _is_aggregator(candidate):
            parsed = urlparse(candidate)
            if parsed.scheme and parsed.hostname:
                return f"{parsed.scheme}://{parsed.hostname}"
    email = first_email(raw.description)
    domain = email.split("@")[-1].lower() if email else ""
    if _is_employer_domain(domain):
        return f"https://{domain}"
    return ""


def _is_employer_domain(domain: str) -> bool:
    """Is this an employer's own domain, rather than a board or a webmail host?"""
    if not domain or "." not in domain:
        return False
    if any(aggregator in domain for aggregator in AGGREGATOR_HOSTS):
        return False
    return domain not in FREE_MAILBOXES


def careers_page(raw: RawJob, website: str) -> tuple[str, bool]:
    """(url, is_guessed)."""
    for url in (raw.apply_url, raw.url):
        if url and not _is_aggregator(url):
            if re.search(r"/(careers?|jobs?|join-?us|work-with-us|vacancies|gigs|rfp)\b", url, re.I):
                parsed = urlparse(url)
                match = re.match(r"(/[^/]*(?:careers?|jobs?|join-?us|gigs|rfp)[^/]*)", parsed.path, re.I)
                if match:
                    return f"{parsed.scheme}://{parsed.hostname}{match.group(1)}", False
    if website:
        return website.rstrip("/") + "/careers", True
    return "", False


def linkedin_company(company: str) -> str:
    if not company:
        return ""
    return "https://www.linkedin.com/search/results/companies/?keywords=" + quote_plus(company)


def extract_contact(description: str) -> tuple[str, str]:
    """(name, role) of the most likely named contact in the advert."""
    if not description:
        return "", ""
    text = description[:6000]
    for pattern in CONTACT_PATTERNS:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groupdict()
        name = groups.get("name1") or groups.get("name2") or groups.get("name3") or ""
        role = groups.get("role1") or groups.get("role2") or ""
        name = name.strip()
        if name and len(name.split()) <= 3 and not re.search(
                r"\b(Ltd|Limited|Inc|LLC|GmbH|Group|Team|We|You|Our|The|This|Please)\b", name):
            return name, role.strip()
    return "", ""


def enrich(job: Job, raw: RawJob) -> None:
    """Populate the contact/company columns on a Job in place."""
    description = raw.description or ""

    job.company_website = company_website(raw)
    job.careers_page, job.careers_page_guessed = careers_page(raw, job.company_website)
    job.linkedin = linkedin_company(raw.company)
    job.public_email = first_email(description)
    job.public_phone = first_phone(description)

    name, role = extract_contact(description)
    job.best_contact_name = name
    job.contact_role = role or ("Hiring contact" if name else "")

    job.application_url = raw.apply_url or raw.url
    job.original_job_url = raw.url or raw.apply_url

    if raw.extra.get("direct_employer") and not job.contact_role:
        job.contact_role = "Direct employer — no agency in between"

    job.description = truncate(description, 4000)
