"""Classification of an opportunity: employment type, contract terms, seniority, industry,
company stage and opportunity category.
"""

from __future__ import annotations

import re

from . import config
from . import profile
from .models import RawJob
from .utils import any_regex, normalize, word_present

_COMPANY_SIZE_PATTERNS = (
    (r"\b(?:1|2|3|4|5|6|7|8|9|10)\s*[-–]\s*10\s*(?:people|employees|person)", "1-10"),
    (r"\b(?:10|11)\s*[-–]\s*50\s*(?:people|employees)", "11-50"),
    (r"\b(?:50|51)\s*[-–]\s*200\s*(?:people|employees)", "51-200"),
    (r"\b(?:200|201)\s*[-–]\s*(?:500|1000)\s*(?:people|employees)", "201-1000"),
    (r"\b(?:1000|1,000|5000|10000)\+?\s*(?:people|employees)", "1000+"),
    (r"\bteam of (\d{1,4})\b", ""),
    (r"\bfortune 500\b", "1000+"),
    (r"\benterprise\b", "Large"),
)


def _first_match(mapping: dict[str, tuple[str, ...]], text: str, default: str) -> str:
    for label, patterns in mapping.items():
        if any_regex(patterns, text):
            return label
    return default


def employment_type(raw: RawJob, text: str) -> str:
    """Full Time / Part Time / Contract / Freelance."""
    declared = normalize(raw.employment_type_raw)
    if declared:
        if re.search(r"part[\s\-_]?time", declared):
            return "Part Time"
        if re.search(r"freelanc", declared):
            return "Freelance"
        if re.search(r"contract|temporary|fixed[\s\-_]?term|b2b", declared):
            return "Contract"
        if re.search(r"full[\s\-_]?time|permanent", declared):
            return "Full Time"
        if re.search(r"intern", declared):
            return "Internship"

    for label in ("Part Time", "Freelance", "Contract", "Full Time"):
        if any_regex(config.EMPLOYMENT_PATTERNS[label], text):
            return label
    return "Full Time" if "salary" in text or "benefits" in text else "Unknown"


def contract_type(text: str, emp_type: str) -> str:
    label = _first_match(config.CONTRACT_TYPE_PATTERNS, text, "")
    if label:
        return label
    if emp_type in ("Contract", "Freelance"):
        return "Unspecified contract"
    if emp_type in ("Full Time", "Part Time"):
        return "Permanent"
    return "N/A"


def seniority(raw: RawJob, text: str) -> str:
    title = normalize(raw.title)
    for label, patterns in config.SENIORITY_PATTERNS.items():
        if any_regex(patterns, title):
            return label
    years = re.search(r"(\d{1,2})\s*\+?\s*(?:-\s*\d{1,2}\s*)?years?[^.\n]{0,40}experience", text)
    if years:
        n = int(years.group(1))
        if n >= 8:
            return "Lead"
        if n >= 4:
            return "Senior"
        if n >= 2:
            return "Mid"
        return "Junior"
    for label, patterns in config.SENIORITY_PATTERNS.items():
        if any_regex(patterns, text):
            return label
    return "Unspecified"


_YEARS_NOT_A_REQUIREMENT = (
    "service", "operation", "operations", "trading", "history", "growth",
    "age", "ago", "existence", "partnership", "funding",
)

_YEARS_PATTERNS = (
    r"(\d{1,2})\s*\+?\s*(?:(?:to|-|–)\s*\d{1,2}\s*)?years?[^.\n]{0,40}\bexperience\b",
    r"\bexperience\b[^.\n]{0,25}?(\d{1,2})\s*\+?\s*years?",
    r"(\d{1,2})\s*\+?\s*years?\s+(?:of|in|with|as\s+an?)\s+"
    r"(?:professional\s+|commercial\s+|hands[\s\-]?on\s+|industry\s+|qualified\s+|"
    r"licensed\s+|certified\s+|registered\s+)?"
    r"(?!(?:%s)\b)[a-z]" % "|".join(_YEARS_NOT_A_REQUIREMENT),  # noqa: UP031
)


def required_years(text: str) -> int | None:
    values: list[int] = []
    for pattern in _YEARS_PATTERNS:
        for match in re.findall(pattern, text, re.IGNORECASE):
            try:
                years = int(match)
            except (TypeError, ValueError):
                continue
            if 0 < years <= 25:
                values.append(years)
    return max(values) if values else None


def experience_level(raw: RawJob, text: str, seniority_label: str,
                     years: int | None) -> str:
    """Collapse the role onto Beginner / Medium / Senior so the CV can be tailored."""
    title = normalize(raw.title)

    for label, patterns in config.SENIORITY_PATTERNS.items():
        if any_regex(patterns, title):
            return config.SENIORITY_TO_LEVEL.get(label, config.LEVEL_UNSPECIFIED)

    if years is not None:
        for threshold, level in config.LEVEL_YEAR_BANDS:
            if years <= threshold:
                return level
        return config.LEVEL_SENIOR

    if any_regex(config.BEGINNER_PHRASES, text):
        return config.LEVEL_BEGINNER
    if any_regex(config.SENIOR_PHRASES, text):
        return config.LEVEL_SENIOR

    return config.SENIORITY_TO_LEVEL.get(seniority_label, config.LEVEL_UNSPECIFIED)


def industry(raw: RawJob, text: str) -> str:
    return _first_match(config.INDUSTRY_PATTERNS, text, "Unknown / General Tech")


def startup_stage(text: str) -> str:
    if not any(sig in text for sig in config.STARTUP_SIGNALS):
        stage = _first_match(config.STARTUP_STAGE_PATTERNS, text, "")
        return stage or "N/A"
    return _first_match(config.STARTUP_STAGE_PATTERNS, text, "Unspecified stage")


def is_startup(text: str, stage: str) -> bool:
    return stage not in ("N/A", "") or any(sig in text for sig in config.STARTUP_SIGNALS)


def company_size(text: str) -> str:
    for pattern, label in _COMPANY_SIZE_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            if label:
                return label
            try:
                n = int(m.group(1))
            except (IndexError, ValueError):
                continue
            if n <= 10:
                return "1-10"
            if n <= 50:
                return "11-50"
            if n <= 200:
                return "51-200"
            return "201+"
    if "startup" in text or "start-up" in text:
        return "Startup (small)"
    return "Unknown"


def is_partnership(text: str, emp_type: str) -> bool:
    """Agency/studio/subcontracting opportunities the candidate could win as a supplier
    rather than an employee.
    """
    hits = sum(1 for sig in config.PARTNERSHIP_SIGNALS if sig in text)
    if hits >= 2:
        return True
    strong = ("statement of work", "request for proposal", "rfp", "white label",
              "white-label", "staff augmentation", "dedicated team",
              "outsourcing partner", "development partner", "subcontract",
              "looking for an agency", "software house", "retainer")
    return any(s in text for s in strong) and emp_type in ("Contract", "Freelance", "Unknown")


def opportunity_type(emp_type: str, partnership: bool, startup: bool) -> str:
    if partnership:
        return "Partnership / Agency"
    if emp_type == "Freelance":
        return "Freelance Project"
    if emp_type == "Contract":
        return "Contract Role"
    if startup:
        return "Startup Role"
    return "Permanent Job"


def core_skills_required(raw: RawJob, text: str) -> tuple[str, str]:
    """How firmly the advert asks for the search's two core technologies."""
    core = profile.active().core_terms
    if not core:
        return "No", "No"
    primary = core[0]
    secondary = core[1] if len(core) > 1 else core[0]

    title = normalize(raw.title)
    tags = normalize(" ".join(raw.tags))

    def level(term: str) -> str:
        if word_present(term, title) or word_present(term, tags):
            return "Yes"
        if not word_present(term, text):
            return "No"
        window_patterns = (
            rf"(?:must|required|require|requires|essential|strong|proven|expert|solid|"
            rf"deep|extensive|proficient|experience (?:with|in))[^.\n]{{0,80}}\b{term}\b",
            rf"\b{term}\b[^.\n]{{0,60}}(?:required|is a must|essential|mandatory)",
        )
        if any_regex(window_patterns, text):
            return "Yes"
        if any_regex((rf"(?:nice to have|bonus|plus|desirable|preferred|advantage)[^.\n]{{0,80}}\b{term}\b",
                      rf"\b{term}\b[^.\n]{{0,50}}(?:is a plus|a bonus|nice to have|desirable)"), text):
            return "Preferred"
        return "Mentioned"

    return level(primary), level(secondary)


def classify_all(raw: RawJob) -> dict:
    text = normalize(raw.haystack())
    emp = employment_type(raw, text)
    stage = startup_stage(text)
    startup = is_startup(text, stage)
    partnership = is_partnership(text, emp)
    core_skill, secondary_skill = core_skills_required(raw, text)
    seniority_label = seniority(raw, text)
    years = required_years(text)
    return {
        "employment_type": emp,
        "contract_type": contract_type(text, emp),
        "seniority": seniority_label,
        "experience_level": experience_level(raw, text, seniority_label, years),
        "required_years": years,
        "industry": industry(raw, text),
        "startup_stage": stage,
        "company_size": company_size(text),
        "is_startup": startup,
        "is_partnership": partnership,
        "opportunity_type": opportunity_type(emp, partnership, startup),
        "core_skill_required": core_skill,
        "secondary_skill_required": secondary_skill,
    }
