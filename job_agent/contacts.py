"""Published contact details for an employer, gathered from their own site.

An application that reaches a named person beats one that joins a queue, and
small employers publish the details needed to do that: a careers mailbox, a
founder's name on an About page. This reads what an employer has chosen to
publish, nothing more.

TLS verification stays on, and mailboxes that exist for legal or privacy
correspondence are excluded rather than harvested.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlparse

from . import config
from .utils import http_get

EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

#: Addresses that are not a route to a hiring conversation, and addresses that
#: are not really addresses at all.
UNWANTED_EMAIL = re.compile(
    r"(example|sentry|wixpress|godaddy|schema\.org|w3\.org|@\dx|"
    r"\.(png|jpe?g|svg|webp|gif|webm|mp4|css|js|woff2?)$|"
    r"^(noreply|no-reply|donotreply|postmaster|abuse|dmarc|privacy|legal|gdpr|dpo|"
    r"security|unsubscribe))", re.IGNORECASE)

VALID_TLD = re.compile(
    r"\.(com|co\.uk|org\.uk|io|ai|dev|net|org|de|fr|es|it|nl|se|no|dk|fi|pl|pt|ie|"
    r"ch|at|be|eu|tech|app|co|me|us|ca|au|nz|sg|ae|ng|gh|ke|za|in|pk|br|mx)$",
    re.IGNORECASE)

ROLE = (r"(?:Co[- ]?Founder|Founder|CEO|CTO|Chief Technology Officer|Chief Executive|"
        r"Managing Director|Head of Engineering|VP of Engineering|Engineering Manager|"
        r"Head of Talent|Head of People|Talent Partner|Technical Recruiter|"
        r"Hiring Manager|Operations Manager|Site Manager|Branch Manager|Owner)")

_NAME = r"[A-Z][a-zà-ÿ'\-]{2,15}(?:\s+[A-Z][a-zA-Zà-ÿ'\-]{1,18}){1,2}"

NAME_THEN_ROLE = re.compile(rf"({_NAME})\s*(?:,|–|—|\||\s-\s)\s*({ROLE})\b")
ROLE_THEN_NAME = re.compile(rf"({ROLE})\s*[:\-–—]\s*({_NAME})")

#: Pages an employer usually publishes contact details on.
PAGES = ("", "/about", "/about-us", "/team", "/our-team", "/company",
         "/contact", "/contact-us", "/people", "/careers")

MAX_PAGES = 6


def rank_email(address: str) -> int:
    """Lower sorts first: a careers mailbox beats a sales one."""
    local = address.split("@")[0].lower()
    if re.match(r"(careers?|jobs?|talent|recruit|hiring|hr|people|work|join|apply)", local):
        return 0
    if re.match(r"(hello|hi|contact|info|team|enquir|general|office|admin)", local):
        return 1
    if re.match(r"(sales|support|help|billing|press|media|marketing)", local):
        return 3
    return 2


def usable_email(address: str) -> bool:
    address = address.strip().strip(".")
    if not address or len(address) > 60:
        return False
    if UNWANTED_EMAIL.search(address):
        return False
    return bool(VALID_TLD.search(address))


def emails_in(text: str) -> list[str]:
    found = {a.strip().strip(".").lower() for a in EMAIL.findall(text)}
    return sorted((a for a in found if usable_email(a)), key=lambda a: (rank_email(a), a))


def people_in(markup: str) -> list[str]:
    """Named people with a role, as "Name — Role"."""
    # Unescape before collapsing: &nbsp; only becomes whitespace once
    # decoded, and otherwise survives inside a name as \xa0.
    text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", markup)))
    found: list[str] = []
    for match in NAME_THEN_ROLE.finditer(text):
        found.append(f"{match.group(1)} — {match.group(2)}")
    for match in ROLE_THEN_NAME.finditer(text):
        found.append(f"{match.group(2)} — {match.group(1)}")
    return sorted(dict.fromkeys(found))


def _same_site(base: str, url: str) -> bool:
    try:
        return urlparse(base).hostname == urlparse(url).hostname
    except ValueError:
        return False


def for_site(website: str, *, careers: str = "") -> dict[str, list[str]]:
    """Emails and named people published on an employer's own site.

    Never raises and never follows off-site links. A site that publishes
    nothing returns empty lists, which is a real answer.
    """
    if not website or not website.startswith("https://"):
        return {"emails": [], "people": []}

    base = website.rstrip("/")
    pages = [base + suffix for suffix in PAGES]
    if careers and _same_site(base, careers):
        pages.insert(1, careers)

    emails: list[str] = []
    people: list[str] = []
    for url in pages[:MAX_PAGES]:
        response = http_get(url)
        if response is None:
            continue
        try:
            markup = response.text
        except (UnicodeDecodeError, ValueError):
            continue
        emails.extend(emails_in(markup))
        people.extend(people_in(markup))

    ordered = sorted(dict.fromkeys(emails), key=lambda a: (rank_email(a), a))
    return {"emails": ordered[:4], "people": list(dict.fromkeys(people))[:4]}


def enrich(job) -> None:
    """Fill in a job's contact details from the employer's site, if it has any."""
    if not config.FETCH_EMPLOYER_CONTACTS:
        return
    if job.public_email and job.best_contact_name:
        return
    found = for_site(job.company_website, careers=job.careers_page)
    if found["emails"] and not job.public_email:
        job.public_email = found["emails"][0]
    if found["people"] and not job.best_contact_name:
        name, _, role = found["people"][0].partition(" — ")
        job.best_contact_name = name.strip()
        job.contact_role = role.strip()
