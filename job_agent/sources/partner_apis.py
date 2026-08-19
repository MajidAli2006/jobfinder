"""Boards reachable only through a paid or approved programme.

Indeed and ZipRecruiter serve a CAPTCHA to ordinary requests, so each stays
dormant until its partner key is set. Glassdoor has no connector: its API was
retired when Indeed acquired it.

GoogleJobs supersedes all three. Those boards publish their listings to
Google's index deliberately, and SerpApi is a licensed API to that index, so
one key reaches them without scraping any of them.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field

import requests

from .. import config
from ..models import RawJob
from ..utils import get_json, normalize_amount, parse_datetime, strip_html
from .base import Source


class Indeed(Source):
    """Indeed, through the publisher/partner API.

    Apply at https://developer.indeed.com/ — access is granted per publisher.
    """

    name = "indeed"
    label = "Indeed (partner API)"
    URL = "https://api.indeed.com/ads/apisearch"
    credentials = ("INDEED_PUBLISHER_ID",)

    def __init__(self) -> None:
        self.enabled = bool(config.INDEED_PUBLISHER_ID)

    def location(self) -> str:
        wanted = self.wanted_countries()
        return wanted[0].title() if wanted else ""

    def probe(self) -> tuple[bool, str]:
        if not config.INDEED_PUBLISHER_ID:
            return False, ("not set: INDEED_PUBLISHER_ID — Indeed blocks ordinary "
                           "requests, so a partner key is the only route "
                           "(https://developer.indeed.com/)")
        try:
            resp = requests.get(self.URL, params=self._params(self.queries(1)[0], 1),
                                headers={"User-Agent": config.USER_AGENT}, timeout=20)
        except Exception as exc:  # noqa: BLE001
            return False, f"could not reach Indeed ({type(exc).__name__})"
        if resp.status_code != 200:
            return False, f"publisher id rejected (HTTP {resp.status_code})"
        return True, "publisher id WORKS"

    def _params(self, query: str, limit: int) -> dict:
        return {
            "publisher": config.INDEED_PUBLISHER_ID,
            "q": query,
            "l": self.location(),
            "limit": limit,
            "fromage": self.max_age_days(),
            "format": "json",
            "v": "2",
            "userip": "1.2.3.4",
            "useragent": config.USER_AGENT,
        }

    def fetch(self) -> list[RawJob]:
        if not self.enabled:
            return []
        jobs: list[RawJob] = []
        for query in self.queries(4):
            data = get_json(self.URL, params=self._params(query, 50))
            time.sleep(config.HTTP_DELAY)
            if not isinstance(data, dict):
                continue
            for item in data.get("results") or []:
                url = item.get("url") or ""
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"indeed-{item.get('jobkey')}",
                    title=item.get("jobtitle") or "",
                    company=item.get("company") or "Undisclosed",
                    url=url,
                    apply_url=url,
                    description=strip_html(item.get("snippet")),
                    location_raw=item.get("formattedLocation") or self.location(),
                    posted_at=parse_datetime(item.get("date")),
                    extra={"truncated_description": True},
                ))
        return self.dedupe_by_id(jobs)


class ZipRecruiter(Source):
    """ZipRecruiter, through the partner API.

    Apply at https://www.ziprecruiter.com/partner — a plain request is served
    a Cloudflare interstitial instead of listings.
    """

    name = "ziprecruiter"
    label = "ZipRecruiter (partner API)"
    URL = "https://api.ziprecruiter.com/jobs/v1"
    credentials = ("ZIPRECRUITER_API_KEY",)
    markets = ("united states", "usa", "us", "canada")

    def __init__(self) -> None:
        self.enabled = bool(config.ZIPRECRUITER_API_KEY)

    def location(self) -> str:
        wanted = self.wanted_countries()
        return wanted[0].title() if wanted else ""

    def probe(self) -> tuple[bool, str]:
        if not config.ZIPRECRUITER_API_KEY:
            return False, ("not set: ZIPRECRUITER_API_KEY — ZipRecruiter blocks "
                           "ordinary requests, so a partner key is the only route "
                           "(https://www.ziprecruiter.com/partner)")
        try:
            resp = requests.get(self.URL, params=self._params(self.queries(1)[0], 1),
                                headers={"User-Agent": config.USER_AGENT}, timeout=20)
        except Exception as exc:  # noqa: BLE001
            return False, f"could not reach ZipRecruiter ({type(exc).__name__})"
        if resp.status_code != 200:
            return False, f"key rejected (HTTP {resp.status_code})"
        return True, "key WORKS"

    def _params(self, query: str, per_page: int) -> dict:
        return {
            "api_key": config.ZIPRECRUITER_API_KEY,
            "search": query,
            "location": self.location(),
            "days_ago": self.max_age_days(),
            "jobs_per_page": per_page,
            "page": 1,
        }

    def fetch(self) -> list[RawJob]:
        if not self.enabled:
            return []
        jobs: list[RawJob] = []
        for query in self.queries(4):
            data = get_json(self.URL, params=self._params(query, 100))
            time.sleep(config.HTTP_DELAY)
            if not isinstance(data, dict):
                continue
            for item in data.get("jobs") or []:
                url = item.get("url") or ""
                salary_min = normalize_amount(item.get("salary_min"))
                salary_max = normalize_amount(item.get("salary_max"))
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"zip-{item.get('id')}",
                    title=item.get("name") or "",
                    company=(item.get("hiring_company") or {}).get("name") or "Undisclosed",
                    url=url,
                    apply_url=url,
                    description=strip_html(item.get("snippet")),
                    location_raw=item.get("location") or self.location(),
                    posted_at=parse_datetime(item.get("posted_time")),
                    salary_min=salary_min,
                    salary_max=salary_max,
                    salary_currency="USD" if (salary_min or salary_max) else "",
                    extra={"truncated_description": True},
                ))
        return self.dedupe_by_id(jobs)


@dataclass(frozen=True)
class SerpProvider:
    """One vendor selling access to Google's job index.

    They all return the same listings — Google's — so the choice is price and
    free allowance, not coverage. Keeping them behind one interface means a
    change of vendor is a change of key, not of code.
    """

    name: str
    label: str
    env: str
    url: str
    signup: str
    #: Extra query parameters this vendor needs beyond query, location and key.
    extra: dict = field(default_factory=dict)
    key_param: str = "api_key"
    results_key: str = "jobs_results"
    verified: bool = False

    @property
    def key(self) -> str:
        return os.environ.get(self.env, "").strip()


PROVIDERS = (
    SerpProvider(
        name="serpapi", label="SerpApi", env="SERPAPI_KEY",
        url="https://serpapi.com/search.json", signup="https://serpapi.com/",
        extra={"engine": "google_jobs"}, verified=True,
    ),
    SerpProvider(
        name="searchapi", label="SearchApi.io", env="SEARCHAPI_KEY",
        url="https://www.searchapi.io/api/v1/search",
        signup="https://www.searchapi.io/",
        extra={"engine": "google_jobs"}, results_key="jobs", verified=True,
    ),
)


def active_provider() -> SerpProvider | None:
    """The first vendor with a key set, or None."""
    return next((p for p in PROVIDERS if p.key), None)


#: Words that mean the vendor turned the request away, rather than simply
#: having nothing to return for it.
REFUSAL_WORDS = re.compile(
    r"api[ _-]?key|unauthoriz|unauthentic|invalid key|forbidden|quota|credit|"
    r"rate limit|billing|subscription",
    re.IGNORECASE,
)


class GoogleJobs(Source):
    """Google's job index, through whichever vendor has a key configured.

    The boards that serve a CAPTCHA to a direct request still publish to
    Google, because that is how they get applicants. Reading Google's results
    reaches them the way they intend.
    """

    name = "google_jobs"
    label = "Google Jobs"
    credentials = tuple(p.env for p in PROVIDERS)

    def __init__(self) -> None:
        self.provider = active_provider()
        self.enabled = self.provider is not None

    def location(self) -> str:
        wanted = self.wanted_countries()
        return wanted[0].title() if wanted else ""

    def probe(self) -> tuple[bool, str]:
        provider = self.provider
        if provider is None:
            names = " or ".join(f"{p.env} ({p.signup})" for p in PROVIDERS)
            return False, (f"not set: {names} — this is the licensed route to "
                           f"Indeed, Glassdoor, Bayt and Naukri, which all refuse "
                           f"direct requests")
        data = get_json(provider.url, params=self._params(self.queries(1)[0]))
        if not isinstance(data, dict):
            return False, self._no_json_reason(provider)

        found = len(data.get(provider.results_key) or [])
        if found:
            return True, f"{provider.label} key WORKS — {found} results for a test query"

        # An answered request that carries an error is the vendor talking about
        # the query, not the key — unless it names the key or the allowance.
        error = str(data.get("error") or "")
        if REFUSAL_WORDS.search(error):
            return False, f"{provider.label} refused the request: {error[:70]}"
        detail = f" ({error[:60]})" if error else ""
        return True, (f"{provider.label} key works — no results for the test query"
                      f"{detail}. Coverage varies by country and wording")

    def _no_json_reason(self, provider) -> str:
        """Whether the vendor refused the key or could not be reached at all.

        Worth one extra request: every non-200 otherwise reads as "could not
        reach", and a key pasted into the wrong vendor's variable is the most
        likely cause by far.
        """
        try:
            resp = requests.get(provider.url, params=self._params(self.queries(1)[0]),
                                timeout=config.HTTP_TIMEOUT)
        except requests.RequestException as exc:
            return f"could not reach {provider.label}: {type(exc).__name__}"
        if resp.status_code in (401, 403):
            others = " or ".join(p.env for p in PROVIDERS if p is not provider)
            return (f"{provider.label} rejected the key (HTTP {resp.status_code}). "
                    f"Check it is a {provider.label} key — another vendor's belongs "
                    f"in {others}. Keys: {provider.signup}")
        return f"{provider.label} returned HTTP {resp.status_code}"

    def _params(self, query: str) -> dict:
        provider = self.provider
        params = {"q": query, provider.key_param: provider.key, **provider.extra}
        where = self.location()
        if where:
            params["location"] = where
        return params

    def pages(self) -> int:
        """How deep to page. Each page is one billed search."""
        return max(1, config.GOOGLE_JOBS_PAGES)

    @staticmethod
    def _apply_url(item: dict) -> str:
        """The link to apply, however this vendor spells it."""
        for option in item.get("apply_options") or []:
            link = option.get("link")
            if link:
                return link
        for name in ("apply_link", "share_link", "sharing_link", "link"):
            if item.get(name):
                return item[name]
        return ""

    def fetch(self) -> list[RawJob]:
        provider = self.provider
        if provider is None:
            return []
        jobs: list[RawJob] = []
        for query in self.queries(3):
            token = ""
            for _page in range(self.pages()):
                params = self._params(query)
                if token:
                    params["next_page_token"] = token
                data = get_json(provider.url, params=params)
                time.sleep(config.HTTP_DELAY)
                if not isinstance(data, dict) or data.get("error"):
                    break
                jobs.extend(self._postings(data, provider))
                token = (data.get("serpapi_pagination") or data.get("pagination")
                         or {}).get("next_page_token") or ""
                if not token:
                    break
        return self.dedupe_by_id(jobs)

    def _postings(self, data: dict, provider: SerpProvider) -> list[RawJob]:
        out: list[RawJob] = []
        for item in data.get(provider.results_key) or []:
            extensions = item.get("detected_extensions") or {}
            url = self._apply_url(item)
            if not url:
                continue
            out.append(RawJob(
                source=self.name,
                source_id=f"gj-{item.get('job_id') or url}",
                title=item.get("title") or "",
                company=item.get("company_name") or "Undisclosed",
                url=url,
                apply_url=url,
                description=strip_html(item.get("description")),
                location_raw=item.get("location") or self.location(),
                posted_at=parse_datetime(extensions.get("posted_at")),
                employment_type_raw=(extensions.get("schedule_type")
                                     or extensions.get("schedule") or ""),
                salary_raw=extensions.get("salary") or "",
                tags=[via for via in [(item.get("via") or "").replace("via ", "")] if via],
                extra={"aggregated_by": provider.name, "truncated_description": False},
            ))
        return out
