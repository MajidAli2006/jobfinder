"""The registry of job platforms: what each needs, and what it costs to reach.

Setup cannot be one static list. Which platforms matter depends on where the
person is looking: Pakistan means Rozee, the Gulf means Bayt, Finland means
Duunitori. So the registry records, per platform, which countries it serves,
what credential it needs, and — where a plain request does not work — why.

Three levels of access, because the honest answer differs by platform:

  FREE       reachable now, no credential at all
  FREE_KEY   a key anyone can self-serve in minutes, no cost
  PARTNER    an approved partner key, sometimes paid
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import config
import contextlib

FREE = "free"
FREE_KEY = "free-key"
PARTNER = "partner"

ACCESS_LABEL = {
    FREE: "works now, no key",
    FREE_KEY: "free key, self-service",
    PARTNER: "partner key, approval needed",
}


@dataclass(frozen=True)
class Platform:
    """One job platform and how to reach it."""

    name: str
    label: str
    #: Countries it serves, lower-cased. Empty means it is not tied to a market.
    countries: tuple[str, ...] = ()
    #: Environment variables it needs. Empty means no credential at all.
    env: tuple[str, ...] = ()
    signup: str = ""
    note: str = ""
    #: True for platforms this package ships a connector for.
    built_in: bool = True
    access: str = FREE
    #: Why an ordinary request does not work, when it does not. Shown in setup
    #: so nobody spends an afternoon debugging a block that is by design.
    blocked: str = ""

    def serves(self, country: str) -> bool:
        if not self.countries or not country:
            return True
        return country.strip().lower() in self.countries

    def missing(self) -> tuple[str, ...]:
        return tuple(name for name in self.env if not os.environ.get(name, "").strip())

    @property
    def configured(self) -> bool:
        return not self.missing()


#: Credentials that apply wherever you search.
CORE = (
    Platform("anthropic", "Claude (compiles the request, judges fit)",
             env=("ANTHROPIC_API_KEY",), access=FREE_KEY,
             signup="https://console.anthropic.com/settings/keys",
             note="Without it a free-text request cannot be compiled into a search."),
    Platform("adzuna", "Adzuna", env=("ADZUNA_APP_ID", "ADZUNA_APP_KEY"),
             access=FREE_KEY, signup="https://developer.adzuna.com/",
             note="About twenty countries; pointed at whichever you search."),
    Platform("jooble", "Jooble", env=("JOOBLE_API_KEY",), access=FREE_KEY,
             signup="https://jooble.org/api/about", note="Worldwide aggregator."),
    Platform("careerjet", "Careerjet", env=("CAREERJET_API_KEY",), access=FREE_KEY,
             signup="https://www.careerjet.com/partners/api/",
             note="Worldwide, strong on trades."),
)

#: Reachable right now with no credential at all. This is the free MVP.
NO_KEY = (
    Platform("linkedin", "LinkedIn (public search)", access=FREE),
    Platform("employer_boards", "Employer ATS boards", access=FREE,
             note="Greenhouse, Lever, Ashby, Workable, Recruitee, SmartRecruiters, "
                  "Teamtailor — curated, plus any discovered during a run."),
    Platform("remote_boards", "Remote job boards", access=FREE,
             note="RemoteOK, Remotive, We Work Remotely, Himalayas, Jobicy, "
                  "Working Nomads, Arbeitnow, Jobspresso, NoDesk, DailyRemote."),
    Platform("hackernews", "Hacker News — who is hiring", access=FREE),
    Platform("structured", "Regional boards publishing job markup", access=FREE,
             note="Found per region. Yields where a board publishes schema.org "
                  "data — Jobberman in Nigeria and Shine in India do; many do not."),
)

#: The way past the boards that refuse direct requests. Google indexes them
#: because they publish job markup for exactly that purpose, and this is a
#: licensed API to that index — not a scraper.
VIA_GOOGLE = (
    Platform("google_jobs", "Google Jobs — via SerpApi", env=("SERPAPI_KEY",),
             access=PARTNER, signup="https://serpapi.com/",
             note="Reaches Indeed, Glassdoor, Bayt, Naukri, Rozee and foundit in "
                  "one call, in every country. 250 searches/month free, then $25. "
                  "The single highest-value key."),
    Platform("searchapi", "Google Jobs — via SearchApi.io (alternative)",
             env=("SEARCHAPI_KEY",), access=PARTNER,
             signup="https://www.searchapi.io/",
             note="Same Google data, different vendor. 100 free requests, then "
                  "$40/month. Set either key; whichever is present is used."),
    Platform("theirstack", "TheirStack job-postings API", access=PARTNER,
             built_in=False, signup="https://theirstack.com/en/job-posting-api",
             note="A different route: 231M postings across 195 countries direct "
                  "from Indeed, LinkedIn, Glassdoor and the ATS platforms, with "
                  "hiring contacts. $49/month for 1,500 credits. No connector "
                  "ships for it."),
)

#: Platforms tied to particular markets.
REGIONAL = (
    Platform("reed", "Reed.co.uk",
             countries=("united kingdom", "uk", "england", "scotland", "wales",
                        "northern ireland"),
             env=("REED_API_KEY",), access=FREE_KEY,
             signup="https://www.reed.co.uk/developers"),
    Platform("indeed", "Indeed", env=("INDEED_PUBLISHER_ID",), access=PARTNER,
             signup="https://developer.indeed.com/",
             blocked="serves a CAPTCHA to ordinary requests",
             note="The largest board in most countries. Reachable either with a "
                  "partner key, or through Google Jobs, which is one key for all "
                  "of these boards at once."),
    Platform("ziprecruiter", "ZipRecruiter",
             countries=("united states", "usa", "us", "canada"),
             env=("ZIPRECRUITER_API_KEY",), access=PARTNER,
             signup="https://www.ziprecruiter.com/partner",
             blocked="Cloudflare interstitial on ordinary requests"),
    Platform("bayt", "Bayt", countries=("united arab emirates", "uae", "saudi arabia",
                                        "qatar", "kuwait", "bahrain", "oman", "egypt",
                                        "jordan", "lebanon", "pakistan"),
             env=("BAYT_API_KEY",), access=PARTNER, built_in=False,
             signup="https://www.bayt.com/en/employer-services/",
             blocked="403 with a CAPTCHA",
             note="The main board across the Gulf and the Levant. Reachable through "
                  "Google Jobs."),
    Platform("naukri", "Naukri", countries=("india",), env=("NAUKRI_API_KEY",),
             access=PARTNER, built_in=False, signup="https://www.naukri.com/recruit/",
             blocked="renders its listings in the browser, so a fetch returns none",
             note="The largest board in India. Reachable through Google Jobs."),
    Platform("rozee", "Rozee.pk", countries=("pakistan",), env=("ROZEE_API_KEY",),
             access=PARTNER, built_in=False, signup="https://www.rozee.pk/employer",
             blocked="CAPTCHA on search pages",
             note="The main board in Pakistan. Reachable through Google Jobs."),
    Platform("seek", "SEEK", countries=("australia", "new zealand"),
             env=("SEEK_API_KEY",), access=PARTNER, built_in=False,
             signup="https://developer.seek.com/",
             note="The main board in Australia and New Zealand."),
    Platform("stepstone", "StepStone",
             countries=("germany", "austria", "switzerland", "netherlands", "belgium"),
             env=("STEPSTONE_API_KEY",), access=PARTNER, built_in=False,
             signup="https://www.stepstone.de/e-recruiting/",
             note="The main board in the German-speaking market."),
    Platform("duunitori", "Duunitori", countries=("finland",), access=FREE,
             built_in=False, signup="https://duunitori.fi/",
             note="Reached through regional discovery when it publishes markup."),
    Platform("jobbank", "Job Bank (Government of Canada)", countries=("canada",),
             access=FREE, built_in=False, signup="https://www.jobbank.gc.ca/",
             note="Public service; reached through regional discovery."),
    Platform("jobberman", "Jobberman", countries=("nigeria", "ghana", "kenya"),
             access=FREE, built_in=False, signup="https://www.jobberman.com/",
             note="Publishes job markup, so regional discovery reads it."),
    Platform("shine", "Shine.com", countries=("india",), access=FREE, built_in=False,
             signup="https://www.shine.com/",
             note="Publishes job markup, so regional discovery reads it."),
)

ALL = CORE + NO_KEY + VIA_GOOGLE + REGIONAL


def env_var_for(platform_name: str) -> str:
    """The environment variable a discovered platform's key goes in."""
    slug = "".join(ch if ch.isalnum() else "_" for ch in platform_name).strip("_")
    return f"JOBFINDER_{slug.upper()}_API_KEY"


def for_region(country: str = "") -> tuple[Platform, ...]:
    """Core platforms, plus those serving this country."""
    return tuple(p for p in ALL if p.serves(country))


def by_access(country: str = "") -> dict[str, tuple[Platform, ...]]:
    """Platforms relevant here, grouped by what it takes to reach them."""
    relevant = for_region(country)
    return {level: tuple(p for p in relevant if p.access == level)
            for level in (FREE, FREE_KEY, PARTNER)}


def needing_keys(country: str = "") -> tuple[Platform, ...]:
    """Platforms relevant here whose credentials are not set."""
    return tuple(p for p in for_region(country) if p.env and not p.configured)


def save_key(name: str, value: str, env_path: Path | None = None) -> Path:
    """Append a credential to the .env file, readable only by its owner.

    The value is never echoed back: the caller is told the variable name, not
    what was stored.
    """
    path = env_path or (config.ROOT / ".env")
    line = f"{name}={value.strip()}\n"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    path.write_text(existing + line, encoding="utf-8")
    with contextlib.suppress(OSError):
        path.chmod(0o600)
    return path
