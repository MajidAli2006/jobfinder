"""Jobgether connector."""

from __future__ import annotations

import json
import re
import time

from .. import config
from ..models import RawJob
from ..utils import http_get, strip_html
from .base import Source

_OFFER_RE = re.compile(r'href="(/offer/[^"]+)"')
_AVAILABLE_FROM_RE = re.compile(
    r"the offer is available from[:\s]*([^\n.]{2,200})", re.IGNORECASE)
def _relevant(text: str) -> bool:
    """Cheap pre-filter before an offer page is fetched."""
    from .. import profile
    active = profile.active()
    terms = tuple(active.core_terms) + tuple(active.secondary_terms)[:12]
    low = text.lower()
    return any(term in low for term in terms) if terms else True


class Jobgether(Source):
    name = "jobgether"
    label = "Jobgether (remote aggregator)"
    remote_by_default = True

    BASE = "https://jobgether.com"

    @classmethod
    def listings(cls) -> tuple[str, ...]:
        """Listing paths built from the active profile."""
        country = cls.home_country().lower().replace(" ", "-")
        out: list[str] = []
        for query in cls.queries(4):
            slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")
            if not slug:
                continue
            out += [
                f"/remote-jobs/{country}/{slug}",
                f"/remote-jobs/{slug}",
                f"/remote-jobs/worldwide/{slug}",
                f"/search-offers?skill={slug}",
            ]
        return tuple(dict.fromkeys(out))

    MAX_OFFERS = 120

    HEADERS = {
        "User-Agent": config.USER_AGENT,
        "Accept-Language": "en-GB,en;q=0.9",
    }

    def fetch(self) -> list[RawJob]:
        paths: dict[str, None] = {}
        for listing in self.listings():
            resp = http_get(self.BASE + listing, headers=self.HEADERS, timeout=20, retries=1)
            time.sleep(config.HTTP_DELAY)
            if resp is None:
                continue
            for match in _OFFER_RE.finditer(resp.text):
                paths.setdefault(match.group(1), None)

        ordered = sorted(paths, key=lambda p: 0 if _relevant(p) else 1)

        jobs: list[RawJob] = []
        for path in ordered[: self.MAX_OFFERS]:
            job = self._read_offer(path)
            time.sleep(config.HTTP_DELAY)
            if job is not None:
                jobs.append(job)
        return self.dedupe_by_id(jobs)


    def _read_offer(self, path: str) -> RawJob | None:
        url = self.BASE + path
        resp = http_get(url, headers=self.HEADERS, timeout=20, retries=1)
        if resp is None:
            return None

        posting = self._job_posting(resp.text)
        if not posting:
            return None

        org = posting.get("hiringOrganization") or {}
        description = strip_html(posting.get("description") or "")
        if not description:
            return None

        available = _AVAILABLE_FROM_RE.search(description)
        location = available.group(1).strip() if available else ""

        employment = posting.get("employmentType")
        if isinstance(employment, list):
            employment = ", ".join(str(e) for e in employment)

        return RawJob(
            source=self.name,
            source_id=f"jobgether-{path.rsplit('/', 1)[-1][:60]}",
            title=posting.get("title") or "",
            company=(org.get("name") if isinstance(org, dict) else "") or "Undisclosed",
            url=url,
            apply_url=url,
            description=description,
            location_raw=location or self._location(posting),
            posted_at=_parse_date(posting.get("datePosted")),
            employment_type_raw=(employment or "").replace("_", " ").title(),
            company_website=(org.get("sameAs") if isinstance(org, dict) else "") or "",
            extra={"is_remote": True, "available_from": location},
        )

    @staticmethod
    def _job_posting(html_text: str) -> dict | None:
        """Pull the JobPosting object out of the page's JSON-LD."""
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                                html_text, re.S):
            try:
                data = json.loads(block)
            except ValueError:
                continue
            for candidate in (data if isinstance(data, list) else [data]):
                if not isinstance(candidate, dict):
                    continue
                if candidate.get("@type") == "JobPosting":
                    return candidate
                for node in candidate.get("@graph") or []:
                    if isinstance(node, dict) and node.get("@type") == "JobPosting":
                        return node
        return None

    @staticmethod
    def _location(posting: dict) -> str:
        raw = posting.get("jobLocation") or posting.get("applicantLocationRequirements")
        if not raw:
            return "Remote"
        names: list[str] = []
        for entry in (raw if isinstance(raw, list) else [raw]):
            if not isinstance(entry, dict):
                continue
            if entry.get("name"):
                names.append(str(entry["name"]))
            address = entry.get("address")
            if isinstance(address, dict) and address.get("addressCountry"):
                names.append(str(address["addressCountry"]))
        return ", ".join(dict.fromkeys(names)) or "Remote"


def _parse_date(value):
    from ..utils import parse_datetime
    return parse_datetime(value)
