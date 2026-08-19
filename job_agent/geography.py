"""Where the candidate may work, and how adverts name those places.

Every question here is about place: what the home country is called, which
regional phrasings include it, which exclude it, and what country a location
string actually refers to. The filters ask these questions; they do not answer
them.
"""

from __future__ import annotations

import re

from . import config, profile
from .utils import normalize, word_present

def home_label() -> str:
    """How to name the candidate's country in text shown to them."""
    return profile.active().home_country or "your location"


def home_terms() -> tuple[str, ...]:
    """Every way an advert might name the country the candidate works from."""
    return profile.active().home_terms


def home_city_terms() -> tuple[str, ...]:
    return profile.active().home_city_terms


def home_known() -> bool:
    """Whether we know where the candidate will be working from."""
    return bool(profile.active().home_country and profile.active().home_terms)


def region_terms() -> tuple[str, ...]:
    """Regional phrasings that normally include home — "Europe" for a UK resident."""
    return profile.active().region_terms


def region_excluding_home_terms() -> tuple[str, ...]:
    """Regional phrasings that specifically exclude home."""
    return profile.active().region_excluding_home_terms


def foreign_country_terms() -> tuple[str, ...]:
    """Countries that, named as the sole permitted location, exclude the candidate."""
    active = profile.active()
    if not active.home_terms:
        return ()
    home = {t.lower() for t in active.home_terms}
    return tuple(c for c in config.FOREIGN_COUNTRY_TERMS if c.lower() not in home)


def residency_patterns() -> tuple[str, ...]:
    """Hand-tuned "must be in the US" regexes, applied only when home is not the US."""
    home = " ".join(home_terms()).lower()
    if any(t in home for t in ("united states", "usa", " us ", "u.s.")):
        return ()
    return config.US_ONLY_PATTERNS


def home_mentioned(text: str) -> bool:
    """Does this text name the candidate's country, or a city in it?"""
    for term in home_terms():
        if len(term) <= 3:
            if word_present(term, text):
                return True
        elif term in text:
            return True
    return any(term in text for term in home_city_terms())


ELIGIBILITY_CONTEXT = (
    "remote", "based", "reside", "resident", "live", "living", "located",
    "location", "eligib", "candidate", "applicant", "hire", "hiring",
    "work from", "anywhere", "employment", "employed", "payroll", "entity",
    "contract", "salary", "right to work", "authorised", "authorized",
    "open to", "welcome", "accept", "timezone", "time zone", "or the",
)


#: Phrasings where the country name sits against a word specific enough to
#: carry the meaning on its own, so they are safe to build from any term.
ANCHORED_CONFIRMATIONS = (
    "{t}-based", "{t} based", "{t} residents", "{t} resident", "{t} applicants",
    "{t} candidates", "{t} employment", "{t} payroll", "{t} entity",
    "{t} contract", "{t} right to work", "right to work in {t}",
    "right to work in the {t}", "work from home {t}", "{t}-wide",
)

#: Phrasings where the country name carries the meaning alone. Built only from
#: terms that cannot be read as ordinary English.
LOOSE_CONFIRMATIONS = (
    "remote {t}", "{t} remote", "including {t}", "including the {t}",
    "{t} or", "or the {t}", "{t} and", "and the {t}",
    "anywhere in {t}", "anywhere in the {t}",
)

#: ISO codes that are also everyday English words — "in" for India, "us" for
#: the United States. A loose phrasing built from one would match almost every
#: advert ever written, so they only ever appear in an anchored phrase.
AMBIGUOUS_TERMS = frozenset({
    "in", "us", "it", "no", "de", "at", "is", "be", "as", "an", "am", "on",
    "or", "so", "to", "do", "go", "id", "me", "my", "pa", "la", "ma", "re",
})


def strong_confirmations() -> tuple[str, ...]:
    """Ways an advert can say outright that someone living at home may be hired."""
    phrases: list[str] = []
    for term in home_terms():
        phrases.extend(pattern.format(t=term) for pattern in ANCHORED_CONFIRMATIONS)
        if term not in AMBIGUOUS_TERMS:
            phrases.extend(pattern.format(t=term) for pattern in LOOSE_CONFIRMATIONS)
    return tuple(dict.fromkeys(phrases))


INCIDENTAL_MENTION_CONTEXT = (
    "office", "headquarter", "hq", "branch", "premises", "site in",
    "gaap", "law", "regulator",
)


def incidental_mentions() -> tuple[str, ...]:
    """Contexts where naming the home country says nothing about eligibility.

    An employer's own address is the common case: "our Lagos office" tells a
    candidate in Lagos nothing about whether they may be hired.
    """
    phrases = list(INCIDENTAL_MENTION_CONTEXT)
    for city in home_city_terms():
        phrases.extend((f"our {city}", f"based in {city}"))
    return tuple(phrases)


_SENTENCE_SPLIT = re.compile(r"[.!?;\n]+")


def home_strongly_eligible(text: str, *, header: str = "") -> bool:
    """The home country named in the location or title, or an explicit eligibility
    phrase. Only this may override a country restriction.
    """
    if header and home_mentioned(header):
        return True
    return any(phrase in text for phrase in strong_confirmations())


def home_explicitly_eligible(text: str, *, header: str = "") -> bool:
    """Positive confirmation that someone living in the home country may be hired."""
    if home_strongly_eligible(text, header=header):
        return True

    for sentence in _SENTENCE_SPLIT.split(text):
        if not home_mentioned(sentence):
            continue
        if any(neg in sentence for neg in incidental_mentions()):
            continue
        if any(ctx in sentence for ctx in ELIGIBILITY_CONTEXT):
            return True
    return False


_METRO_RE = re.compile(
    r"(?:greater\s+)?([a-z][a-z\s\-']{2,28}?)\s+"
    r"(?:metropolitan\s+(?:area|region)|metro\s+area|area|region)\b", re.IGNORECASE)


def location_country(location: str) -> str:
    """Best-effort country for a location string, or "" when it names no place."""
    text = normalize(location or "")
    if not text or home_mentioned(text):
        return ""

    for country in foreign_country_terms():
        if word_present(country, text) or text.startswith(country):
            return country

    match = _METRO_RE.search(text)
    if match:
        city = match.group(1).strip()
        if city in config.CITY_COUNTRY:
            return config.CITY_COUNTRY[city]

    for part in (p.strip() for p in text.split(",")):
        if part in config.CITY_COUNTRY:
            return config.CITY_COUNTRY[part]
    return ""


def worldwide(text: str) -> bool:
    for term in config.WORLDWIDE_TERMS:
        if term == "anywhere":
            if re.search(r"\banywhere\b(?!\s+(?:in|within|across)\s+(?:the\s+)?(?!world\b)[a-z])", text):
                return True
        elif term in text:
            return True
    return False


