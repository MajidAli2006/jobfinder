"""Applicant tracking systems, as one adapter layer.

Each hosted system exposes a public JSON board. Both the curated seed list and
runtime discovery read them through these adapters.
"""

from __future__ import annotations

import re
import unicodedata

from ..models import RawJob
from ..utils import get_json, normalize_amount, parse_datetime, strip_html

#: Where each system's token appears in a URL, so a token can be recovered from
#: any advert that happens to link to one.
TOKEN_PATTERNS = {
    "greenhouse": (r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_\-]{2,40})",
                   r"greenhouse\.io/v1/boards/([a-z0-9_\-]{2,40})"),
    "lever": (r"jobs\.lever\.co/([a-z0-9_\-]{2,40})",),
    "ashby": (r"jobs\.ashbyhq\.com/([a-z0-9_\-]{2,40})",),
    "workable": (r"apply\.workable\.com/([a-z0-9_\-]{2,40})",),
    "recruitee": (r"([a-z0-9_\-]{2,40})\.recruitee\.com",),
    "smartrecruiters": (r"careers\.smartrecruiters\.com/([A-Za-z0-9_\-]{2,40})",),
    "teamtailor": (r"([a-z0-9_\-]{2,40})\.teamtailor\.com",),
}

#: Words that are not part of a company's board token.
_LEGAL_SUFFIX = re.compile(
    r"\b(ltd|limited|inc|incorporated|gmbh|llc|plc|group|holdings|technologies|"
    r"solutions|software|labs|systems|services|international|the)\b", re.IGNORECASE)


def slug(name: str) -> str:
    """A company name reduced to the shape most boards use as a token."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = _LEGAL_SUFFIX.sub("", text)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def discover_tokens(text: str) -> dict[str, set[str]]:
    """Every ATS token appearing in a blob of collected adverts."""
    found: dict[str, set[str]] = {}
    for kind, patterns in TOKEN_PATTERNS.items():
        tokens: set[str] = set()
        for pattern in patterns:
            tokens.update(m.lower() for m in re.findall(pattern, text, re.IGNORECASE))
        if tokens:
            found[kind] = tokens
    return found


def _greenhouse(token: str, board: dict) -> list[RawJob]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                    params={"content": "true"})
    if not isinstance(data, dict):
        return []
    out = []
    for item in data.get("jobs") or []:
        url = item.get("absolute_url") or ""
        out.append(RawJob(
            source="", source_id=f"gh-{token}-{item.get('id')}",
            title=item.get("title") or "",
            company=board.get("name") or token.title(),
            url=url, apply_url=url,
            description=strip_html(item.get("content")),
            location_raw=(item.get("location") or {}).get("name") or "",
            posted_at=parse_datetime(item.get("updated_at") or item.get("first_published")),
            company_website=board.get("website", ""),
            extra={"direct_employer": True, "ats": "greenhouse", "board_token": token},
        ))
    return out


def _lever(token: str, board: dict) -> list[RawJob]:
    data = get_json(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        categories = item.get("categories") or {}
        out.append(RawJob(
            source="", source_id=f"lever-{token}-{item.get('id')}",
            title=item.get("text") or "",
            company=board.get("name") or token.title(),
            url=item.get("hostedUrl") or "",
            apply_url=item.get("applyUrl") or item.get("hostedUrl") or "",
            description=strip_html(item.get("descriptionPlain") or item.get("description")),
            location_raw=categories.get("location") or "",
            posted_at=parse_datetime(item.get("createdAt")),
            employment_type_raw=categories.get("commitment") or "",
            company_website=board.get("website", ""),
            tags=[t for t in (categories.get("team"), categories.get("department")) if t],
            extra={"direct_employer": True, "ats": "lever", "board_token": token},
        ))
    return out


def _ashby(token: str, board: dict) -> list[RawJob]:
    data = get_json("https://api.ashbyhq.com/posting-api/job-board/" + token,
                    params={"includeCompensation": "true"})
    if not isinstance(data, dict):
        return []
    out = []
    for item in data.get("jobs") or []:
        comp = item.get("compensation") or {}
        components = comp.get("summaryComponents") or []
        out.append(RawJob(
            source="", source_id=f"ashby-{token}-{item.get('id')}",
            title=item.get("title") or "",
            company=board.get("name") or token.title(),
            url=item.get("jobUrl") or "",
            apply_url=item.get("applyUrl") or item.get("jobUrl") or "",
            description=strip_html(item.get("descriptionHtml") or item.get("descriptionPlain")),
            location_raw=item.get("location") or "",
            posted_at=parse_datetime(item.get("publishedAt") or item.get("updatedAt")),
            employment_type_raw=item.get("employmentType") or "",
            salary_raw=comp.get("compensationTierSummary") or "",
            salary_min=normalize_amount(components[0].get("minValue")) if components else None,
            company_website=board.get("website", ""),
            tags=[t for t in (item.get("department"), item.get("team")) if t],
            extra={"direct_employer": True, "ats": "ashby", "board_token": token,
                   "is_remote": bool(item.get("isRemote"))},
        ))
    return out


def _workable(token: str, board: dict) -> list[RawJob]:
    data = get_json(f"https://apply.workable.com/api/v1/widget/accounts/{token}",
                    params={"details": "true"})
    if not isinstance(data, dict):
        return []
    out = []
    for item in data.get("jobs") or []:
        url = item.get("url") or item.get("application_url") or ""
        out.append(RawJob(
            source="", source_id=f"workable-{token}-{item.get('shortcode') or url}",
            title=item.get("title") or "",
            company=board.get("name") or data.get("name") or token.title(),
            url=url, apply_url=item.get("application_url") or url,
            description=strip_html(item.get("description")),
            location_raw=", ".join(
                part for part in (item.get("city"), item.get("country")) if part),
            posted_at=parse_datetime(item.get("published_on") or item.get("created_at")),
            employment_type_raw=item.get("employment_type") or "",
            company_website=board.get("website", ""),
            extra={"direct_employer": True, "ats": "workable", "board_token": token,
                   "is_remote": bool(item.get("telecommuting"))},
        ))
    return out


def _recruitee(token: str, board: dict) -> list[RawJob]:
    data = get_json(f"https://{token}.recruitee.com/api/offers/")
    if not isinstance(data, dict):
        return []
    out = []
    for item in data.get("offers") or []:
        url = item.get("careers_url") or item.get("careers_apply_url") or ""
        out.append(RawJob(
            source="", source_id=f"recruitee-{token}-{item.get('id')}",
            title=item.get("title") or "",
            company=board.get("name") or item.get("company_name") or token.title(),
            url=url, apply_url=item.get("careers_apply_url") or url,
            description=strip_html(item.get("description")),
            location_raw=", ".join(
                part for part in (item.get("city"), item.get("country")) if part),
            posted_at=parse_datetime(item.get("published_at") or item.get("created_at")),
            employment_type_raw=item.get("employment_type_code") or "",
            company_website=board.get("website", ""),
            tags=[t for t in (item.get("department"),) if t],
            extra={"direct_employer": True, "ats": "recruitee", "board_token": token,
                   "is_remote": (item.get("remote") or "").lower() in ("yes", "true", "fully")},
        ))
    return out


def _smartrecruiters(token: str, board: dict) -> list[RawJob]:
    data = get_json(f"https://api.smartrecruiters.com/v1/companies/{token}/postings",
                    params={"limit": 100})
    if not isinstance(data, dict):
        return []
    out = []
    for item in data.get("content") or []:
        location = item.get("location") or {}
        posting_id = item.get("id")
        url = f"https://jobs.smartrecruiters.com/{token}/{posting_id}"
        out.append(RawJob(
            source="", source_id=f"sr-{token}-{posting_id}",
            title=item.get("name") or "",
            company=board.get("name") or (item.get("company") or {}).get("name") or token,
            url=url, apply_url=url,
            description=strip_html((item.get("jobAd") or {}).get("sections", {})
                                   .get("jobDescription", {}).get("text", "")),
            location_raw=", ".join(
                part for part in (location.get("city"), location.get("country")) if part),
            posted_at=parse_datetime(item.get("releasedDate")),
            company_website=board.get("website", ""),
            extra={"direct_employer": True, "ats": "smartrecruiters", "board_token": token,
                   "is_remote": bool(location.get("remote"))},
        ))
    return out


def _teamtailor(token: str, board: dict) -> list[RawJob]:
    """Teamtailor publishes a JSON Feed, so vacancies are `items`, not `jobs`.

    The feed is opt-in per tenant and 404s where it is switched off, which
    fetch_board turns into an empty list.
    """
    data = get_json(f"https://{token}.teamtailor.com/jobs.json")
    if not isinstance(data, dict):
        return []
    out = []
    for item in data.get("items") or []:
        url = item.get("url") or item.get("external_url") or ""
        out.append(RawJob(
            source="", source_id=f"tt-{token}-{item.get('id') or url}",
            title=item.get("title") or "",
            company=board.get("name") or data.get("title") or token.title(),
            url=url, apply_url=url,
            description=strip_html(item.get("content_html") or item.get("content_text")),
            location_raw=item.get("location") or "",
            posted_at=parse_datetime(item.get("date_published") or item.get("date_modified")),
            company_website=board.get("website", ""),
            extra={"direct_employer": True, "ats": "teamtailor", "board_token": token},
        ))
    return out


ADAPTERS = {
    "greenhouse": _greenhouse,
    "lever": _lever,
    "ashby": _ashby,
    "workable": _workable,
    "recruitee": _recruitee,
    "smartrecruiters": _smartrecruiters,
    "teamtailor": _teamtailor,
}


def fetch_board(kind: str, token: str, board: dict | None = None) -> list[RawJob]:
    """Every current vacancy on one employer's board. Never raises."""
    adapter = ADAPTERS.get((kind or "").lower())
    if not adapter or not token:
        return []
    try:
        return adapter(token, board or {})
    except Exception:  # noqa: BLE001 - one bad board must not stop the rest
        return []
