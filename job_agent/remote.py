"""Is the work remote, and may this candidate do it from where they live?

Split out of `filters` because it answers a different question from the rest
of the gates. Everything here reads one advert's own words about location,
arrangement and eligibility; nothing here knows what trade is being searched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import config, profile
from .filters import Verdict
from .geography import (
    home_explicitly_eligible, home_mentioned, home_strongly_eligible,
    residency_patterns, worldwide, foreign_country_terms, home_known,
    home_label, location_country, region_excluding_home_terms, region_terms,
)
from .models import RawJob
from .utils import any_regex, is_negated, normalize, word_present


INTERNATIONAL_CONTRACTOR_TERMS = (
    "international contractor", "contractors worldwide", "global contractor",
    "hire globally", "hire internationally", "employer of record", " eor ",
    "deel", "remote.com", "oyster hr", "oysterhr", "globalization partners",
    "papaya global", "velocity global", "anywhere in the world",
    "we hire anywhere", "work from anywhere", "any country", "global team",
    "distributed across", "team spans", "timezone agnostic", "async-first",
    "async first",
)

TIMEZONE_CONFLICT_PATTERNS = (
    r"\b(?:pst|pdt|pacific time|mst|mdt|mountain time|cst|cdt|central time)\b",
    r"\best\b|\beastern time\b",
    r"\bovernight\b",
    r"\b(?:aest|aedt|australian eastern|ist\b|jst|sgt|nzst)\b",
    r"overlap[^.\n]{0,40}\b(?:pacific|pst|us hours|american hours)\b",
    r"\butc[\s\-+]?(?:0?[5-9]|1[0-2])\b",
    r"\butc\s*-\s*(?:5|6|7|8)\b",
)

VISA_SPONSOR_NEGATIVE = (
    r"(?:cannot|can not|unable to|do not|don't|will not|won't|no)\s+"
    r"(?:offer\s+|provide\s+)?(?:visa\s+)?sponsor(?:ship)?",
    r"no sponsorship (?:available|offered|provided)",
    r"sponsorship is not available",
)

AGENCY_MARKERS = (
    "recruitment agency", "recruiting agency", "staffing agency", "our client",
    "on behalf of our client", "recruitment partner", "talent partner",
    "we are recruiting for", "confidential client", "leading recruitment",
)
@dataclass
class RemoteVerdict(Verdict):
    remote_status: str = ""
    eligibility: str = ""
    prospect_worthy: bool = False
def _find_unnegated(patterns, text: str) -> tuple[str, str]:
    """Return (matched_text, pattern) for the first non-negated regex match."""
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            if not is_negated(text, m.start()):
                return m.group(0).strip(), pattern
    return "", ""
@dataclass
class RemoteReading:
    """One advert's remote and eligibility evidence, read once."""

    raw: RawJob
    location: str
    title: str
    body: str
    header: str
    hay: str
    details: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    home_explicit: bool = False

    @classmethod
    def read(cls, raw: RawJob) -> RemoteReading:
        location = normalize(raw.location_raw)
        title = normalize(raw.title)
        body = normalize(raw.description)
        tags = normalize(" ".join(raw.tags))
        header = f"{title} {location} {tags}"
        return cls(raw=raw, location=location, title=title, body=body,
                   header=header, hay=f"{header} {body}")

    @property
    def home_strong(self) -> bool:
        return home_strongly_eligible(self.hay, header=self.header)

    @property
    def worldwide(self) -> bool:
        return worldwide(self.hay)

    @property
    def international(self) -> bool:
        return any(term in self.hay for term in INTERNATIONAL_CONTRACTOR_TERMS)

    @property
    def region_hit(self) -> str:
        return next((t for t in region_terms()
                     if word_present(t, self.header) or word_present(t, self.body)), "")

    @property
    def home_in_location(self) -> bool:
        return home_mentioned(self.location) or home_mentioned(self.title)


ARRANGEMENTS = ("remote", "hybrid", "onsite")


def classify_arrangement(text: str, *, source_says_remote: bool = False) -> str:
    """Read an advert as remote, hybrid, onsite, or unknown.

    Hybrid is tested first: "remote/hybrid" and "three days a week in the
    office" both offer some home working and some office time, and calling
    either of them remote would put someone in a commute they did not agree to.
    """
    hybrid_hit, _ = _find_unnegated(config.HYBRID_PATTERNS, text)
    if hybrid_hit:
        return "hybrid"
    onsite_hit, _ = _find_unnegated(config.ONSITE_PATTERNS, text)
    if onsite_hit:
        return "onsite"
    if any(marker in text for marker in config.REMOTE_MARKERS) or source_says_remote:
        return "remote"
    return "unknown"


def arrangement_wanted(arrangement: str) -> bool:
    """Does the active search accept work on these terms?"""
    wanted = profile.active().work_arrangement
    if wanted == "any":
        return True
    if arrangement == "unknown":
        # Nothing said. Only a remote-only search can rule it out, because an
        # advert that never mentions working from home usually is not remote.
        return wanted != "remote"
    return arrangement == wanted


def arrangement_label(arrangement: str) -> str:
    return {"remote": "Remote", "hybrid": "Hybrid", "onsite": "On-site"}.get(
        arrangement, "Not stated")


def _office_attendance(r: RemoteReading) -> RemoteVerdict | None:
    hit, _pattern = _find_unnegated(config.HYBRID_REJECT_PATTERNS, r.hay)
    if not hit:
        return None
    return RemoteVerdict(
        False, f"Office attendance required — matched \"{hit}\"", "not_remote",
        remote_status="Hybrid / On-site", eligibility="N/A",
    )


def _remote_evidence(r: RemoteReading) -> RemoteVerdict | None:
    """Find a statement that the work is remote, or explain why none was found."""
    soft = any_regex(config.SOFT_TRAVEL_PATTERNS, r.hay)
    if soft:
        r.concerns.append(f"Occasional travel mentioned (\"{soft}\") — confirm frequency")

    marker = next((m for m in config.REMOTE_MARKERS if m in r.header), "")
    if not marker:
        for candidate in config.REMOTE_MARKERS:
            index = r.body.find(candidate)
            if index != -1 and not is_negated(r.body, index):
                marker = candidate
                break

    if marker:
        r.details.append(f"Remote confirmed in the posting (\"{marker}\")")
        return None
    if r.raw.extra.get("is_remote"):
        r.details.append("Listed as remote by the source board")
        return None

    if r.raw.extra.get("truncated_description") and r.home_in_location:
        return RemoteVerdict(
            False,
            "Remote status not stated in the truncated description — open the "
            "original advert to confirm",
            "not_remote",
            remote_status="Unknown — description truncated by the source",
            eligibility=f"{home_label()} location, remote working unconfirmed",
            prospect_worthy=True,
        )
    return RemoteVerdict(
        False, "No remote working statement found", "not_remote",
        remote_status="Not stated", eligibility="N/A",
    )


def _residency_restricted(r: RemoteReading) -> RemoteVerdict | None:
    hit, _ = _find_unnegated(residency_patterns(), r.hay)
    if not hit or r.home_strong or r.worldwide:
        return None
    if not home_known():
        return RemoteVerdict(
            False,
            f"Role restricted by residency — matched \"{hit}\". No home region "
            f"set, so this cannot be judged automatically.",
            "region_unknown",
            remote_status="Remote (region-restricted)",
            eligibility="Unknown — set a region or supply a CV",
            prospect_worthy=True,
        )
    return RemoteVerdict(
        False, f"US-only role — matched \"{hit}\"", "ineligible",
        remote_status="Remote (US only)",
        eligibility="Not eligible — US residency/authorisation required",
        prospect_worthy=r.home_explicit,
    )


def _named_country(match: re.Match) -> str:
    """The country a restriction phrase names, trimmed of trailing clauses."""
    country = normalize(match.group(1)).strip(" .,-")
    country = re.sub(r"^(the)\s+", "", country)
    return re.split(
        r"\s+(?:for|due|because|as|since|so|with|and|or|to|in|at|on|only)\b",
        country, maxsplit=1)[0].strip()


def _country_restricted(r: RemoteReading) -> RemoteVerdict | None:
    for pattern in config.COUNTRY_RESTRICTION_PATTERNS:
        for match in re.finditer(pattern, r.hay, re.IGNORECASE):
            country = _named_country(match)
            if not country:
                continue
            if home_mentioned(country) or worldwide(country):
                r.home_explicit = True
                continue
            if not any(word_present(c, country) or country.startswith(c)
                       for c in foreign_country_terms()):
                continue
            if r.home_strong or r.worldwide:
                continue
            return RemoteVerdict(
                False,
                f"Restricted to residents of {country.title()} — matched "
                f"\"{match.group(0).strip()}\"",
                "ineligible",
                remote_status="Remote (country restricted)",
                eligibility=f"Not eligible — {country.title()} residency required",
            )
    return None


def _region_excluding_home(r: RemoteReading) -> RemoteVerdict | None:
    hit = next((t for t in region_excluding_home_terms() if t in r.hay), "")
    if not hit or r.home_explicit or r.worldwide:
        return None
    return RemoteVerdict(
        False,
        f"Restricted to a region that excludes {home_label()} — matched "
        f"\"{hit}\"",
        "ineligible",
        remote_status=f"Remote (region excludes {home_label()})",
        eligibility=f"Not eligible — {home_label()} not confirmed",
        prospect_worthy=True,
    )


REMOTE_GATES = (
    _office_attendance,
    _remote_evidence,
    _residency_restricted,
    _country_restricted,
    _region_excluding_home,
)


def _positive_eligibility(r: RemoteReading) -> RemoteVerdict:
    """Having survived the gates, say on what grounds the advert is open to them."""
    home = home_label()
    if r.worldwide:
        r.details.append(f"Open worldwide, so {home} is covered")
        return RemoteVerdict(True, remote_status="Remote — Worldwide",
                             eligibility="Eligible — worldwide / work from anywhere")
    if r.home_in_location:
        r.details.append(f"{home} named directly in the job location")
        return RemoteVerdict(True, remote_status=f"Remote — {home}",
                             eligibility=f"Eligible — {home} named in the location")
    if r.home_explicit:
        region = r.region_hit
        status = (f"Remote — regional ({home} confirmed via \"{region}\")"
                  if region else f"Remote — {home}")
        r.details.append(f"{home} explicitly named as an eligible location")
        return RemoteVerdict(True, remote_status=status,
                             eligibility=f"Eligible — {home} explicitly permitted "
                                         f"in the posting")
    if r.international:
        r.details.append("Company states it hires contractors internationally")
        r.concerns.append("Confirm the company can contract with a supplier based "
                          f"in {home}")
        return RemoteVerdict(True, remote_status="Remote — International contractor",
                             eligibility="Eligible — hires international remote contractors")

    region = r.region_hit
    if region:
        return RemoteVerdict(
            False,
            f"Regional role (\"{region}\") but {home} eligibility is not confirmed",
            "ineligible",
            remote_status=f"Remote — regional ({home} unconfirmed)",
            eligibility="Unconfirmed — ask before applying",
            prospect_worthy=True,
        )

    based_in = location_country(r.raw.location_raw)
    if based_in:
        return RemoteVerdict(
            False,
            f"Remote role based in {based_in.title()} with no {home} or worldwide "
            f"eligibility stated",
            "ineligible",
            remote_status=f"Remote — {based_in.title()}",
            eligibility=f"Not eligible — advertised for {based_in.title()}",
            prospect_worthy=False,
        )
    return RemoteVerdict(
        False,
        f"Remote, but no location eligibility stated — {home} cannot be verified",
        "ineligible",
        remote_status="Remote — region unstated",
        eligibility="Unconfirmed — no eligible location stated",
        prospect_worthy=True,
    )


def _standing_concerns(r: RemoteReading) -> None:
    """Notes worth carrying, none of which reject the advert on their own."""
    home = home_label()
    timezone_hit = any_regex(TIMEZONE_CONFLICT_PATTERNS, r.hay)
    if timezone_hit:
        r.concerns.append(f"Working hours outside {home} implied "
                          f"(\"{timezone_hit}\") — check overlap")
    if any_regex(VISA_SPONSOR_NEGATIVE, r.hay):
        r.concerns.append("States no visa sponsorship — fine if you already have "
                          f"the right to work in {home}")
    if any(marker in r.hay for marker in AGENCY_MARKERS):
        r.concerns.append("Posted by a recruitment agency — end client is undisclosed")


def assess_remote(raw: RawJob) -> RemoteVerdict:
    """Can this advert be worked from where the candidate is?"""
    reading = RemoteReading.read(raw)
    reading.home_explicit = home_explicitly_eligible(reading.hay,
                                                      header=reading.header)

    for gate in REMOTE_GATES:
        verdict = gate(reading)
        if verdict is not None:
            return verdict

    verdict = _positive_eligibility(reading)
    if not verdict.passed:
        return verdict

    _standing_concerns(reading)
    verdict.details = reading.details
    verdict.concerns = reading.concerns
    return verdict
