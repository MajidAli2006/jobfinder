"""Remote-first job board connectors (public JSON APIs and RSS feeds)."""

from __future__ import annotations

from html.entities import html5 as _html5
import html as html_module
from urllib.parse import quote_plus, urljoin, urlparse

import json
import re
import time
import xml.etree.ElementTree as ET

from .. import config
from ..models import RawJob
from ..utils import (
    get_json, http_get, normalize_amount, parse_datetime, strip_html,
)
from .base import Source

#: Named HTML entities, without the trailing semicolon form.
html_entities = {name.rstrip(';'): char for name, char in _html5.items()}


def feed_text(item, tag: str) -> str:
    """One element's text from a feed entry, or "" when it is absent."""
    node = item.find(tag)
    return (node.text or "").strip() if node is not None and node.text else ""


def tag_list(value) -> list[str]:
    """A feed's list field, read as tags.

    Boards send this three ways: a real list, a comma-separated string, or a
    bare string. Iterating a string yields one tag per letter, so a string is
    read as the tag or tags it actually is. A null inside a list is dropped
    rather than becoming the tag "None".
    """
    if not value:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item not in (None, "")]
    return [str(value)]


class RemoteOK(Source):
    name = "remoteok"
    label = "RemoteOK"
    remote_by_default = True
    URL = "https://remoteok.com/api"

    TAGS: tuple[str, ...] = ()
    HEADERS = {"Accept": "application/json"}

    def fetch(self) -> list[RawJob]:
        parsed = (self._to_raw(entry)
                  for payload in self._payloads() for entry in payload)
        return self.dedupe_by_id([job for job in parsed if job])

    def sweeps(self) -> tuple[str, ...]:
        """Tags to sweep after the firehose. RemoteOK tags are single words."""
        if self.TAGS:
            return self.TAGS
        single_word = tuple(q for q in self.queries(6) if " " not in q)[:5]
        return single_word or self.queries(3)

    def _payloads(self) -> list[list]:
        """The firehose listing plus one listing per tag sweep."""
        payloads: list[list] = []
        firehose = get_json(self.URL, headers=self.HEADERS, timeout=12, retries=1)
        if isinstance(firehose, list):
            payloads.append(firehose)
        for tag in self.sweeps():
            tagged = get_json(self.URL, params={"tags": tag}, headers=self.HEADERS,
                              timeout=12, retries=1)
            time.sleep(config.HTTP_DELAY)
            if isinstance(tagged, list):
                payloads.append(tagged)
        return payloads

    @classmethod
    def _to_raw(cls, item) -> RawJob | None:
        """One listing entry, or None for the legal notice the feed leads with."""
        if not isinstance(item, dict) or "position" not in item:
            return None
        url = item.get("url") or ""
        return RawJob(
            source=cls.name,
            source_id=f"remoteok-{item.get('id') or item.get('slug')}",
            title=item.get("position") or "",
            company=item.get("company") or "",
            url=url,
            apply_url=item.get("apply_url") or url,
            description=strip_html(item.get("description")),
            location_raw=item.get("location") or "Remote",
            posted_at=parse_datetime(item.get("epoch") or item.get("date")),
            salary_min=normalize_amount(item.get("salary_min")),
            salary_max=normalize_amount(item.get("salary_max")),
            salary_currency="USD",
            company_logo=item.get("company_logo") or item.get("logo") or "",
            tags=tag_list(item.get("tags")),
            extra={"is_remote": True},
        )


class Remotive(Source):
    name = "remotive"
    label = "Remotive"
    remote_by_default = True
    URL = "https://remotive.com/api/remote-jobs"

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for query in self.queries(4):
            data = get_json(self.URL, params={"search": query, "limit": 100})
            time.sleep(config.HTTP_DELAY)
            if not isinstance(data, dict):
                continue
            for item in data.get("jobs") or []:
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"remotive-{item.get('id')}",
                    title=item.get("title") or "",
                    company=item.get("company_name") or "",
                    url=item.get("url") or "",
                    apply_url=item.get("url") or "",
                    description=strip_html(item.get("description")),
                    location_raw=item.get("candidate_required_location") or "Remote",
                    posted_at=parse_datetime(item.get("publication_date")),
                    employment_type_raw=item.get("job_type") or "",
                    salary_raw=item.get("salary") or "",
                    company_logo=item.get("company_logo") or "",
                    tags=tag_list(item.get("tags")),
                    extra={"is_remote": True},
                ))
        return self.dedupe_by_id(jobs)


class WeWorkRemotely(Source):
    name = "weworkremotely"
    label = "We Work Remotely"
    remote_by_default = True
    FEEDS = (
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-full-stack-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-back-end-programming-jobs.rss",
        "https://weworkremotely.com/remote-jobs.rss",
    )

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for feed in self.FEEDS:
            resp = http_get(feed, headers={"Accept": "application/rss+xml, application/xml"})
            time.sleep(config.HTTP_DELAY)
            if resp is None:
                continue
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                continue
            for item in root.iter("item"):
                raw_title = feed_text(item, "title")
                company, _, position = raw_title.partition(":")
                if not position:
                    company, position = "", raw_title
                region = feed_text(item, "region") or feed_text(item, "{https://weworkremotely.com/}region")
                link = feed_text(item, "link")
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"wwr-{feed_text(item, 'guid') or link}",
                    title=position.strip() or raw_title,
                    company=company.strip(),
                    url=link,
                    apply_url=link,
                    description=strip_html(feed_text(item, "description")),
                    location_raw=region or "Remote",
                    posted_at=parse_datetime(feed_text(item, "pubDate")),
                    tags=[t for t in (feed_text(item, "category"),) if t],
                    extra={"is_remote": True},
                ))
        return self.dedupe_by_id(jobs)


class Arbeitnow(Source):
    name = "arbeitnow"
    label = "Arbeitnow"
    URL = "https://www.arbeitnow.com/api/job-board-api"

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for page in (1, 2, 3):
            data = get_json(self.URL, params={"page": page})
            time.sleep(config.HTTP_DELAY)
            if not isinstance(data, dict):
                break
            entries = data.get("data") or []
            if not entries:
                break
            for item in entries:
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"arbeitnow-{item.get('slug')}",
                    title=item.get("title") or "",
                    company=item.get("company_name") or "",
                    url=item.get("url") or "",
                    apply_url=item.get("url") or "",
                    description=strip_html(item.get("description")),
                    location_raw=item.get("location") or "",
                    posted_at=parse_datetime(item.get("created_at")),
                    employment_type_raw=", ".join(item.get("job_types") or []),
                    tags=tag_list(item.get("tags")),
                    extra={"is_remote": bool(item.get("remote"))},
                ))
        return self.dedupe_by_id(jobs)


class Jobicy(Source):
    name = "jobicy"
    label = "Jobicy"
    remote_by_default = True
    URL = "https://jobicy.com/api/v2/remote-jobs"

    def geo(self) -> str:
        """Jobicy's slug for the search's country, or "anywhere"."""
        wanted = self.wanted_countries()
        return wanted[0].replace(" ", "-") if wanted else "anywhere"

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        requests_to_make = tuple(
            {"count": 50, "tag": tag} for tag in self.queries(3)
        ) + (
            {"count": 50, "geo": self.geo()},
            {"count": 50, "geo": "anywhere"},
        )
        for params in requests_to_make:
            data = get_json(self.URL, params=params)
            time.sleep(config.HTTP_DELAY)
            if not isinstance(data, dict):
                continue
            for item in data.get("jobs") or []:
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"jobicy-{item.get('id')}",
                    title=item.get("jobTitle") or "",
                    company=item.get("companyName") or "",
                    url=item.get("url") or "",
                    apply_url=item.get("url") or "",
                    description=strip_html(item.get("jobDescription") or item.get("jobExcerpt")),
                    location_raw=item.get("jobGeo") or "Anywhere",
                    posted_at=parse_datetime(item.get("pubDate")),
                    employment_type_raw=", ".join(item.get("jobType") or []),
                    salary_min=normalize_amount(item.get("annualSalaryMin")),
                    salary_max=normalize_amount(item.get("annualSalaryMax")),
                    salary_currency=(item.get("salaryCurrency") or "").upper(),
                    company_logo=item.get("companyLogo") or "",
                    tags=tag_list(item.get("jobIndustry")) + tag_list(item.get("jobLevel")),
                    extra={"is_remote": True},
                ))
        return self.dedupe_by_id(jobs)


class WorkingNomads(Source):
    name = "workingnomads"
    label = "Working Nomads"
    remote_by_default = True
    URL = "https://www.workingnomads.com/api/exposed_jobs/"

    def fetch(self) -> list[RawJob]:
        data = get_json(self.URL)
        if not isinstance(data, list):
            return []
        jobs: list[RawJob] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            jobs.append(RawJob(
                source=self.name,
                source_id=f"workingnomads-{item.get('id') or item.get('slug')}",
                title=item.get("title") or "",
                company=item.get("company_name") or "",
                url=item.get("url") or "",
                apply_url=item.get("url") or "",
                description=strip_html(item.get("description")),
                location_raw=item.get("location") or "Remote",
                posted_at=parse_datetime(item.get("pub_date")),
                tags=tag_list(item.get("tags")),
                extra={"is_remote": True},
            ))
        return jobs


class Himalayas(Source):
    name = "himalayas"
    label = "Himalayas"
    remote_by_default = True
    URL = "https://himalayas.app/jobs/api"

    PAGE_SIZE = 20
    PAGES = 12

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for page in range(self.PAGES):
            entries = self._page(page)
            if not entries:
                break
            jobs.extend(self._to_raw(item) for item in entries)
        return self.dedupe_by_id(jobs)

    def _page(self, page: int) -> list:
        """One page of listings, or [] once the feed is exhausted."""
        data = get_json(self.URL, params={"limit": self.PAGE_SIZE,
                                          "offset": page * self.PAGE_SIZE})
        time.sleep(config.HTTP_DELAY)
        return (data.get("jobs") or []) if isinstance(data, dict) else []

    @staticmethod
    def _location(item: dict) -> str:
        """The restriction list as one string; unrestricted reads as Worldwide."""
        restrictions = item.get("locationRestrictions") or []
        if isinstance(restrictions, str):
            restrictions = [restrictions]
        return ", ".join(str(r) for r in restrictions) or "Worldwide"

    @staticmethod
    def _company(item: dict) -> str:
        """The employer, falling back to the slug when the feed sends a placeholder."""
        company = item.get("companyName") or ""
        if company.lower() in ("", "name", "null", "none"):
            return (item.get("companySlug") or "").replace("-", " ").title()
        return company

    @classmethod
    def _to_raw(cls, item: dict) -> RawJob:
        link = item.get("applicationLink") or ""
        return RawJob(
            source=cls.name,
            source_id=f"himalayas-{item.get('guid') or item.get('title')}",
            title=item.get("title") or "",
            company=cls._company(item),
            url=link or item.get("guid") or "",
            apply_url=link,
            description=strip_html(item.get("description") or item.get("excerpt")),
            location_raw=cls._location(item),
            posted_at=parse_datetime(item.get("pubDate")),
            employment_type_raw=str(item.get("employmentType") or ""),
            salary_min=normalize_amount(item.get("minSalary")),
            salary_max=normalize_amount(item.get("maxSalary")),
            salary_currency=(item.get("currency")
                             or item.get("salaryCurrency") or "").upper(),
            company_logo=item.get("companyLogo") or "",
            tags=tag_list(item.get("categories")) + tag_list(item.get("seniority")),
            extra={"is_remote": True},
        )


class RssBoard(Source):
    """A job board that publishes WordPress-style job feeds.

    Most niche boards run the same plugin, so one reader serves all of them:
    a feed URL carrying the search term, `<item>` entries, and the employer in
    `dc:creator` with the location after a line break.
    """

    FEED = ""
    QUERIES = 2
    remote_by_default = False

    def feeds(self) -> tuple[str, ...]:
        return tuple(self.FEED.format(query=quote_plus(query))
                     for query in self.queries(self.QUERIES))

    @staticmethod
    def _creator(raw_creator: str) -> tuple[str, str]:
        """(company, location) out of the `dc:creator` field."""
        if not raw_creator:
            return "", ""
        parts = re.split(r"<br\s*/?>", raw_creator, maxsplit=1)
        company = strip_html(parts[0]).strip()
        location = strip_html(parts[1]).replace("\u26b2", "").strip() if len(parts) > 1 else ""
        return company, location

    @staticmethod
    def parse(payload: bytes):
        """Parse a feed that may carry HTML entities XML does not define.

        Several boards emit `&nbsp;` and friends, which is not valid XML and
        aborts a strict parse. Resolving the named entities first keeps a
        whole board from silently returning nothing.
        """
        try:
            return ET.fromstring(payload)
        except ET.ParseError:
            pass
        text = payload.decode("utf-8", "replace")
        text = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)([A-Za-z][A-Za-z0-9]*);",
                      lambda m: html_entities.get(m.group(1), ""), text)
        try:
            return ET.fromstring(text)
        except ET.ParseError:
            return None

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for feed in self.feeds():
            resp = http_get(feed)
            time.sleep(config.HTTP_DELAY)
            if resp is None:
                continue
            root = self.parse(resp.content)
            if root is None:
                continue
            for item in root.iter("item"):
                link = feed_text(item, "link")
                if not link:
                    continue
                company, location = self._creator(
                    feed_text(item, "{http://purl.org/dc/elements/1.1/}creator"))
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"{self.name}-{feed_text(item, 'guid') or link}",
                    title=feed_text(item, "title"),
                    company=company or "Undisclosed",
                    url=link,
                    apply_url=link,
                    description=strip_html(
                        feed_text(item, "{http://purl.org/rss/1.0/modules/content/}encoded")
                        or feed_text(item, "description")),
                    location_raw=location or ("Remote" if self.remote_by_default else ""),
                    posted_at=parse_datetime(feed_text(item, "pubDate")),
                ))
        return self.dedupe_by_id(jobs)




class NoDesk(RssBoard):
    """Publishes one firehose rather than a searchable feed, so the query is
    not sent and relevance filtering does the narrowing."""

    name = "nodesk"
    label = "NoDesk"
    remote_by_default = True
    FEED = "https://nodesk.co/remote-jobs/index.xml"
    QUERIES = 1

    def feeds(self) -> tuple[str, ...]:
        return (self.FEED,)

    def fetch(self) -> list[RawJob]:
        # Titles read "Role at Employer" and there is no separate company field.
        jobs = super().fetch()
        for job in jobs:
            if " at " in job.title:
                role, _, employer = job.title.rpartition(" at ")
                if role.strip() and employer.strip():
                    job.title, job.company = role.strip(), employer.strip()
        return jobs




class EuRemoteJobs(RssBoard):
    """European remote work, so it is only asked about European searches."""

    name = "euremotejobs"
    label = "EU Remote Jobs"
    remote_by_default = True
    FEED = "https://euremotejobs.com/?feed=job_feed&search_keywords={query}"
    markets = ("germany", "france", "spain", "italy", "netherlands", "poland",
               "portugal", "ireland", "sweden", "denmark", "finland", "norway",
               "belgium", "austria", "czechia", "romania", "greece", "hungary")




class CryptocurrencyJobs(RssBoard):
    name = "cryptocurrencyjobs"
    label = "Cryptocurrency Jobs"
    remote_by_default = True
    FEED = "https://cryptocurrencyjobs.co/index.xml"
    QUERIES = 1

    def feeds(self) -> tuple[str, ...]:
        return (self.FEED,)


class HackerNewsHiring(Source):
    """The monthly "Ask HN: Who is hiring?" thread, read through Algolia.

    Top-level comments in that thread are individual job posts, and are often
    the first place a small employer advertises. Replies are discussion, so
    only the thread's own comments are read and only where the search terms
    appear.
    """

    name = "hackernews"
    label = "Hacker News — who is hiring"
    URL = "https://hn.algolia.com/api/v1/search"
    THREADS = 3

    def hiring_threads(self) -> tuple[str, ...]:
        """Story ids of the most recent hiring threads."""
        data = get_json(self.URL, params={
            "query": "Ask HN: Who is hiring?",
            "tags": "story,author_whoishiring",
            "hitsPerPage": self.THREADS,
        })
        time.sleep(config.HTTP_DELAY)
        if not isinstance(data, dict):
            return ()
        return tuple(str(hit.get("objectID")) for hit in (data.get("hits") or [])
                     if hit.get("objectID"))

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for story_id in self.hiring_threads():
            for query in self.queries(3):
                data = get_json(self.URL, params={
                    "query": query,
                    "tags": f"comment,story_{story_id}",
                    "hitsPerPage": 50,
                })
                time.sleep(config.HTTP_DELAY)
                if not isinstance(data, dict):
                    continue
                for hit in data.get("hits") or []:
                    if str(hit.get("parent_id")) != story_id:
                        continue        # a reply, not a job post
                    body = strip_html(hit.get("comment_text") or "")
                    if len(body) < 120:
                        continue
                    object_id = hit.get("objectID")
                    url = f"https://news.ycombinator.com/item?id={object_id}"
                    headline = body.split("\n")[0].strip()
                    jobs.append(RawJob(
                        source=self.name,
                        source_id=f"hn-{object_id}",
                        title=headline[:120],
                        company=headline.split("|")[0].strip()[:60] or "Undisclosed",
                        url=url,
                        apply_url=url,
                        description=body,
                        posted_at=parse_datetime(hit.get("created_at")),
                    ))
        return self.dedupe_by_id(jobs)


class RemoteRocks(Source):
    """Remote-jobs RSS aggregator that carries a lot of European remote work."""

    name = "jobspresso"
    label = "Jobspresso"
    remote_by_default = True
    FEED = "https://jobspresso.co/?feed=job_feed&search_keywords={query}"

    @property
    def FEEDS(self) -> tuple[str, ...]:
        """One feed per search term, so the board is asked what was searched for."""
        return tuple(self.FEED.format(query=quote_plus(query))
                     for query in self.queries(2))

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for feed in self.FEEDS:
            resp = http_get(feed)
            time.sleep(config.HTTP_DELAY)
            if resp is None:
                continue
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                continue
            for item in root.iter("item"):
                creator = feed_text(item, "{http://purl.org/dc/elements/1.1/}creator")
                company, location = creator, ""
                if creator:
                    parts = re.split(r"<br\s*/?>", creator, maxsplit=1)
                    company = strip_html(parts[0]).strip()
                    if len(parts) > 1:
                        location = strip_html(parts[1]).replace("⚲", "").strip()

                link = feed_text(item, "link")
                jobs.append(RawJob(
                    source=self.name,
                    source_id=f"jobspresso-{feed_text(item, 'guid') or link}",
                    title=feed_text(item, "title"),
                    company=company,
                    url=link,
                    apply_url=link,
                    description=strip_html(
                        feed_text(item, "{http://purl.org/rss/1.0/modules/content/}encoded")
                        or feed_text(item, "description")),
                    location_raw=location or "Remote",
                    posted_at=parse_datetime(feed_text(item, "pubDate")),
                    extra={"is_remote": True},
                ))
        return self.dedupe_by_id(jobs)


class JsonLdBoard(Source):
    """A board read through the structured data it publishes.

    schema.org JobPosting markup is the one thing most job boards have in
    common, whatever country or trade they serve, so one reader handles all of
    them. Subclasses only decide which boards to ask.

    Search pages that carry no posting markup are followed through to the
    advert pages, which almost always do.
    """

    name = "jsonld_board"
    label = "Structured-data board"

    def candidates(self):
        raise NotImplementedError

    def fetch(self) -> list[RawJob]:
        jobs: list[RawJob] = []
        for candidate in self.candidates():
            for query in self.queries(2):
                url = candidate.search_url(query)
                resp = self._get(url)
                time.sleep(config.HTTP_DELAY)
                if resp is None:
                    continue
                if candidate.kind == "rss":
                    jobs.extend(self._from_feed(candidate, resp.content))
                    continue
                found = self._from_jsonld(candidate, resp.text, url)
                if not found:
                    # Most boards put JobPosting on the advert page, not the
                    # results page. Follow the results through to it rather
                    # than writing a parser per board.
                    found = self._follow_details(candidate, resp.text, url)
                jobs.extend(found)
        return self.dedupe_by_id(jobs)

    MAX_DETAIL_PAGES = 12

    #: Path fragments that mark a link as an individual advert rather than
    #: navigation. Deliberately broad: boards name these differently.
    DETAIL_HINTS = ("/job/", "/jobs/", "/listing", "/vacanc", "/career",
                    "/position", "/opening", "/stelle", "/emploi", "/empleo")

    @staticmethod
    def _get(url: str):
        """Discovered addresses are fetched with every redirect re-checked."""
        from ..discovery import safe_get
        return safe_get(url)

    def _detail_links(self, page_html: str, page_url: str) -> list[str]:
        """Same-host advert links on a results page, in document order."""
        host = urlparse(page_url).hostname or ""
        seen: dict[str, None] = {}
        for href in re.findall(r'href="([^"#]+)"', page_html):
            link = urljoin(page_url, html_module.unescape(href))
            parsed = urlparse(link)
            if parsed.scheme != "https" or parsed.hostname != host:
                continue
            path = (parsed.path or "").lower()
            if not any(hint in path for hint in self.DETAIL_HINTS):
                continue
            if path.rstrip("/").count("/") < 2:
                continue        # a section index, not one advert
            seen.setdefault(link.split("?")[0], None)
        return list(seen)[: self.MAX_DETAIL_PAGES]

    def _follow_details(self, candidate, page_html: str, page_url: str) -> list[RawJob]:
        out: list[RawJob] = []
        for link in self._detail_links(page_html, page_url):
            resp = self._get(link)
            time.sleep(config.HTTP_DELAY)
            if resp is None:
                continue
            out.extend(self._from_jsonld(candidate, resp.text, link))
        return out

    def _from_feed(self, candidate, payload: bytes) -> list[RawJob]:
        root = RssBoard.parse(payload)
        if root is None:
            return []
        out = []
        for item in root.iter("item"):
            link = feed_text(item, "link")
            if not link:
                continue
            out.append(RawJob(
                source=self.name,
                source_id=f"{candidate.name}-{feed_text(item, 'guid') or link}",
                title=feed_text(item, "title"),
                company=feed_text(item, "{http://purl.org/dc/elements/1.1/}creator") or candidate.label,
                url=link, apply_url=link,
                description=strip_html(feed_text(item, "description")),
                location_raw=candidate.country,
                posted_at=parse_datetime(feed_text(item, "pubDate")),
                extra={"discovered_platform": candidate.name},
            ))
        return out

    def _from_jsonld(self, candidate, html: str, page_url: str) -> list[RawJob]:
        """Read schema.org JobPosting blocks, which most boards publish."""
        out = []
        for block in re.findall(
                r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
                html, re.S | re.I):
            try:
                data = json.loads(block.strip())
            except (ValueError, TypeError):
                continue
            postings = self._job_postings(data)
            if not postings:
                out.extend(self._from_itemlist(candidate, data))
                continue
            index = self._by_id(data)
            for node in postings:
                title = str(node.get("title") or "").strip()
                if not title:
                    continue
                org = self._resolve(node.get("hiringOrganization"), index)
                employer = self._clean_company(
                    str((org or {}).get("name") or ""), candidate.label)
                url = str(node.get("url") or page_url)
                out.append(RawJob(
                    source=self.name,
                    source_id=f"{candidate.name}-{node.get('identifier') or url}-{title[:40]}",
                    title=title,
                    company=employer,
                    url=url, apply_url=url,
                    description=strip_html(str(node.get("description") or "")),
                    location_raw=self._place(node) or candidate.country,
                    posted_at=parse_datetime(node.get("datePosted")),
                    employment_type_raw=str(node.get("employmentType") or ""),
                    extra={"discovered_platform": candidate.name,
                           "truncated_description": True},
                ))
        return out

    def _from_itemlist(self, candidate, data) -> list[RawJob]:
        """Search pages often list links and leave JobPosting on the detail page.

        The links are worth keeping when they are there: the advert-fetch stage
        fills in the body later. Boards whose list carries only a name and no
        URL yield nothing here, which is the honest outcome — the posting is
        not in the structured data at all.
        """
        out = []
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if str(node.get("@type") or "") == "ListItem":
                item = node.get("item")
                if isinstance(item, dict):
                    url = str(item.get("url") or "").strip()
                    title = str(item.get("name") or "").strip()
                    if url.startswith("https://") and title:
                        out.append(RawJob(
                            source=self.name,
                            source_id=f"{candidate.name}-{url}",
                            title=title,
                            company=candidate.label,
                            url=url, apply_url=url,
                            location_raw=candidate.country,
                            extra={"discovered_platform": candidate.name,
                                   "truncated_description": True},
                        ))
            stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        return out

    @staticmethod
    def _job_postings(data) -> list[dict]:
        """Every JobPosting in a JSON-LD document, however it is nested."""
        found: list[dict] = []
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if str(node.get("@type") or "") == "JobPosting":
                    found.append(node)
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        return found

    #: Boards that paywall the employer name still publish a value for it.
    #: Showing it verbatim puts "[Unlock with Premium]" in the Company column.
    PLACEHOLDER_COMPANY = re.compile(
        r"^\s*[\[\(]|unlock|premium|sign ?in|log ?in|subscribe|hidden|confidential|"
        r"^n/?a$|^undisclosed$", re.IGNORECASE)

    @classmethod
    def _clean_company(cls, name: str, fallback: str) -> str:
        """The employer, or an honest "Undisclosed" when the board hides it."""
        name = (name or "").strip()
        if not name or cls.PLACEHOLDER_COMPANY.search(name):
            return "Undisclosed"
        return name

    @staticmethod
    def _by_id(data) -> dict[str, dict]:
        """Every node in the document that declares an @id.

        schema.org lets a page define an organisation once and refer to it by
        id from the posting. Without this the employer reads as an unresolved
        reference and the row shows the board's name instead of theirs.
        """
        index: dict[str, dict] = {}
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                identifier = node.get("@id")
                if isinstance(identifier, str) and len(node) > 1:
                    index.setdefault(identifier, node)
                stack.extend(v for v in node.values() if isinstance(v, (dict, list)))
        return index

    @staticmethod
    def _resolve(value, index: dict[str, dict]) -> dict:
        """A node, following an @id reference when that is all there is."""
        if isinstance(value, list):
            value = value[0] if value else {}
        if not isinstance(value, dict):
            return {}
        if set(value) == {"@id"}:
            return index.get(str(value["@id"]), {})
        return value

    @staticmethod
    def _place(node: dict) -> str:
        location = node.get("jobLocation") or {}
        if isinstance(location, list):
            location = location[0] if location else {}
        address = (location or {}).get("address") or {}
        if not isinstance(address, dict):
            return ""
        parts = (address.get("addressLocality"), address.get("addressRegion"),
                 address.get("addressCountry"))
        return ", ".join(str(p) for p in parts if isinstance(p, str) and p)


class DiscoveredPlatforms(JsonLdBoard):
    """Regional boards the discovery stage found for the country being searched.

    Nothing here is curated. Every URL has passed discovery's validation
    before a request is made, and postings are labelled as discovered so a bad
    parse is visible rather than mixed in silently.
    """

    name = "discovered_platforms"
    label = "Regional boards (discovered)"

    def candidates(self):
        from .. import discovery, profile
        if not getattr(config, "DISCOVER_PLATFORMS", True):
            return []
        active = profile.active()
        wanted = self.wanted_countries()
        country = wanted[0].title() if wanted else active.home_country
        return discovery.discover(country, active.label or active.query)


class StructuredBoards(JsonLdBoard):
    """Boards that publish JobPosting markup, read without a bespoke parser.

    These are here rather than as hand-written connectors because there is
    nothing board-specific to write: the markup is the interface. Each entry
    was checked to actually return postings — a board that publishes no
    structured data belongs nowhere near this list.
    """

    name = "structured_boards"
    label = "Structured-data job boards"

    BOARDS = (
        ("dailyremote", "DailyRemote", "",
         "https://dailyremote.com/remote-jobs?search={query}"),
    )

    def candidates(self):
        from ..discovery import Candidate
        return [Candidate(name=name, label=label, country=country,
                          url_template=template, kind="jsonld")
                for name, label, country, template in self.BOARDS]
