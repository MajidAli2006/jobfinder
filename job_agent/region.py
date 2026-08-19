"""Working out where someone is, when nobody has said."""

from __future__ import annotations

import datetime
import locale as locale_module
import logging
import os
from dataclasses import dataclass

log = logging.getLogger("job_agent.region")


@dataclass(frozen=True)
class Region:
    """A place someone works from, and every way an advert might name it."""

    country: str
    terms: tuple[str, ...]
    cities: tuple[str, ...] = ()
    timezone: str = ""
    source: str = ""

    def __bool__(self) -> bool:
        return bool(self.country)


COUNTRIES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "United Kingdom": (
        ("united kingdom", "uk", "u.k.", "great britain", "britain", "gb", "gbr",
         "england", "scotland", "wales", "northern ireland"),
        ("london", "manchester", "birmingham", "edinburgh", "glasgow", "leeds",
         "bristol", "cardiff", "belfast", "liverpool", "cambridge", "oxford"),
    ),
    "United States": (
        ("united states", "usa", "us", "u.s.", "u.s.a.", "america"),
        ("new york", "san francisco", "los angeles", "chicago", "austin",
         "seattle", "boston", "denver", "atlanta", "miami"),
    ),
    "Canada": (("canada", "ca", "can"),
               ("toronto", "vancouver", "montreal", "ottawa", "calgary")),
    "Australia": (("australia", "au", "aus"),
                  ("sydney", "melbourne", "brisbane", "perth", "canberra")),
    "New Zealand": (("new zealand", "nz"), ("auckland", "wellington")),
    "Ireland": (("ireland", "republic of ireland", "ie"), ("dublin", "cork")),
    "Germany": (("germany", "deutschland", "de", "ger"),
                ("berlin", "munich", "hamburg", "frankfurt", "cologne")),
    "France": (("france", "fr"), ("paris", "lyon", "marseille", "toulouse")),
    "Spain": (("spain", "espana", "españa", "es"),
              ("madrid", "barcelona", "valencia", "seville")),
    "Portugal": (("portugal", "pt"), ("lisbon", "porto")),
    "Netherlands": (("netherlands", "holland", "nl"),
                    ("amsterdam", "rotterdam", "utrecht", "the hague")),
    "Belgium": (("belgium", "be"), ("brussels", "antwerp", "ghent")),
    "Switzerland": (("switzerland", "ch"), ("zurich", "geneva", "basel")),
    "Austria": (("austria", "at"), ("vienna", "graz")),
    "Italy": (("italy", "italia", "it"), ("milan", "rome", "turin")),
    "Poland": (("poland", "polska", "pl"), ("warsaw", "krakow", "wroclaw")),
    "Sweden": (("sweden", "se"), ("stockholm", "gothenburg")),
    "Norway": (("norway", "no"), ("oslo", "bergen")),
    "Denmark": (("denmark", "dk"), ("copenhagen", "aarhus")),
    "Finland": (("finland", "fi"), ("helsinki", "tampere")),
    "Czechia": (("czechia", "czech republic", "cz"), ("prague", "brno")),
    "Romania": (("romania", "ro"), ("bucharest", "cluj")),
    "Ukraine": (("ukraine", "ua"), ("kyiv", "kiev", "lviv")),
    "Turkey": (("turkey", "türkiye", "turkiye", "tr"), ("istanbul", "ankara")),
    "India": (("india", "in", "ind"),
              ("bangalore", "bengaluru", "mumbai", "delhi", "hyderabad", "pune")),
    "Pakistan": (("pakistan", "pk"), ("karachi", "lahore", "islamabad")),
    "Bangladesh": (("bangladesh", "bd"), ("dhaka",)),
    "Singapore": (("singapore", "sg"), ("singapore",)),
    "Japan": (("japan", "jp"), ("tokyo", "osaka")),
    "South Korea": (("south korea", "korea", "kr"), ("seoul",)),
    "China": (("china", "cn"), ("beijing", "shanghai", "shenzhen")),
    "Philippines": (("philippines", "ph"), ("manila", "cebu")),
    "Indonesia": (("indonesia", "id"), ("jakarta",)),
    "Vietnam": (("vietnam", "viet nam", "vn"), ("hanoi", "ho chi minh city")),
    "United Arab Emirates": (("united arab emirates", "uae", "ae"),
                             ("dubai", "abu dhabi")),
    "Saudi Arabia": (("saudi arabia", "sa"), ("riyadh", "jeddah")),
    "Israel": (("israel", "il"), ("tel aviv", "jerusalem")),
    "South Africa": (("south africa", "za"),
                     ("johannesburg", "cape town", "durban")),
    "Nigeria": (("nigeria", "ng"), ("lagos", "abuja")),
    "Kenya": (("kenya", "ke"), ("nairobi",)),
    "Egypt": (("egypt", "eg"), ("cairo",)),
    "Brazil": (("brazil", "brasil", "br"), ("sao paulo", "rio de janeiro")),
    "Mexico": (("mexico", "mx"), ("mexico city", "guadalajara")),
    "Argentina": (("argentina", "ar"), ("buenos aires",)),
    "Colombia": (("colombia", "co"), ("bogota", "medellin")),
    "Chile": (("chile", "cl"), ("santiago",)),
}

_ISO_TO_COUNTRY = {
    "GB": "United Kingdom", "UK": "United Kingdom", "US": "United States",
    "CA": "Canada", "AU": "Australia", "NZ": "New Zealand", "IE": "Ireland",
    "DE": "Germany", "FR": "France", "ES": "Spain", "PT": "Portugal",
    "NL": "Netherlands", "BE": "Belgium", "CH": "Switzerland", "AT": "Austria",
    "IT": "Italy", "PL": "Poland", "SE": "Sweden", "NO": "Norway",
    "DK": "Denmark", "FI": "Finland", "CZ": "Czechia", "RO": "Romania",
    "UA": "Ukraine", "TR": "Turkey", "IN": "India", "PK": "Pakistan",
    "BD": "Bangladesh", "SG": "Singapore", "JP": "Japan", "KR": "South Korea",
    "CN": "China", "PH": "Philippines", "ID": "Indonesia", "VN": "Vietnam",
    "AE": "United Arab Emirates", "SA": "Saudi Arabia", "IL": "Israel",
    "ZA": "South Africa", "NG": "Nigeria", "KE": "Kenya", "EG": "Egypt",
    "BR": "Brazil", "MX": "Mexico", "AR": "Argentina", "CO": "Colombia",
    "CL": "Chile",
}

_TZ_TO_COUNTRY = {
    "Europe/London": "United Kingdom", "Europe/Belfast": "United Kingdom",
    "Europe/Dublin": "Ireland",
    "Europe/Berlin": "Germany", "Europe/Paris": "France", "Europe/Madrid": "Spain",
    "Europe/Lisbon": "Portugal", "Europe/Amsterdam": "Netherlands",
    "Europe/Brussels": "Belgium", "Europe/Zurich": "Switzerland",
    "Europe/Vienna": "Austria", "Europe/Rome": "Italy", "Europe/Warsaw": "Poland",
    "Europe/Stockholm": "Sweden", "Europe/Oslo": "Norway",
    "Europe/Copenhagen": "Denmark", "Europe/Helsinki": "Finland",
    "Europe/Prague": "Czechia", "Europe/Bucharest": "Romania",
    "Europe/Kyiv": "Ukraine", "Europe/Kiev": "Ukraine",
    "Europe/Istanbul": "Turkey", "Europe/Moscow": "",
    "America/New_York": "United States", "America/Chicago": "United States",
    "America/Denver": "United States", "America/Los_Angeles": "United States",
    "America/Phoenix": "United States", "America/Anchorage": "United States",
    "Pacific/Honolulu": "United States",
    "America/Toronto": "Canada", "America/Vancouver": "Canada",
    "America/Edmonton": "Canada", "America/Winnipeg": "Canada",
    "America/Halifax": "Canada",
    "America/Sao_Paulo": "Brazil", "America/Mexico_City": "Mexico",
    "America/Argentina/Buenos_Aires": "Argentina", "America/Bogota": "Colombia",
    "America/Santiago": "Chile",
    "Australia/Sydney": "Australia", "Australia/Melbourne": "Australia",
    "Australia/Brisbane": "Australia", "Australia/Perth": "Australia",
    "Australia/Adelaide": "Australia",
    "Pacific/Auckland": "New Zealand",
    "Asia/Kolkata": "India", "Asia/Calcutta": "India",
    "Asia/Karachi": "Pakistan", "Asia/Dhaka": "Bangladesh",
    "Asia/Singapore": "Singapore", "Asia/Tokyo": "Japan", "Asia/Seoul": "South Korea",
    "Asia/Shanghai": "China", "Asia/Hong_Kong": "China",
    "Asia/Manila": "Philippines", "Asia/Jakarta": "Indonesia",
    "Asia/Ho_Chi_Minh": "Vietnam", "Asia/Saigon": "Vietnam",
    "Asia/Dubai": "United Arab Emirates", "Asia/Riyadh": "Saudi Arabia",
    "Asia/Jerusalem": "Israel", "Asia/Tel_Aviv": "Israel",
    "Africa/Johannesburg": "South Africa", "Africa/Lagos": "Nigeria",
    "Africa/Nairobi": "Kenya", "Africa/Cairo": "Egypt",
}


def build(country: str, *, timezone: str = "", source: str = "") -> Region:
    """A `Region` for a country name, with its naming variants filled in."""
    name = (country or "").strip()
    if not name:
        return Region("", ())

    key = _match_country(name)
    if key:
        terms, cities = COUNTRIES[key]
        return Region(key, terms, cities, timezone, source)
    return Region(name, (name.lower(),), (), timezone, source)


def _match_country(text: str) -> str:
    """Resolve free text — a name, an alias, an ISO code — to a known country."""
    low = text.strip().lower()
    if low.upper() in _ISO_TO_COUNTRY:
        return _ISO_TO_COUNTRY[low.upper()]
    for name, (terms, _) in COUNTRIES.items():
        if low == name.lower() or low in terms:
            return name
    return ""


def parse_list(text: str) -> tuple[Region, ...]:
    """Read "USA, UK and Australia" into regions, preserving the order given."""
    if not text:
        return ()
    cleaned = text.replace(" and ", ",").replace("&", ",").replace("/", ",")
    out: list[Region] = []
    seen: set[str] = set()
    for part in cleaned.split(","):
        region = build(part.strip(), source="stated in the request")
        if region and region.country not in seen:
            seen.add(region.country)
            out.append(region)
    return tuple(out)


def detect() -> Region:
    """Work out where this machine is, or return an empty Region."""
    zone = _system_timezone()
    if zone:
        country = _TZ_TO_COUNTRY.get(zone, "")
        if country:
            return build(country, timezone=zone,
                         source=f"your system timezone ({zone})")

    for value, label in _locale_candidates():
        code = value.split("_")[-1].split(".")[0].split("-")[-1].strip().upper()
        country = _ISO_TO_COUNTRY.get(code, "")
        if country:
            return build(country, timezone=zone, source=f"your system {label}")

    return Region("", (), timezone=zone)


def _system_timezone() -> str:
    """The IANA zone name this machine is set to, if it can be determined."""
    for candidate in (os.environ.get("TZ", "").strip(),):
        if candidate and "/" in candidate:
            return candidate
    try:
        info = datetime.datetime.now().astimezone().tzinfo
        key = getattr(info, "key", "")
        if key:
            return key
    except (OSError, ValueError):
        pass
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return ""


def _locale_candidates() -> list[tuple[str, str]]:
    """Locale strings worth inspecting, weakest signal last."""
    found: list[tuple[str, str]] = []
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var, "").strip()
        if value and value.lower() not in ("c", "posix"):
            found.append((value, f"{var} setting"))
    try:
        current = locale_module.getlocale()[0]
        if current:
            found.append((current, "locale"))
    except (TypeError, ValueError):
        pass
    return found
