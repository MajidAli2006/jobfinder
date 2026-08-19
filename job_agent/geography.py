"""Where the candidate may work, and how adverts name those places.

Every question here is about place: what the home country is called, which
regional phrasings include it, which exclude it, and what country a location
string actually refers to. The filters ask these questions; they do not answer
them.
"""

from __future__ import annotations

import re

from . import config, profile, region
from .utils import normalize, word_present

def home_label() -> str:
    """How to name the candidate's country in text shown to them."""
    return profile.active().home_country or "your location"


#: Country names that are acronyms, so `.title()` would render them "Usa".
_ACRONYMS = frozenset({"usa", "us", "uk", "uae", "eu"})


def country_label(country: str) -> str:
    """A country name as it should read in a sentence shown to the candidate."""
    text = (country or "").strip()
    return text.upper() if text.lower() in _ACRONYMS else text.title()


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


_COMPONENT_SPLIT = re.compile(r"[,/|()\[\]]|\s+[-–]\s+")


def _components(text: str) -> list[str]:
    """The comma- or bracket-separated parts of a location string, in order."""
    return [part.strip() for part in _COMPONENT_SPLIT.split(text) if part.strip()]


def home_mentioned(text: str) -> bool:
    """Does this text name the candidate's country, or a city in it?"""
    short = False
    for term in home_terms():
        if len(term) <= 2 or term in AMBIGUOUS_TERMS:
            # "de" is Germany, Delaware, and an everyday word across half of
            # Europe. Two-letter codes are never read on their own; they are
            # settled by `resolve_location`, which reads the whole string.
            short = True
        elif word_present(term, text):
            # Whole words only: "India" must not be found inside "Indianapolis".
            return True
    if any(word_present(term, text) for term in home_city_terms()):
        return True
    return short and _is_home(resolve_location(text))


def _is_home(country: str) -> bool:
    """Is this country name the candidate's own?"""
    if not country:
        return False
    home = {t.lower() for t in home_terms()}
    home.add((profile.active().home_country or "").lower())
    return country.lower() in home


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


def resolve_location(text: str) -> str:
    """The country a location string names, or "" when it settles nothing.

    Named places are read before two-letter codes, because those codes are
    shared between countries and US states — CA is Canada and California, DE is
    Germany and Delaware, IN is India and Indiana. A code carrying both readings
    with nothing else in the string to separate them resolves to nothing at all,
    which leaves the advert unconfirmed rather than filed under the wrong flag.
    """
    parts = _components(text)
    seen = set(parts)

    for part in parts:
        if part in config.CITY_COUNTRY:
            return config.CITY_COUNTRY[part]

    match = _METRO_RE.search(text)
    if match:
        city = match.group(1).strip()
        if city in config.CITY_COUNTRY:
            return config.CITY_COUNTRY[city]

    if seen & config.US_STATE_NAMES:
        return "united states"

    codes = region.iso_countries()
    for index, part in enumerate(parts):
        if len(part) != 2:
            continue
        country, state = codes.get(part, ""), part in config.US_STATE_CODES
        if country and not state:
            return country
        if state and not country:
            return "united states"
        if not (state or country):
            continue
        # Both readings are live. Only the rest of the string can settle it.
        if seen & (config.US_STATE_CODES - {part}) or seen & {"usa", "us"}:
            return "united states"
        # "Rostock, Mecklenburg-Vorpommern, DE" is the international
        # city/region/country form; "Santa Barbara, CA" is the American
        # city/state one. Nothing else separates them, so the shape does.
        if index == len(parts) - 1 and len(parts) >= 3:
            return country
        return ""
    return ""


def _if_foreign(country: str) -> str:
    """A country name, unless it is the candidate's own."""
    return "" if _is_home(country) else country


def location_country(location: str) -> str:
    """Best-effort country for a location string, or "" when it names no place.

    Only ever names a country the candidate does not live in: the city lookup
    knows that Stuttgart is in Germany, but that is not a finding worth
    reporting to someone who lives there.
    """
    text = normalize(location or "")
    if not text or home_mentioned(text):
        return ""

    for country in foreign_country_terms():
        if word_present(country, text):
            return country

    return _if_foreign(resolve_location(text))


def worldwide(text: str) -> bool:
    for term in config.WORLDWIDE_TERMS:
        if term == "anywhere":
            if re.search(r"\banywhere\b(?!\s+(?:in|within|across)\s+(?:the\s+)?(?!world\b)[a-z])", text):
                return True
        elif term in text:
            return True
    return False


