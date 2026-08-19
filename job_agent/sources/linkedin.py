"""LinkedIn and Careerjet connectors."""

from __future__ import annotations

import base64
import html
import logging
import re
import time
from queue import Empty, Queue
from threading import Lock, Thread

import requests

from .. import cache, config
from ..models import RawJob
from ..utils import http_get, normalize, parse_datetime, strip_html
from .base import Source

log = logging.getLogger("job_agent.sources")

_CARD_RE = re.compile(r"<li>(.*?)</li>", re.S)
_URN_RE = re.compile(r'data-entity-urn="urn:li:jobPosting:(\d+)"')


def _duration(seconds: float) -> str:
    """Human-readable span, e.g. "45s" or "12m30s"."""
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _elapsed(started: float) -> str:
    return _duration(time.time() - started)


def _progress(done: int, total: int, started: float) -> str:
    """"3m20s · ~7m left" — the estimate is measured from the work actually completed, so
    it corrects itself rather than guessing up front. An estimate derived from the
    configured delay alone ignores network latency and reads far too optimistic.
    """
    spent = time.time() - started
    text = _duration(spent)
    if done and done < total:
        remaining = spent / done * (total - done)
        text += f" · ~{_duration(remaining)} left"
    return text


def _text(pattern: str, blob: str) -> str:
    match = re.search(pattern, blob, re.S)
    if not match:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", " ", match.group(1))).strip()


class LinkedIn(Source):
    """Remote-filtered LinkedIn job search via the public guest endpoints."""

    name = "linkedin"
    label = "LinkedIn (public guest search)"

    SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
    DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"

    SECONDARY_GEOS = 8

    def primary_keywords(self) -> tuple[str, ...]:
        """The search's own phrases — what an advert for this job is titled."""
        return self.queries(5)

    def secondary_keywords(self) -> tuple[str, ...]:
        """Broader phrases for the same work, to catch adverts titled differently."""
        from .. import profile
        active = profile.active()
        broader = tuple(term for term in active.secondary_terms if " " in term)
        return broader[:9] or self.queries(3)

    GEOS = (
        ("United Kingdom", "101165590"),
        ("Worldwide", "92000000"),
        ("Europe", "91000000"),
        ("United States", "103644278"),
        ("Canada", "101174742"),
        ("Ireland", "104738515"),
        ("Germany", "101282230"),
        ("Netherlands", "102890719"),
        ("Switzerland", "106693272"),
        ("Sweden", "105117694"),
        ("Norway", "103819153"),
        ("Denmark", "104514075"),
        ("Spain", "105646813"),
        ("Portugal", "100364837"),
        ("Poland", "105072130"),
        ("Estonia", "102974008"),
        ("United Arab Emirates", "104305776"),
        ("Saudi Arabia", "100459316"),
        ("Qatar", "104170880"),
        ("Australia", "101452733"),
        ("New Zealand", "105490917"),
        ("Singapore", "102454443"),
    )

    UNIVERSAL_GEOS = ("Worldwide",)

    CONTINENT_GEOS = {
        "Europe": {
            "united kingdom", "ireland", "germany", "netherlands", "switzerland",
            "sweden", "norway", "denmark", "spain", "portugal", "poland",
            "estonia", "france", "italy", "belgium", "austria", "czechia",
            "romania", "finland", "ukraine",
        },
    }

    def geos(self) -> tuple[tuple[str, str], ...]:
        """The geos worth searching for the active profile."""
        from .. import profile

        active = profile.active()
        wanted = {r.lower() for r in active.target_regions}
        if active.home_country:
            wanted.add(active.home_country.lower())
        if not wanted:
            return self.GEOS

        keep = set(self.UNIVERSAL_GEOS)
        keep |= {name for name, _ in self.GEOS if name.lower() in wanted}
        for continent, members in self.CONTINENT_GEOS.items():
            if wanted & members:
                keep.add(continent)

        chosen = tuple(g for g in self.GEOS if g[0] in keep)
        return chosen if len(chosen) > len(self.UNIVERSAL_GEOS) else self.GEOS

    JOB_TYPES = (("C", "Contract"), ("P", "Part Time"), ("T", "Temporary"))
    JOB_TYPE_KEYWORDS: tuple[str, ...] = ()
    JOB_TYPE_GEOS = 6

    PAGES = (0, 25)
    DEEP_PAGES = (0, 25, 50)

    PROGRESS_EVERY = 25

    MAX_DETAILS: int | None = None
    DETAIL_DELAY = config.LINKEDIN_DETAIL_DELAY

    HEADERS = {
        "User-Agent": config.USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-GB,en;q=0.9",
    }

    def __init__(self, deep: bool | None = None) -> None:
        self.deep = config.LINKEDIN_DEEP if deep is None else deep
        self.cache_hits = 0

    def fetch(self) -> list[RawJob]:
        seen: dict[str, RawJob] = {}
        seen_lock = Lock()

        tasks = self._search_tasks()
        log.info("  linkedin: %d searches queued (%d keywords x regions)",
                 len(tasks),
                 len(self.primary_keywords()) + len(self.secondary_keywords()))
        self._run_pool(tasks, lambda task: self._sweep(seen, seen_lock, *task),
                       config.LINKEDIN_SEARCH_WORKERS, "search",
                       note=lambda: f" · {len(seen)} cards")

        pending = self._select_pending(seen)
        if not pending:
            return list(seen.values())

        log.info("  linkedin: fetching %d descriptions (%d workers)",
                 len(pending), max(1, config.LINKEDIN_DETAIL_WORKERS))
        self._run_pool(pending, self._fetch_detail,
                       config.LINKEDIN_DETAIL_WORKERS, "descriptions")
        return list(seen.values())

    def _search_tasks(self) -> list[tuple[str, str, dict | None, str]]:
        """Every (keywords, region, extra params, label) search this run will make.

        Primary keywords sweep every region; the weaker sweeps are capped to the
        first few regions unless this is a deep run.
        """
        tasks: list[tuple[str, str, dict | None, str]] = []
        all_geos = self.geos()
        for keywords in self.primary_keywords():
            for _name, geo_id in all_geos:
                tasks.append((keywords, geo_id, None, ""))

        secondary_geos = all_geos if self.deep else all_geos[: self.SECONDARY_GEOS]
        for keywords in self.secondary_keywords():
            for _name, geo_id in secondary_geos:
                tasks.append((keywords, geo_id, None, ""))

        job_type_geos = all_geos if self.deep else all_geos[: self.JOB_TYPE_GEOS]
        for code, label in self.JOB_TYPES:
            for keywords in (self.JOB_TYPE_KEYWORDS or self.queries(4)):
                for _name, geo_id in job_type_geos:
                    tasks.append((keywords, geo_id, {"f_JT": code}, label))
        return tasks

    def _sweep(self, seen: dict[str, RawJob], seen_lock: Lock, keywords: str,
               geo_id: str, extra: dict | None = None, label: str = "") -> None:
        """Page through one search, adding cards not already seen.

        Stops early on a dead response or a short page: LinkedIn returns fewer
        than ten cards only on the last page of results.
        """
        window = f"r{config.LINKEDIN_WINDOW_DAYS * 86400}"
        for start in (self.DEEP_PAGES if self.deep else self.PAGES):
            params = {
                "keywords": keywords,
                "geoId": geo_id,
                "f_WT": "2",
                "f_TPR": window,
                "start": start,
            }
            params.update(extra or {})
            resp = http_get(self.SEARCH, params=params, headers=self.HEADERS,
                            timeout=20, retries=1)
            time.sleep(config.LINKEDIN_SEARCH_DELAY)
            if resp is None:
                return
            cards = _CARD_RE.findall(resp.text)
            if not cards:
                return
            for card in cards:
                job = self._parse_card(card)
                if not job:
                    continue
                with seen_lock:
                    if job.source_id in seen:
                        continue
                    if label:
                        job.employment_type_raw = label
                    seen[job.source_id] = job
            if len(cards) < 10:
                return

    def _run_pool(self, items: list, handle, workers: int, label: str,
                  note=None) -> None:
        """Run `handle` over `items` across a thread pool, logging progress.

        A handler that raises is logged and skipped, so one bad query or page
        cannot stop the sweep. `note` adds a phase-specific tally to the
        progress line.
        """
        queue: Queue = Queue()
        for item in items:
            queue.put(item)
        total = len(items)
        started = time.time()
        done = [0]
        lock = Lock()

        def worker() -> None:
            while True:
                try:
                    item = queue.get_nowait()
                except Empty:
                    return
                try:
                    handle(item)
                except Exception:  # noqa: BLE001 - one failure must not stop the rest
                    log.debug("linkedin %s failed: %.120s", label, item)
                with lock:
                    done[0] += 1
                    count = done[0]
                if count % self.PROGRESS_EVERY == 0 or count == total:
                    log.info("  linkedin: %s %d/%d%s · %s", label, count, total,
                             note() if note else "",
                             _progress(count, total, started))

        threads = [Thread(target=worker, daemon=True)
                   for _ in range(max(1, workers))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def _select_pending(self, seen: dict[str, RawJob]) -> list[RawJob]:
        """The jobs still needing a detail fetch, best first and within budget.

        Anything the cache can serve is filled in here and drops out; the rest
        competes for a fixed number of detail requests.
        """
        budget = (config.LINKEDIN_MAX_DETAILS_DEEP if self.deep
                  else (self.MAX_DETAILS if self.MAX_DETAILS is not None
                        else config.LINKEDIN_MAX_DETAILS))
        ordered = sorted(seen.values(), key=self._priority, reverse=True)

        pending: list[RawJob] = []
        for job in ordered:
            if self._restore_from_cache(job):
                continue
            if len(pending) < budget:
                pending.append(job)
        self.cache_hits = len(ordered) - len(pending)
        log.info("linkedin: %d descriptions from cache, %d to fetch",
                 self.cache_hits, len(pending))
        return pending

    def _fetch_detail(self, job: RawJob) -> None:
        """Fetch and cache one advert's full description.

        The delay is paid even when the fetch fails: a run erroring on every
        advert is exactly when not to hammer LinkedIn.
        """
        try:
            self._attach_description(job)
            self._store_in_cache(job)
        finally:
            time.sleep(self.DETAIL_DELAY)


    @staticmethod
    def _cache_key(job: RawJob) -> str:
        return f"linkedin:detail:{job.extra.get('linkedin_job_id') or job.source_id}"

    def _restore_from_cache(self, job: RawJob) -> bool:
        """Re-apply a previously fetched description. True when the job is served."""
        entry = cache.get(self._cache_key(job), config.LINKEDIN_CACHE_DAYS)
        if not entry or not entry.get("description"):
            return False
        job.description = entry["description"]
        job.extra["truncated_description"] = False
        if entry.get("employment_type"):
            job.employment_type_raw = entry["employment_type"]
        for tag in entry.get("tags") or []:
            if tag not in job.tags:
                job.tags.append(tag)
        if entry.get("company_linkedin"):
            job.extra["company_linkedin"] = entry["company_linkedin"]
        fresh = cache.get(self._cache_key(job), config.LINKEDIN_APPLICANTS_CACHE_DAYS)
        if fresh and fresh.get("applicants"):
            job.extra["applicants"] = fresh["applicants"]
        return True

    def _store_in_cache(self, job: RawJob) -> None:
        if not job.description:
            return
        cache.put(self._cache_key(job), {
            "description": job.description,
            "employment_type": job.employment_type_raw,
            "tags": job.tags,
            "applicants": job.extra.get("applicants", ""),
            "company_linkedin": job.extra.get("company_linkedin", ""),
        })

    @staticmethod
    def _priority(job: RawJob) -> tuple:
        """Core-titled and newest first, so the detail budget is well spent."""
        from .. import profile
        core = profile.active().core_terms
        title = normalize(job.title)
        primary = core[0] if core else ""
        secondary = core[1] if len(core) > 1 else ""
        return (
            1 if primary and primary in title else 0,
            1 if secondary and secondary in title else 0,
            job.posted_at.timestamp() if job.posted_at else 0,
        )

    def _parse_card(self, card: str) -> RawJob | None:
        title = _text(r'class="base-search-card__title"[^>]*>(.*?)</h3>', card)
        if not title:
            return None
        company = _text(r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>', card)
        location = _text(r'class="job-search-card__location"[^>]*>(.*?)</span>', card)

        date_match = re.search(r'datetime="([\d\-]+)"', card)
        link_match = re.search(r'href="(https://[^"?]+/jobs/view/[^"?]+)', card)
        urn = _URN_RE.search(card)

        job_id = urn.group(1) if urn else ""
        if not job_id and link_match:
            tail = re.search(r"-(\d{6,})$", link_match.group(1))
            job_id = tail.group(1) if tail else link_match.group(1)
        if not job_id:
            return None

        url = link_match.group(1) if link_match else f"https://www.linkedin.com/jobs/view/{job_id}"

        return RawJob(
            source=self.name,
            source_id=f"linkedin-{job_id}",
            title=title,
            company=company or "Undisclosed",
            url=url,
            apply_url=url,
            location_raw=location,
            posted_at=parse_datetime(date_match.group(1)) if date_match else None,
            extra={
                "is_remote": True,
                "linkedin_job_id": job_id,
                "truncated_description": True,
            },
        )

    def _attach_description(self, job: RawJob) -> None:
        job_id = job.extra.get("linkedin_job_id")
        if not job_id:
            return
        resp = http_get(self.DETAIL.format(job_id=job_id), headers=self.HEADERS,
                        timeout=20, retries=2)
        if resp is None:
            return
        body = re.search(
            r'class="(?:show-more-less-html__markup|description__text)[^"]*"[^>]*>(.*?)</div>',
            resp.text, re.S)
        text = strip_html(body.group(1) if body else resp.text)
        if len(text) > 200:
            job.description = text[:12000]
            job.extra["truncated_description"] = False

        criteria = re.findall(
            r'job-criteria-subheader[^>]*>(.*?)</h3>\s*<span[^>]*job-criteria-text[^>]*>(.*?)</span>',
            resp.text, re.S)
        for label, value in criteria:
            label_clean = strip_html(label).lower()
            value_clean = strip_html(value)
            if "employment type" in label_clean:
                job.employment_type_raw = value_clean
            elif "seniority" in label_clean:
                job.tags.append(value_clean)

        applicants = re.search(r'num-applicants__caption[^>]*>\s*([^<]+?)\s*<', resp.text)
        if applicants:
            job.extra["applicants"] = html.unescape(applicants.group(1)).strip()

        org = re.search(r'topcard__org-name-link[^>]*href="([^"?]+)', resp.text)
        if org:
            job.extra["company_linkedin"] = org.group(1)


class Careerjet(Source):
    """Careerjet v4 search API."""

    name = "careerjet"
    label = "Careerjet (aggregator)"
    credentials = ("CAREERJET_API_KEY",)
    URL = "https://search.api.careerjet.net/v4/query"

    USER_IP = "127.0.0.1"

    @staticmethod
    def queries_with_places() -> tuple[tuple[str, str], ...]:
        """(query, location) pairs: the search's terms, at home and worldwide."""
        source = Careerjet()
        wanted = source.wanted_countries()
        home = wanted[0].title() if wanted else ""
        pairs = [(query, home) for query in source.queries(4)]
        pairs.append((source.queries(1)[0], ""))
        return tuple(pairs)
    PAGE_SIZE = 100

    def __init__(self) -> None:
        self.api_key = config.CAREERJET_API_KEY
        self.enabled = bool(self.api_key)


    def _auth_header(self) -> str:
        """Basic auth: the API key as the user name, empty password."""
        token = base64.b64encode(f"{self.api_key}:".encode()).decode()
        return f"Basic {token}"

    def _query(self, keywords: str, location: str, page: int = 1) -> tuple[dict | None, str]:
        """Return (payload, error). Careerjet explains refusals in the body, so the text is
        kept rather than collapsed into a bare None — "unauthorized access from IP x"
        and "bad key" need very different fixes.
        """
        params = {
            "locale_code": "en_GB",
            "keywords": keywords,
            "sort": "date",
            "page": page,
            "page_size": self.PAGE_SIZE,
            "user_ip": self.USER_IP,
            "user_agent": config.USER_AGENT,
        }
        if location:
            params["location"] = location
        try:
            resp = requests.get(self.URL, params=params, headers={
                "Authorization": self._auth_header(),
                "Accept": "application/json",
                "User-Agent": config.USER_AGENT,
                "Referer": config.CAREERJET_REFERER,
            }, timeout=20)
        except Exception as exc:  # noqa: BLE001
            return None, f"could not reach Careerjet ({type(exc).__name__})"

        try:
            payload = resp.json()
        except ValueError:
            return None, f"HTTP {resp.status_code} with a non-JSON body"

        if resp.status_code != 200 or payload.get("type") == "ERROR":
            return None, payload.get("error") or payload.get("message") or f"HTTP {resp.status_code}"
        return payload, ""


    def probe(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "not set: CAREERJET_API_KEY"
        data, error = self._query(self.queries(1)[0], self.home_country(), 1)
        if error:
            if "unauthorized access from ip" in error.lower():
                return False, (f"{error} — add that IP to the allowed list in your "
                               f"Careerjet Publisher account (Websites -> API access)")
            return False, error
        kind = (data or {}).get("type")
        if kind == "LOCATIONS":
            return True, "key WORKS — but 'United Kingdom' resolved to multiple locations"
        if kind != "JOBS":
            return False, f"unexpected response type {kind!r} — {(data or {}).get('message', 'no message')}"
        return True, (f"key WORKS — Careerjet reports "
                      f"{(data or {}).get('hits', '?')} results")

    def fetch(self) -> list[RawJob]:
        if not self.enabled:
            return []
        jobs: list[RawJob] = []
        for keywords, location in self.queries_with_places():
            data, error = self._query(keywords, location)
            time.sleep(config.HTTP_DELAY)
            if error:
                log.debug("careerjet %r/%r: %s", keywords, location, error)
                continue
            if not data:
                continue
            if data.get("type") != "JOBS":
                log.debug("careerjet location mode for %r: %s",
                          location, data.get("message"))
                continue
            for item in data.get("jobs") or []:
                job = self._to_raw(item)
                if job:
                    jobs.append(job)
        return self.dedupe_by_id(jobs)


    @staticmethod
    def _to_raw(item: dict) -> RawJob | None:
        url = item.get("url") or ""
        title = item.get("title") or ""
        if not (url and title):
            return None

        salary_type = (item.get("salary_type") or "").upper()
        salary_min = item.get("salary_min") if salary_type == "Y" else None
        salary_max = item.get("salary_max") if salary_type == "Y" else None

        return RawJob(
            source="careerjet",
            source_id=f"careerjet-{url[-40:]}",
            title=title,
            company=item.get("company") or "Undisclosed",
            url=url,
            apply_url=url,
            description=strip_html(item.get("description")),
            location_raw=item.get("locations") or "",
            posted_at=parse_datetime(item.get("date")),
            salary_raw=item.get("salary") or "",
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=item.get("salary_currency_code") or "",
            extra={"truncated_description": True},
        )
