"""Finding the job platforms that serve a place, rather than shipping a list.

No curated list can cover every trade in every country. This asks the model
which platforms serve the region being searched, caches the answer, and treats
what comes back as untrusted input.

That last part matters more than the feature. A model naming a URL that this
code then fetches is a server-side request forgery hole: a hallucinated or
poisoned answer could point at localhost, a cloud metadata endpoint, or an
internal address that only this machine can reach. Every candidate is
validated before a single request is made, and the checks below are the
security boundary, not a formality.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlparse

from . import cache, config, llm

log = logging.getLogger("job_agent.discovery")

#: Only these schemes. No file://, no ftp://, no gopher://.
ALLOWED_SCHEMES = ("https",)

#: Only these ports. A non-standard port usually means an internal service.
ALLOWED_PORTS = (None, 443)

MAX_PLATFORMS = 12

DISCOVERY_VERSION = "v1"

SCHEMA = {
    "type": "object",
    "properties": {
        "platforms": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "label": {"type": "string"},
                    "country": {"type": "string"},
                    "url_template": {"type": "string"},
                    "kind": {"type": "string", "enum": ["jsonld", "rss"]},
                    "needs_key": {"type": "boolean"},
                },
                "required": ["name", "label", "country", "url_template", "kind",
                             "needs_key"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["platforms"],
    "additionalProperties": False,
}

SYSTEM = """You name the job platforms that serve a particular place and trade.

Return real, well-known job boards that genuinely operate in the country given
— Jobberman in Nigeria, Naukri in India, StepStone in Germany, Seek in
Australia, Bayt in the Gulf. Prefer general boards that carry trades and
manual work, not only technology roles.

`url_template` must be the board's public search URL with the literal token
{query} where the search words belong, for example
"https://www.example.com/jobs?q={query}". HTTPS only. Never invent a URL you
are not confident is that board's real search page — an unreachable board
costs a wasted request, but a wrong one wastes the whole search.

`kind` is "rss" when the URL returns a feed, "jsonld" when it returns an HTML
page carrying schema.org JobPosting markup.

Return an empty list rather than guessing when you do not know the market."""


@dataclass(frozen=True)
class Candidate:
    """One platform the model proposed. Not yet trusted."""

    name: str
    label: str
    country: str
    url_template: str
    kind: str
    needs_key: bool = False

    def search_url(self, query: str) -> str:
        return self.url_template.replace("{query}", quote_plus(query))


def _host_is_public(host: str) -> bool:
    """Does every address this host resolves to sit on the public internet?

    Rejecting by name is not enough: `evil.example.com` can resolve to
    127.0.0.1 or 169.254.169.254. The addresses are what matter.
    """
    try:
        results = socket.getaddrinfo(host, None)
    except (OSError, UnicodeError, ValueError):
        # Anything that stops us proving the host is public means we do not
        # fetch it. gaierror is an OSError; so are the rarer resolver faults.
        return False
    if not results:
        return False
    for result in results:
        raw = result[4][0]
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if not address.is_global or address.is_multicast:
            return False
    return True


def rejection(candidate: Candidate, *, resolve: bool = True) -> str:
    """Why this candidate must not be fetched, or "" when it is safe."""
    template = candidate.url_template or ""
    if "{query}" not in template:
        return "no {query} placeholder — the search words could not be inserted"
    return url_rejection(template.replace("{query}", "x"), resolve=resolve)


def url_rejection(url: str, *, resolve: bool = True) -> str:
    """Why this address must not be fetched, or "" when it is safe.

    Applied to the address actually about to be requested, so a redirect is
    judged by the same rules as the address the model first proposed.
    """
    parsed = urlparse(url or "")
    if parsed.scheme not in ALLOWED_SCHEMES:
        return f"scheme {parsed.scheme or 'missing'!r} is not allowed"
    if parsed.username or parsed.password or "@" in (parsed.netloc or ""):
        return "credentials embedded in the URL"
    try:
        port = parsed.port
    except ValueError:
        return "malformed port"
    if port not in ALLOWED_PORTS:
        return f"port {port} is not allowed"
    host = parsed.hostname or ""
    if not host or "." not in host:
        return "no public hostname"
    if resolve and not _host_is_public(host):
        return f"{host} does not resolve to a public address"
    return ""


#: How many redirects a discovered board may send us through.
MAX_REDIRECTS = 4


def safe_get(url: str, **kwargs):
    """Fetch a discovered address, vetting every redirect it sends us through.

    Validating the address the model proposed says nothing about where that
    address redirects to. A public host answering 302 to 169.254.169.254 would
    otherwise be followed and its body read, which is the whole hole this
    module exists to close.
    """
    from .utils import http_get

    for _ in range(MAX_REDIRECTS + 1):
        if url_rejection(url):
            log.debug("discovery refused %s", url)
            return None
        resp = http_get(url, allow_redirects=False, **kwargs)
        if resp is None:
            return None
        if not resp.is_redirect:
            return resp
        target = resp.headers.get("location") or ""
        if not target:
            return None
        url = urljoin(url, target)
    return None


def safe_candidates(candidates: list[Candidate], *, resolve: bool = True) -> list[Candidate]:
    """Only those that pass every check, capped."""
    kept: list[Candidate] = []
    for candidate in candidates:
        if rejection(candidate, resolve=resolve):
            continue
        kept.append(candidate)
        if len(kept) >= MAX_PLATFORMS:
            break
    return kept


def _parse(payload: dict) -> list[Candidate]:
    out: list[Candidate] = []
    for row in (payload.get("platforms") or [])[: MAX_PLATFORMS * 2]:
        if not isinstance(row, dict):
            continue
        try:
            out.append(Candidate(
                name=str(row.get("name") or "").strip()[:40],
                label=str(row.get("label") or "").strip()[:60],
                country=str(row.get("country") or "").strip()[:40],
                url_template=str(row.get("url_template") or "").strip()[:400],
                kind=str(row.get("kind") or "jsonld").strip(),
                needs_key=bool(row.get("needs_key")),
            ))
        except (TypeError, ValueError):
            continue
    return [c for c in out if c.name and c.url_template]


def discover(country: str, trade: str, *, resolve: bool = True) -> list[Candidate]:
    """Platforms serving this country and trade, cached and validated."""
    country = (country or "").strip()
    if not country:
        return []

    key = f"platforms:{DISCOVERY_VERSION}:{country.lower()}:{trade.lower()[:40]}"
    payload = cache.get(key, config.LLM_CACHE_DAYS)
    if payload is None:
        if not llm.available()[0]:
            return []
        budget = llm.Budget(config.LLM_MAX_SPEND_USD)
        payload = llm.ask(
            SYSTEM, SCHEMA,
            f"Country: {country}\nTrade or role: {trade or 'any'}",
            config.LLM_EFFORT_FIT, budget)
        if payload is None:
            return []
        cache.put(key, payload)

    return safe_candidates(_parse(payload), resolve=resolve)
