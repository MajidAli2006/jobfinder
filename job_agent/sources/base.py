"""Base class shared by all job sources."""

from __future__ import annotations

import logging
import time

from ..models import RawJob
from ..utils import scrub

log = logging.getLogger("job_agent.sources")

#: Every URL seen in this run, so later stages can mine them for employer
#: boards. Reset per run by `clear_seen()`.
_seen_urls: list[str] = []


def remember_urls(jobs: list[RawJob]) -> None:
    for job in jobs:
        for candidate in (job.url, job.apply_url, job.company_website):
            if candidate:
                _seen_urls.append(candidate)


def seen_urls() -> tuple[str, ...]:
    return tuple(_seen_urls)


def clear_seen() -> None:
    _seen_urls.clear()


class Source:
    """A job board connector."""

    name: str = "base"
    label: str = "Base"
    remote_by_default: bool = False
    enabled: bool = True

    credentials: tuple[str, ...] = ()

    @staticmethod
    def queries(limit: int = 6) -> tuple[str, ...]:
        """Search phrases for the active profile."""
        from .. import profile
        chosen = profile.active().search_queries or (profile.active().query,)
        return tuple(q for q in chosen[:limit] if q)

    markets: tuple[str, ...] = ()

    @staticmethod
    def wanted_countries() -> tuple[str, ...]:
        """Where the active search wants work, lower-cased."""
        from .. import profile
        active = profile.active()
        wanted = active.target_regions or ((active.home_country,) if active.home_country else ())
        return tuple(w.strip().lower() for w in wanted if w and w.strip())

    def serves_active_search(self) -> bool:
        """Can this board answer the question the active search is asking?"""
        if not self.markets:
            return True
        wanted = self.wanted_countries()
        if not wanted:
            return True
        return any(country in self.markets for country in wanted)

    @staticmethod
    def home_country() -> str:
        """Where the candidate works from, or "" when that is not known."""
        from .. import profile
        return profile.active().home_country

    def fetch(self) -> list[RawJob]:  # pragma: no cover - interface
        raise NotImplementedError

    def probe(self) -> tuple[bool, str]:
        """Live check that this source is usable right now."""
        return True, "no API key required"


    def collect(self) -> list[RawJob]:
        started = time.time()
        try:
            jobs = self.fetch() or []
        except Exception as exc:  # noqa: BLE001 - isolation is the point
            log.warning("source %s failed: %s", self.name, scrub(exc))
            raise
        finally:
            log.debug("source %s took %.1fs", self.name, time.time() - started)
        for job in jobs:
            job.source = self.name
            if self.remote_by_default:
                job.extra.setdefault("is_remote", True)
        remember_urls(jobs)
        return jobs

    @staticmethod
    def dedupe_by_id(jobs: list[RawJob]) -> list[RawJob]:
        seen: set[str] = set()
        out: list[RawJob] = []
        for job in jobs:
            key = job.source_id or job.url
            if key in seen:
                continue
            seen.add(key)
            out.append(job)
        return out
