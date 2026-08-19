"""Fetch the full advert for postings whose source only returned a snippet."""

from __future__ import annotations

import base64
import json
import logging
import re
import threading
import time
from queue import Empty, Queue
from collections.abc import Callable

from . import cache, config, filters
from .models import RawJob
from .utils import http_get, normalize, parse_datetime, strip_html

log = logging.getLogger("job_agent.descriptions")

_LD_BLOCK_RE = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S | re.I)

_CONTAINER_RES = (
    re.compile(r'<section[^>]+class="[^"]*\badp-body\b[^"]*"[^>]*>(.*?)</section>', re.S | re.I),
)

MIN_USEFUL_LENGTH = 400


class _HostPacer:
    """Keeps a minimum interval between requests to the same host."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            due = self._next.get(host, 0.0)
            delay = max(0.0, due - now)
            self._next[host] = max(now, due) + self.interval
        if delay:
            time.sleep(delay)


def job_posting_ld(html_text: str) -> dict | None:
    """Return the schema.org JobPosting object embedded in a page, if any."""
    for block in _LD_BLOCK_RE.findall(html_text):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for candidate in (data if isinstance(data, list) else [data]):
            if not isinstance(candidate, dict):
                continue
            if "JobPosting" in str(candidate.get("@type", "")):
                return candidate
            for node in candidate.get("@graph") or []:
                if isinstance(node, dict) and "JobPosting" in str(node.get("@type", "")):
                    return node
    return None


def advert_container(html_text: str) -> str:
    """Text of a known advert container, for pages carrying no JSON-LD."""
    for pattern in _CONTAINER_RES:
        match = pattern.search(html_text)
        if match:
            return strip_html(match.group(1))
    return ""


def _fetch_jsonld(raw: RawJob) -> dict | None:
    """Read the advert page: JobPosting JSON-LD first, named container second."""
    resp = http_get(raw.url, headers={"User-Agent": config.USER_AGENT},
                    timeout=20, retries=1)
    if resp is None:
        return None
    posting = job_posting_ld(resp.text)
    if not posting:
        body = advert_container(resp.text)
        if not body:
            return None
        return {"description": body, "posted_at": "", "employment_type": "",
                "applicants": ""}

    employment = posting.get("employmentType")
    if isinstance(employment, list):
        employment = ", ".join(str(e) for e in employment)

    return {
        "description": strip_html(posting.get("description") or ""),
        "posted_at": posting.get("datePosted") or "",
        "employment_type": (employment or "").replace("_", " ").title(),
        "applicants": "",
    }


def _fetch_reed(raw: RawJob) -> dict | None:
    """Reed publishes the full advert as data, so no page parsing is needed."""
    if not config.REED_API_KEY:
        return None
    job_id = raw.source_id.split("reed-", 1)[-1]
    if not job_id.isdigit():
        return None
    token = base64.b64encode(f"{config.REED_API_KEY}:".encode()).decode()
    resp = http_get(f"https://www.reed.co.uk/api/1.0/jobs/{job_id}",
                    headers={"Authorization": f"Basic {token}",
                             "User-Agent": config.USER_AGENT},
                    timeout=20, retries=1)
    if resp is None:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None

    count = data.get("applicationCount")
    applicants = f"{count} applicants" if isinstance(count, int) else ""

    return {
        "description": strip_html(data.get("jobDescription") or ""),
        "posted_at": data.get("datePosted") or "",
        "employment_type": ("Part Time" if data.get("partTime") else "")
                           + (" Full Time" if data.get("fullTime") else ""),
        "applicants": applicants,
    }


FETCHERS: dict[str, Callable[[RawJob], dict | None]] = {
    "reed": _fetch_reed,
    "adzuna": _fetch_jsonld,
}


def _cache_key(raw: RawJob) -> str:
    return f"desc:{raw.source}:{raw.source_id}"


def _apply(raw: RawJob, payload: dict) -> bool:
    """Merge a fetched payload into the posting. True when it added a real body."""
    text = (payload.get("description") or "").strip()
    if len(text) < MIN_USEFUL_LENGTH or len(text) <= len(raw.description or ""):
        return False

    raw.description = text[:12000]
    raw.extra["truncated_description"] = False

    if raw.posted_at is None and payload.get("posted_at"):
        raw.posted_at = parse_datetime(payload["posted_at"])
    if payload.get("employment_type") and not raw.employment_type_raw.strip():
        raw.employment_type_raw = payload["employment_type"]
    if payload.get("applicants"):
        raw.extra["applicants"] = payload["applicants"]
    return True


def _priority(raw: RawJob) -> tuple:
    """Best rows first, so a capped fetch budget is spent where it pays."""
    from . import profile
    active = profile.active()

    title = normalize(raw.title)
    location = normalize(raw.location_raw)

    home = active.home_terms + active.home_city_terms
    at_home = any(term in location for term in home) if home else False

    on_target = any(term in title for term in active.core_terms)

    return (
        1 if at_home else 0,
        1 if on_target else 0,
        raw.posted_at.timestamp() if raw.posted_at else 0,
    )


def candidates(raws: list[RawJob]) -> list[RawJob]:
    """Postings worth spending a fetch on, best first."""
    pending = [
        raw for raw in raws
        if raw.extra.get("truncated_description")
        and raw.source in FETCHERS
        and raw.url
        and filters.check_relevance(raw).passed
    ]
    return sorted(pending, key=_priority, reverse=True)


def fill_descriptions(raws: list[RawJob], budget: int | None = None) -> int:
    """Replace truncated descriptions with the full advert. Returns the count filled."""
    pending = candidates(raws)
    if not pending:
        return 0

    filled = 0
    to_fetch: list[RawJob] = []
    limit = config.DESCRIPTION_MAX_FETCHES if budget is None else budget

    for raw in pending:
        key = _cache_key(raw)
        cached = cache.get(key, config.DESCRIPTION_CACHE_DAYS)
        if cached:
            if cached.get("applicants") and not cache.get(
                    key, config.LINKEDIN_APPLICANTS_CACHE_DAYS):
                cached = {**cached, "applicants": ""}
            if _apply(raw, cached):
                filled += 1
            continue
        if len(to_fetch) < limit:
            to_fetch.append(raw)

    log.info("descriptions: %d restored from cache, %d to fetch", filled, len(to_fetch))
    if not to_fetch:
        return filled

    pacer = _HostPacer(config.DESCRIPTION_DELAY)
    queue: Queue = Queue()
    for raw in to_fetch:
        queue.put(raw)

    fetched_lock = threading.Lock()
    fetched = [0]

    def worker() -> None:
        while True:
            try:
                raw = queue.get_nowait()
            except Empty:
                return
            try:
                pacer.wait(raw.source)
                payload = FETCHERS[raw.source](raw)
                if payload:
                    cache.put(_cache_key(raw), payload)
                    if _apply(raw, payload):
                        with fetched_lock:
                            fetched[0] += 1
            except Exception:  # noqa: BLE001 - one bad advert must not stop the rest
                log.debug("description fetch failed for %s", raw.source_id)

    threads = [threading.Thread(target=worker, daemon=True)
               for _ in range(max(1, config.DESCRIPTION_WORKERS))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    log.info("descriptions: %d full adverts fetched", fetched[0])
    return filled + fetched[0]
