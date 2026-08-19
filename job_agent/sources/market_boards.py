"""National job-market connectors. Each needs a free API credential and is skipped silently
when the corresponding environment variable is not set.
"""

from __future__ import annotations

import base64
import time

import requests

from .. import config
from ..models import RawJob
from ..utils import get_json, http_get, normalize_amount, parse_datetime, strip_html
from .base import Source


def _describe_http(status: int) -> str:
    """Turn an HTTP status from a credential probe into plain English."""
    if status in (401, 403):
        return f"key REJECTED (HTTP {status}) — wrong or inactive credentials"
    if status == 429:
        return "key accepted but rate-limited (HTTP 429) — try again shortly"
    if status >= 500:
        return f"provider error (HTTP {status}) — their side, not your key"
    return f"unexpected response (HTTP {status})"


ADZUNA_MARKETS = {
    "united kingdom": "gb", "uk": "gb", "great britain": "gb", "england": "gb",
    "scotland": "gb", "wales": "gb", "northern ireland": "gb",
    "united states": "us", "usa": "us", "us": "us", "america": "us",
    "australia": "au", "austria": "at", "belgium": "be", "brazil": "br",
    "canada": "ca", "switzerland": "ch", "germany": "de", "spain": "es",
    "france": "fr", "india": "in", "italy": "it", "mexico": "mx",
    "netherlands": "nl", "new zealand": "nz", "poland": "pl",
    "singapore": "sg", "south africa": "za",
}

ADZUNA_CURRENCY = {
    "gb": "GBP", "us": "USD", "au": "AUD", "at": "EUR", "be": "EUR",
    "br": "BRL", "ca": "CAD", "ch": "CHF", "de": "EUR", "es": "EUR",
    "fr": "EUR", "in": "INR", "it": "EUR", "mx": "MXN", "nl": "EUR",
    "nz": "NZD", "pl": "PLN", "sg": "SGD", "za": "ZAR",
}


class Adzuna(Source):
    """Adzuna. Free key: https://developer.adzuna.com/"""

    name = "adzuna"
    label = "Adzuna"
    URL = "https://api.adzuna.com/v1/api/jobs/{market}/search/1"
    credentials = ("ADZUNA_APP_ID", "ADZUNA_APP_KEY")
    markets = tuple(ADZUNA_MARKETS)

    def __init__(self) -> None:
        self.enabled = bool(config.ADZUNA_APP_ID and config.ADZUNA_APP_KEY)

    def endpoint(self, market: str) -> str:
        return self.URL.format(market=market)

    def target_markets(self) -> tuple[str, ...]:
        """The Adzuna country codes this search should be run against."""
        codes = []
        for country in self.wanted_countries():
            code = ADZUNA_MARKETS.get(country)
            if code and code not in codes:
                codes.append(code)
        return tuple(codes)

    def probe(self) -> tuple[bool, str]:
        missing = [name for name, value in (("ADZUNA_APP_ID", config.ADZUNA_APP_ID),
                                            ("ADZUNA_APP_KEY", config.ADZUNA_APP_KEY))
                   if not value]
        if missing:
            return False, f"not set: {', '.join(missing)}"
        markets = self.target_markets() or ("gb",)
        market = markets[0]
        query = self.queries(1)[0]
        try:
            resp = requests.get(self.endpoint(market), params={
                "app_id": config.ADZUNA_APP_ID,
                "app_key": config.ADZUNA_APP_KEY,
                "results_per_page": 1,
                "what": query,
            }, headers={"User-Agent": config.USER_AGENT}, timeout=20)
        except Exception as exc:  # noqa: BLE001
            return False, f"could not reach Adzuna ({type(exc).__name__})"
        if resp.status_code != 200:
            return False, _describe_http(resp.status_code)
        try:
            total = resp.json().get("count", "?")
        except ValueError:
            return False, "key accepted but the response was not JSON"
        return True, (f"key WORKS — Adzuna reports {total} '{query}' results "
                      f"in {market.upper()}")

    def fetch(self) -> list[RawJob]:
        if not self.enabled:
            return []
        markets = self.target_markets()
        if not markets:
            return []
        jobs: list[RawJob] = []
        for market, query in ((m, q) for m in markets for q in self.queries(4)):
            for item in self._results(market, query):
                jobs.append(self._to_raw(item, market))
        return self.dedupe_by_id(jobs)

    def _results(self, market: str, query: str) -> list:
        """One market/query search, or [] when the endpoint gives nothing usable."""
        data = get_json(self.endpoint(market), params={
            "app_id": config.ADZUNA_APP_ID,
            "app_key": config.ADZUNA_APP_KEY,
            "results_per_page": 50,
            "what": query,
            "max_days_old": self.max_age_days(),
            "content-type": "application/json",
        })
        time.sleep(config.HTTP_DELAY)
        return (data.get("results") or []) if isinstance(data, dict) else []

    @classmethod
    def _to_raw(cls, item: dict, market: str) -> RawJob:
        contract_time = (item.get("contract_time") or "").replace("_", " ")
        contract_type = (item.get("contract_type") or "").replace("_", " ")
        url = item.get("redirect_url") or ""
        return RawJob(
            source=cls.name,
            source_id=f"adzuna-{item.get('id')}",
            title=item.get("title") or "",
            company=(item.get("company") or {}).get("display_name") or "",
            url=url,
            apply_url=url,
            description=strip_html(item.get("description")),
            location_raw=(item.get("location") or {}).get("display_name") or market.upper(),
            posted_at=parse_datetime(item.get("created")),
            employment_type_raw=f"{contract_time} {contract_type}".strip(),
            salary_min=normalize_amount(item.get("salary_min")),
            salary_max=normalize_amount(item.get("salary_max")),
            salary_currency=ADZUNA_CURRENCY.get(market, ""),
            tags=[c for c in [(item.get("category") or {}).get("label")] if c],
            extra={"truncated_description": True},
        )


class Reed(Source):
    """Reed.co.uk. Free key: https://www.reed.co.uk/developers"""

    name = "reed"
    label = "Reed.co.uk"
    URL = "https://www.reed.co.uk/api/1.0/search"
    credentials = ("REED_API_KEY",)
    markets = ("united kingdom", "uk", "great britain", "england",
               "scotland", "wales", "northern ireland")

    def __init__(self) -> None:
        self.enabled = bool(config.REED_API_KEY)

    def probe(self) -> tuple[bool, str]:
        if not config.REED_API_KEY:
            return False, "not set: REED_API_KEY"
        token = base64.b64encode(f"{config.REED_API_KEY}:".encode()).decode()
        try:
            resp = requests.get(self.URL,
                                params={"keywords": self.queries(1)[0], "resultsToTake": 1},
                                headers={"Authorization": f"Basic {token}",
                                         "User-Agent": config.USER_AGENT}, timeout=20)
        except Exception as exc:  # noqa: BLE001
            return False, f"could not reach Reed ({type(exc).__name__})"
        if resp.status_code != 200:
            return False, _describe_http(resp.status_code)
        try:
            total = resp.json().get("totalResults", "?")
        except ValueError:
            return False, "key accepted but the response was not JSON"
        return True, f"key WORKS — Reed reports {total} '{self.queries(1)[0]}' results"

    def fetch(self) -> list[RawJob]:
        if not self.enabled:
            return []
        token = base64.b64encode(f"{config.REED_API_KEY}:".encode()).decode()
        headers = {"Authorization": f"Basic {token}"}
        jobs: list[RawJob] = []
        for query in self.queries(4):
            resp = http_get(self.URL, params={
                "keywords": query,
                "resultsToTake": 100,
                "postedByRecruitmentAgency": "false" if query == self.queries(1)[0] else "true",
            }, headers=headers)
            time.sleep(config.HTTP_DELAY)
            if resp is None:
                continue
            try:
                data = resp.json()
            except ValueError:
                continue
            for item in data.get("results") or []:
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"reed-{item.get('jobId')}",
                    title=item.get("jobTitle") or "",
                    company=item.get("employerName") or "",
                    url=item.get("jobUrl") or "",
                    apply_url=item.get("jobUrl") or "",
                    description=strip_html(item.get("jobDescription")),
                    location_raw=item.get("locationName") or "United Kingdom",
                    posted_at=parse_datetime(item.get("date")),
                    employment_type_raw=("Part Time" if item.get("partTime") else "")
                                        + (" Full Time" if item.get("fullTime") else ""),
                    salary_min=normalize_amount(item.get("minimumSalary")),
                    salary_max=normalize_amount(item.get("maximumSalary")),
                    salary_currency=(item.get("currency") or "GBP").upper(),
                    extra={"direct_employer": not item.get("postedByRecruitmentAgency", True),
                           "truncated_description": True},
                ))
        return self.dedupe_by_id(jobs)


class Jooble(Source):
    """Jooble aggregator. Free key: https://jooble.org/api/about"""

    name = "jooble"
    label = "Jooble"
    URL = "https://jooble.org/api/"
    credentials = ("JOOBLE_API_KEY",)

    def __init__(self) -> None:
        self.enabled = bool(config.JOOBLE_API_KEY)

    def probe(self) -> tuple[bool, str]:
        if not config.JOOBLE_API_KEY:
            return False, "not set: JOOBLE_API_KEY"
        try:
            resp = requests.post(
                self.URL + config.JOOBLE_API_KEY,
                json=self.payload(self.queries(1)[0]),
                headers={"Content-Type": "application/json", "User-Agent": config.USER_AGENT},
                timeout=20,
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"could not reach Jooble ({type(exc).__name__})"
        if resp.status_code != 200:
            return False, _describe_http(resp.status_code)
        try:
            total = resp.json().get("totalCount", "?")
        except ValueError:
            return False, "key accepted but the response was not JSON"
        return True, (f"key WORKS — Jooble reports {total} "
                      f"'{self.queries(1)[0]}' results in {self.where() or 'any location'}")

    def where(self) -> str:
        """The location to ask Jooble about, or "" to let it answer broadly."""
        wanted = self.wanted_countries()
        return wanted[0].title() if wanted else ""

    def payload(self, query: str, location: str | None = None) -> dict:
        body = {"keywords": query, "page": "1"}
        where = self.where() if location is None else location
        if where:
            body["location"] = where
        return body

    def search_terms(self) -> tuple[str, ...]:
        """Queries to run, narrowed to remote only when remote was asked for."""
        from .. import profile
        base = self.queries(3)
        if profile.active().remote_only:
            return tuple(f"{q} remote" for q in self.queries(2)) + base[1:]
        return base

    def _post(self, query: str, location: str | None = None) -> dict:
        """One search, or {} when Jooble cannot be reached or does not answer."""
        try:
            resp = requests.post(
                self.URL + config.JOOBLE_API_KEY,
                json=self.payload(query, location),
                headers={"Content-Type": "application/json", "User-Agent": config.USER_AGENT},
                timeout=config.HTTP_TIMEOUT,
            )
        except Exception:  # noqa: BLE001
            return {}
        time.sleep(config.HTTP_DELAY)
        if resp.status_code != 200:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    def _search(self, query: str) -> dict:
        """A located search, retried unlocated when the location finds nothing.

        Jooble's country filter answers for some markets and not others: asking
        for "Pakistan" or "Karachi" returns nothing while "Lahore" returns
        plenty, so a whole source went quiet for countries it actually covers.
        The unlocated results are filtered downstream on location like any
        other board's.
        """
        data = self._post(query)
        if data.get("jobs") or not self.where():
            return data
        return self._post(query, location="")

    def fetch(self) -> list[RawJob]:
        if not self.enabled:
            return []
        jobs: list[RawJob] = []
        for query in self.search_terms():
            data = self._search(query)
            for item in data.get("jobs") or []:
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"jooble-{item.get('id')}",
                    title=item.get("title") or "",
                    company=item.get("company") or "",
                    url=item.get("link") or "",
                    apply_url=item.get("link") or "",
                    description=strip_html(item.get("snippet")),
                    location_raw=item.get("location") or self.where(),
                    posted_at=parse_datetime(item.get("updated")),
                    employment_type_raw=item.get("type") or "",
                    salary_raw=item.get("salary") or "",
                    extra={"truncated_description": True},
                ))
        return self.dedupe_by_id(jobs)
