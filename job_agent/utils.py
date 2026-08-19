"""Shared helpers: HTTP, HTML/text cleaning, date and money parsing."""

from __future__ import annotations

import html
import logging
import re
import time
import unicodedata
from datetime import date, datetime, timedelta, UTC
from typing import Any
from collections.abc import Iterable

import requests

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config

log = logging.getLogger("job_agent")

_SESSION: requests.Session | None = None

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NL_RE = re.compile(r"\n{3,}")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b")
_PHONE_RE = re.compile(
    r"(?<![\w])(?:\+\d{1,3}[\s\-.]?)?(?:\(\d{2,5}\)[\s\-.]?|\d{2,5}[\s\-.])\d{3,4}[\s\-.]?\d{3,4}(?![\w])"
)
_URL_RE = re.compile(r"https?://[^\s<>\"')]+")


def session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        s = requests.Session()
        s.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "application/json, text/plain, application/xml, text/xml, */*",
            "Accept-Language": "en-GB,en;q=0.9",
        })
        _SESSION = s
    return _SESSION


def secret_values() -> tuple[str, ...]:
    """Every configured credential, longest first so overlaps scrub cleanly."""
    from . import config
    values = [
        getattr(config, name, "") or ""
        for name in ("ANTHROPIC_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
                     "REED_API_KEY", "JOOBLE_API_KEY", "CAREERJET_API_KEY",
                     "INDEED_PUBLISHER_ID", "ZIPRECRUITER_API_KEY")
    ]
    return tuple(sorted((v for v in values if len(v) >= 8), key=len, reverse=True))


def scrub(text: object) -> str:
    """Text with any credential replaced, safe to log or show.

    Some boards take their key in the URL path rather than a header, and a
    failed request raises an exception carrying that whole URL. Logging it
    verbatim writes the key into the terminal and into any log file.
    """
    out = str(text)
    for secret in secret_values():
        out = out.replace(secret, "***")
    return out


def http_get(url: str, *, params: dict | None = None, headers: dict | None = None,
             timeout: int | None = None, retries: int | None = None) -> requests.Response | None:
    """GET with retries. Returns None instead of raising so one dead source cannot take
    down the run.
    """
    last_error: Exception | None = None
    attempts = config.HTTP_RETRIES if retries is None else retries
    for attempt in range(attempts + 1):
        try:
            resp = session().get(
                url,
                params=params,
                headers=headers,
                timeout=timeout or config.HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 502, 503, 504) and attempt < attempts:
                time.sleep(1.5 * (attempt + 1))
                continue
            log.debug("GET %s -> HTTP %s", scrub(url), resp.status_code)
            return None
        except Exception as exc:  # noqa: BLE001 - network layer must never crash a run
            last_error = exc
            if attempt < attempts:
                time.sleep(1.0 * (attempt + 1))
    if last_error:
        log.debug("GET %s failed: %s", scrub(url), scrub(last_error))
    return None


def get_json(url: str, **kwargs: Any) -> Any:
    resp = http_get(url, **kwargs)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def strip_html(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n- ", text)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n\n", text)
    return text.strip()


def normalize(text: str | None) -> str:
    """Lower-cased, accent-folded, whitespace-collapsed text for matching."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[‐-―]", "-", text)
    text = re.sub(r"[^\w\s\-/+.,:;()£$€@'&]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(text: str | None) -> str:
    """Aggressive normalisation used for de-duplication keys."""
    if not text:
        return ""
    text = re.split(r"\s*[|·—–]\s*", text)[0].strip() or text
    text = normalize(text)
    text = re.sub(r"\b(ltd|limited|inc|inc\.|llc|gmbh|bv|b\.v\.|plc|corp|corporation|"
                  r"co|company|group|technologies|technology|tech|labs|lab|studio|"
                  r"studios|software|solutions|digital|the)\b", " ", text)
    return re.sub(r"[^a-z0-9]", "", text)


def title_slug(text: str | None) -> str:
    if not text:
        return ""
    text = normalize(text)
    text = re.sub(r"\b(?:ref|job\s*id|req(?:uisition)?(?:\s*id)?)\s*[#:.\-]?\s*\d+", " ", text)
    text = re.sub(r"#\d{3,}", " ", text)
    text = re.sub(r"\b(senior|snr|sr|junior|jr|mid|lead|staff|principal|i{1,3}|"
                  r"remote|contract|permanent|full[\s\-]?time|part[\s\-]?time|"
                  r"m/f/d|m/w/d|f/m/d|h/f|urgent|hiring|now)\b", " ", text)
    text = re.sub(r"\(.*?\)", " ", text)
    return re.sub(r"[^a-z0-9]", "", text)


def truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def first_email(text: str) -> str:
    """Return the most 'public looking' email address found in free text."""
    if not text:
        return ""
    found = _EMAIL_RE.findall(text)
    if not found:
        return ""
    preferred = ("careers@", "jobs@", "recruit", "hiring@", "hello@", "contact@",
                 "info@", "hr@", "talent@", "apply@", "work@", "people@")
    cleaned: list[str] = []
    for email in found:
        email = email.strip(".,;:")
        low = email.lower()
        if any(low.endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp")):
            continue
        if "example.com" in low or "sentry" in low or "noreply" in low or "no-reply" in low:
            continue
        cleaned.append(email)
    if not cleaned:
        return ""
    for email in cleaned:
        if any(p in email.lower() for p in preferred):
            return email
    return cleaned[0]


def first_phone(text: str) -> str:
    if not text:
        return ""
    for match in _PHONE_RE.findall(text):
        digits = re.sub(r"\D", "", match)
        if 9 <= len(digits) <= 15 and not digits.startswith("20"):
            return match.strip()
    return ""


def find_urls(text: str) -> list[str]:
    return [u.rstrip(".,);") for u in _URL_RE.findall(text or "")]


def any_in(needles: Iterable[str], haystack: str) -> bool:
    return any(n in haystack for n in needles)


def matched(needles: Iterable[str], haystack: str) -> list[str]:
    return [n for n in needles if n in haystack]


def word_present(word: str, haystack: str) -> bool:
    """Whole-word / whole-phrase containment check."""
    pattern = r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])"
    return re.search(pattern, haystack) is not None


NEGATORS = (
    "no ", "not ", "never ", "without ", "zero ", "n't ", "non-", "non ",
    "rather than ", "instead of ", "isn't ", "aren't ", "free from ",
    "we don't ", "we do not ", "forget ", "say goodbye to ", "avoid ",
    "few ", "rarely ", "hardly ", "seldom ", "minimal ", "little ",
    "optional", "if you prefer", "should you wish", "entirely up to you",
)


def is_negated(text: str, start: int, window: int = 45) -> bool:
    """True when a negator immediately precedes a match (e.g. 'no hybrid working')."""
    prefix = text[max(0, start - window): start].lower()
    return any(neg in prefix for neg in NEGATORS)


def any_regex(patterns: Iterable[str], haystack: str) -> str:
    """Return the first matching pattern's matched text, or ''."""
    for pattern in patterns:
        m = re.search(pattern, haystack, re.IGNORECASE)
        if m:
            return m.group(0).strip()
    return ""


def all_regex_matches(patterns: Iterable[str], haystack: str) -> list[str]:
    out: list[str] = []
    for pattern in patterns:
        for m in re.finditer(pattern, haystack, re.IGNORECASE):
            out.append(m.group(0).strip())
    return out


def local_timezone():
    """The candidate's own timezone, falling back to the default when unset.

    A profile compiled for someone in Lagos or Sydney carries their zone, and
    every date the run reports — how old an advert is, which calendar days the
    window covers — is only right when read in it.
    """
    from . import profile
    name = profile.active().timezone
    if name:
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            log.debug("profile timezone %r is not a known zone", name)
    return config.TIMEZONE


def now_local() -> datetime:
    """Now, in the candidate's timezone."""
    return datetime.now(local_timezone())


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(local_timezone())


_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")


def parse_datetime(value: Any) -> datetime | None:
    """Best-effort parse of the many date shapes job boards emit."""
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return to_local(value)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=local_timezone())
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000.0
        if seconds < 1_000_000_000:
            return None
        try:
            return to_local(datetime.fromtimestamp(seconds, tz=UTC))
        except (OverflowError, OSError, ValueError):
            return None

    text = str(value).strip()
    if not text:
        return None

    text = _TRAILING_PAREN_RE.sub("", text).strip()

    if re.fullmatch(r"\d{9,13}", text):
        return parse_datetime(int(text))

    low = text.lower()
    if low in ("today", "just posted", "new"):
        return now_local()
    if low == "yesterday":
        return now_local() - timedelta(days=1)
    rel = re.match(r"(\d+)\s*(minute|hour|day|week|month)s?\s*ago", low)
    if rel:
        amount, unit = int(rel.group(1)), rel.group(2)
        delta = {
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
            "month": timedelta(days=30 * amount),
        }[unit]
        return now_local() - delta

    cleaned = text.replace("Z", "+00:00")
    try:
        return to_local(datetime.fromisoformat(cleaned))
    except ValueError:
        pass

    formats = (
        "%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
        "%d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
        "%d/%m/%Y", "%m/%d/%Y", "%d %B %Y", "%B %d, %Y", "%d %b %Y",
    )
    for fmt in formats:
        try:
            return to_local(datetime.strptime(text, fmt))
        except ValueError:
            continue

    try:
        from dateutil import parser as date_parser

        return to_local(date_parser.parse(text, dayfirst=True))
    except Exception:  # noqa: BLE001
        return None


def freshness_window(days: int = config.FRESHNESS_DAYS) -> tuple[datetime, date, date]:
    """(cutoff datetime, period start date, period end date) in the candidate's timezone."""
    now = now_local()
    start_day = (now - timedelta(days=days - 1)).date()
    cutoff = datetime.combine(start_day, datetime.min.time(), tzinfo=local_timezone())
    return cutoff, start_day, now.date()


def job_age(posted: datetime | None) -> tuple[int | None, str]:
    if posted is None:
        return None, "Date not published"
    days = (now_local().date() - posted.astimezone(local_timezone()).date()).days
    if days <= 0:
        return max(days, 0), "Today"
    if days == 1:
        return 1, "Yesterday"
    return days, f"{days} days ago"


_CURRENCY_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR", "₹": "INR", "zł": "PLN", "kr": "SEK"}
_CURRENCY_CODES = ("GBP", "USD", "EUR", "CAD", "AUD", "CHF", "SEK", "NOK", "DKK", "PLN", "INR")

_AMOUNT_RE = re.compile(
    r"(?P<sym>[£$€₹])?\s*(?P<num>\d{1,3}(?:[,\s]\d{3})+|\d{2,7}(?:\.\d+)?)\s*(?P<suffix>k\b|m\b)?",
    re.IGNORECASE,
)


def detect_currency(text: str) -> str:
    if not text:
        return ""
    upper = text.upper()
    for code in _CURRENCY_CODES:
        if re.search(r"\b" + code + r"\b", upper):
            return code
    for symbol, code in _CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return ""


def _to_number(num: str, suffix: str | None) -> float:
    value = float(num.replace(",", "").replace(" ", ""))
    if suffix:
        low = suffix.lower().strip()
        if low == "k":
            value *= 1_000
        elif low == "m":
            value *= 1_000_000
    return value


def parse_compensation(text: str) -> dict[str, Any]:
    """Extract annual salary and/or day rate ranges from free text."""
    result: dict[str, Any] = {
        "salary_min": None, "salary_max": None,
        "day_rate_min": None, "day_rate_max": None,
        "currency": "",
    }
    if not text:
        return result

    currency = detect_currency(text)
    low = text.lower()

    is_day_rate = bool(re.search(
        r"(per day|/\s*day|a day|day rate|daily rate|\bpd\b|day[\s\-]?rate)", low))
    is_hourly = bool(re.search(r"(per hour|/\s*hour|an hour|hourly|\bph\b|/hr|per hr)", low))

    values: list[float] = []
    for m in _AMOUNT_RE.finditer(text):
        raw_num = m.group("num")
        if re.fullmatch(r"(19|20)\d{2}", raw_num.replace(",", "")) and not m.group("sym"):
            continue
        value = _to_number(raw_num, m.group("suffix"))
        if m.group("sym") and not currency:
            currency = _CURRENCY_SYMBOLS.get(m.group("sym"), "")
        values.append(value)

    if not values:
        result["currency"] = currency
        return result

    if is_hourly:
        hourly = [v for v in values if 8 <= v <= 500]
        if hourly:
            lo, hi = min(hourly), max(hourly)
            result["day_rate_min"] = round(lo * 8)
            result["day_rate_max"] = round(hi * 8)
            result["currency"] = currency
            return result

    if is_day_rate:
        daily = [v for v in values if 80 <= v <= 4_000]
        if daily:
            result["day_rate_min"] = min(daily)
            result["day_rate_max"] = max(daily)
            result["currency"] = currency
            return result

    annual = [v for v in values if 12_000 <= v <= 1_000_000]
    if annual:
        result["salary_min"] = min(annual)
        result["salary_max"] = max(annual)
    result["currency"] = currency
    return result


def normalize_amount(value: Any) -> float | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        number = float(str(value).replace(",", "").replace("$", "").replace("£", "").replace("€", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None
