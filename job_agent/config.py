"""Central configuration: candidate profile, filter vocabularies, thresholds, paths."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parent.parent


def _env_candidates() -> list[Path]:
    explicit = os.environ.get("JOBFINDER_ENV", "").strip()
    paths = [Path(explicit).expanduser()] if explicit else []
    return paths + [
        Path.cwd() / ".env",
        Path.home() / ".jobfinder" / ".env",
        ROOT / ".env",
    ]


def _load_dotenv() -> None:
    """Load KEY=value pairs from the first `.env` found, without overriding the shell."""
    seen: set[Path] = set()
    for path in _env_candidates():
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            for line in resolved.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip().removeprefix("export ").strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
        except OSError:
            continue


_load_dotenv()

def _workspace() -> Path:
    """Where reports and the seen-jobs database belong."""
    override = os.environ.get("JOBFINDER_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    if (ROOT / "pyproject.toml").is_file() or (ROOT / ".git").exists():
        return ROOT
    return Path.home() / ".jobfinder"


WORKSPACE = _workspace()

REPORTS_DIR = WORKSPACE / "reports"


def output_dir() -> Path:
    """Where finished workbooks go.

    The Desktop, because a report you have to go looking for does not get
    read. JOBFINDER_OUTPUT_DIR overrides it, and if there is no Desktop —
    a server, a container — the workspace reports directory is used instead.
    """
    override = os.environ.get("JOBFINDER_OUTPUT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    desktop = Path.home() / "Desktop"
    if desktop.is_dir():
        return desktop / "job finder"
    return REPORTS_DIR
DATA_DIR = WORKSPACE / "data"
CACHE_DIR = DATA_DIR / "cache"
SEEN_DB = DATA_DIR / "seen_jobs.sqlite3"
PACKAGE_DATA = ROOT / "data"
COMPANY_BOARDS_FILE = PACKAGE_DATA / "company_boards.json"
SAMPLE_JOBS_FILE = PACKAGE_DATA / "sample_jobs.json"


def zone(name: str) -> ZoneInfo:
    """Resolve an IANA zone, or say plainly what is missing.

    Windows ships no timezone database, so a bare source checkout there
    fails on import with an error that names neither the cause nor the fix.
    """
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            f"No timezone database entry for {name!r}. On Windows this means "
            f"the `tzdata` package is missing: install it with "
            f"`pip install tzdata`, or reinstall this tool with `pip install -e .`"
        ) from exc


TIMEZONE = zone("Europe/London")

FRESHNESS_DAYS = 30

KEEP_UNDATED = True

HTTP_TIMEOUT = 25
HTTP_RETRIES = 2
HTTP_DELAY = 0.8


DESCRIPTION_MAX_FETCHES = 150

DESCRIPTION_DELAY = 0.7

DESCRIPTION_WORKERS = 3

DESCRIPTION_CACHE_DAYS = 21


LINKEDIN_MAX_DETAILS = 400

LINKEDIN_DETAIL_DELAY = 1.3

LINKEDIN_MAX_DETAILS_DEEP = 900

LINKEDIN_CACHE_DAYS = 21

LINKEDIN_APPLICANTS_CACHE_DAYS = 2

LINKEDIN_DETAIL_WORKERS = 3

LINKEDIN_WINDOW_DAYS = 30

LINKEDIN_DEEP = False

LINKEDIN_SEARCH_DELAY = 0.25

LINKEDIN_SEARCH_WORKERS = 1
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36 JobHuntingAgent/1.0"
)


HOT_LEAD_SCORE = 85
MIN_QUALIFY_SCORE = 40
PROSPECT_MIN_SCORE = 55

PROSPECT_UNVERIFIED_MIN_SCORE = 35


SALARY_FLOOR_USD = 0

CONTRACT_DAYS_PER_YEAR = 220

CURRENCY_TO_USD = {
    "USD": 1.00, "GBP": 1.27, "EUR": 1.09, "CHF": 1.12, "CAD": 0.74,
    "AUD": 0.66, "NZD": 0.61, "SGD": 0.74, "JPY": 0.0067, "SEK": 0.095,
    "NOK": 0.093, "DKK": 0.146, "PLN": 0.25, "CZK": 0.043, "HUF": 0.0028,
    "RON": 0.22, "BGN": 0.56, "AED": 0.27, "SAR": 0.27, "ILS": 0.27,
    # The rest of the Gulf, where Bayt is the main board, plus Ghana and
    # Thailand. Without these a local salary cannot be priced at all.
    "QAR": 0.27, "KWD": 3.26, "BHD": 2.65, "OMR": 2.60, "JOD": 1.41,
    "GHS": 0.064, "THB": 0.029, "MYR": 0.22, "TWD": 0.031, "HKD": 0.128,
    "ZAR": 0.055, "BRL": 0.18, "MXN": 0.055, "ARS": 0.001, "TRY": 0.030,
    "INR": 0.012, "PKR": 0.0036, "BDT": 0.0085, "NPR": 0.0075, "LKR": 0.0033,
    "PHP": 0.017, "IDR": 0.000062, "VND": 0.000039, "NGN": 0.00065,
    "KES": 0.0077, "EGP": 0.021, "UAH": 0.024, "RUB": 0.011,
}


EXCLUDE_LARGE_EMPLOYERS = True

#: Mine the adverts collected in a run for employer ATS boards nobody
#: curated. Off in quick mode, where the extra requests are not worth it.
DISCOVER_EMPLOYER_BOARDS = True

#: Ask which regional platforms serve the country being searched.
DISCOVER_PLATFORMS = True

#: Read an employer's own site for published contact details. Several
#: requests per employer, so it belongs to the deeper tiers.
FETCH_EMPLOYER_CONTACTS = True

LARGE_SIZE_LABELS = ("1000+", "Large")

LARGE_EMPLOYER_HEADCOUNT = 1000

CHECK_COMPANY_SIZE_ONLINE = True

COMPANY_SIZE_CACHE_DAYS = 120

LARGE_EMPLOYER_NAMES = (
    r"thomson\s*reuters|canonical|canva|commonwealth\s*bank|tesco|stripe|motorola|cond[eé]\s*nast",
    r"generali|luxoft|nord\s*security|chime|binance|okx|alphasense|exfo|nando|spacelabs|tide\b|teya",
    r"luminor|revolut|monzo|starling|wise\b|zepz|worldremit|remitly|payoneer|airwallex|klarna|adyen|n26",
    r"duolingo|dropbox|mozilla|tripadvisor|viator|geico|google|alphabet|meta\b|amazon|microsoft|apple\b",
    r"ibm\b|oracle|sap\b|siemens|bosch|philips|ericsson|nokia|samsung|sony|adobe|salesforce|shopify",
    r"atlassian|twilio|datadog|cloudflare|gitlab|elastic|hashicorp|coinbase|robinhood|deliveroo",
    r"just\s*eat|delivery\s*hero|hellofresh|zalando|bolt\b|grab\b|careem|capgemini|accenture|infosys",
    r"tata\b|wipro|cognizant|deloitte|kpmg|pwc\b|mckinsey|hsbc|barclays|lloyds|natwest|santander",
    r"vodafone|virgin\s*media|sainsbury|asda|john\s*lewis|boots\b|aviva|axa\b|allianz|zurich|prudential",
    r"unilever|nestl|pepsi|coca[-\s]?cola|mcdonald|starbucks|nike\b|adidas|ikea|zara\b|inditex",
    r"ubisoft|electronic\s*arts|activision|epic\s*games|riot\s*games|shell\b|\bbp\b|totalenergies",
    r"jpmorgan|goldman|morgan\s*stanley|citigroup|wells\s*fargo|american\s*express|visa\b|mastercard",
    r"paypal|intuit|workday|servicenow|snowflake|palantir|nvidia|intel\b|\bamd\b|qualcomm|cisco|dell\b",
    r"lenovo|huawei|xiaomi|alibaba|tencent|bytedance|tiktok|uber\b|lyft|airbnb|booking\.com|expedia",
)


CITY_COUNTRY = {
    "mumbai": "india", "delhi": "india", "new delhi": "india", "bengaluru": "india",
    "bangalore": "india", "hyderabad": "india", "chennai": "india", "pune": "india",
    "kolkata": "india", "ahmedabad": "india", "noida": "india", "gurgaon": "india",
    "gurugram": "india", "coimbatore": "india", "kochi": "india", "jaipur": "india",
    "indore": "india", "chandigarh": "india", "nagpur": "india", "surat": "india",
    "karachi": "pakistan", "lahore": "pakistan", "islamabad": "pakistan",
    "rawalpindi": "pakistan", "faisalabad": "pakistan",
    "dhaka": "bangladesh", "colombo": "sri lanka", "kathmandu": "nepal",
    "lisbon": "portugal", "porto": "portugal", "madrid": "spain", "barcelona": "spain",
    "valencia": "spain", "seville": "spain", "malaga": "spain",
    "paris": "france", "lyon": "france", "toulouse": "france", "marseille": "france",
    "berlin": "germany", "munich": "germany", "hamburg": "germany",
    "frankfurt": "germany", "cologne": "germany", "stuttgart": "germany",
    "dusseldorf": "germany", "düsseldorf": "germany",
    "amsterdam": "netherlands", "rotterdam": "netherlands", "utrecht": "netherlands",
    "eindhoven": "netherlands", "the hague": "netherlands",
    "brussels": "belgium", "antwerp": "belgium", "ghent": "belgium",
    "zurich": "switzerland", "zürich": "switzerland", "geneva": "switzerland",
    "basel": "switzerland", "bern": "switzerland",
    "vienna": "austria", "graz": "austria", "salzburg": "austria",
    "milan": "italy", "rome": "italy", "turin": "italy", "naples": "italy",
    "bologna": "italy", "florence": "italy",
    "stockholm": "sweden", "gothenburg": "sweden", "malmo": "sweden", "malmö": "sweden",
    "oslo": "norway", "bergen": "norway", "trondheim": "norway",
    "copenhagen": "denmark", "aarhus": "denmark",
    "helsinki": "finland", "tampere": "finland", "espoo": "finland",
    "warsaw": "poland", "krakow": "poland", "kraków": "poland", "wroclaw": "poland",
    "wrocław": "poland", "gdansk": "poland", "gdańsk": "poland", "poznan": "poland",
    "zamosc": "poland", "zamość": "poland", "lodz": "poland", "katowice": "poland",
    "prague": "czechia", "brno": "czechia", "bratislava": "slovakia",
    "budapest": "hungary", "bucharest": "romania", "cluj": "romania",
    "cluj-napoca": "romania", "timisoara": "romania", "sofia": "bulgaria",
    "belgrade": "serbia", "zagreb": "croatia", "ljubljana": "slovenia",
    "athens": "greece", "thessaloniki": "greece",
    "dublin": "ireland", "cork": "ireland", "galway": "ireland",
    "kyiv": "ukraine", "kiev": "ukraine", "lviv": "ukraine",
    "tallinn": "estonia", "riga": "latvia", "vilnius": "lithuania",
    "luxembourg": "luxembourg", "reykjavik": "iceland",
    "istanbul": "turkey", "ankara": "turkey", "izmir": "turkey",
    "new york": "united states", "san francisco": "united states",
    "los angeles": "united states", "chicago": "united states",
    "boston": "united states", "seattle": "united states", "austin": "united states",
    "atlanta": "united states", "denver": "united states", "dallas": "united states",
    "houston": "united states", "miami": "united states", "phoenix": "united states",
    "philadelphia": "united states", "san diego": "united states",
    "portland": "united states", "san jose": "united states", "irving": "united states",
    "charlotte": "united states", "nashville": "united states",
    "minneapolis": "united states", "detroit": "united states",
    "salt lake city": "united states", "raleigh": "united states",
    "pittsburgh": "united states", "columbus": "united states",
    "toronto": "canada", "vancouver": "canada", "montreal": "canada",
    "calgary": "canada", "ottawa": "canada", "edmonton": "canada",
    "waterloo": "canada", "mississauga": "canada",
    "mexico city": "mexico", "guadalajara": "mexico", "monterrey": "mexico",
    "dubai": "united arab emirates", "abu dhabi": "united arab emirates",
    "sharjah": "united arab emirates", "riyadh": "saudi arabia",
    "jeddah": "saudi arabia", "doha": "qatar", "kuwait city": "kuwait",
    "manama": "bahrain", "muscat": "oman", "amman": "jordan",
    "tel aviv": "israel", "jerusalem": "israel", "haifa": "israel",
    "cairo": "egypt", "alexandria": "egypt", "lagos": "nigeria",
    "abuja": "nigeria", "nairobi": "kenya", "accra": "ghana",
    "cape town": "south africa", "johannesburg": "south africa",
    "casablanca": "morocco", "tunis": "tunisia",
    "singapore": "singapore", "tokyo": "japan", "osaka": "japan", "kyoto": "japan",
    "seoul": "south korea", "beijing": "china", "shanghai": "china",
    "shenzhen": "china", "hong kong": "hong kong", "taipei": "taiwan",
    "bangkok": "thailand", "jakarta": "indonesia", "manila": "philippines",
    "cebu": "philippines", "kuala lumpur": "malaysia", "hanoi": "vietnam",
    "ho chi minh": "vietnam", "ho chi minh city": "vietnam",
    "sydney": "australia", "melbourne": "australia", "brisbane": "australia",
    "perth": "australia", "adelaide": "australia", "canberra": "australia",
    "auckland": "new zealand", "wellington": "new zealand",
    "sao paulo": "brazil", "são paulo": "brazil", "rio de janeiro": "brazil",
    "belo horizonte": "brazil", "buenos aires": "argentina", "cordoba": "argentina",
    "santiago": "chile", "bogota": "colombia", "bogotá": "colombia",
    "medellin": "colombia", "medellín": "colombia", "lima": "peru",
    "montevideo": "uruguay", "san jose, costa rica": "costa rica",
}

LOW_RATE_MARKETS = (
    "pakistan", "india", "bangladesh", "nepal", "sri lanka", "syria",
    "afghanistan", "myanmar", "cambodia", "laos", "indonesia", "philippines",
    "vietnam", "nigeria", "kenya", "ghana", "ethiopia", "uganda", "tanzania",
    "egypt", "morocco", "algeria", "tunisia", "bolivia", "venezuela",
    "honduras", "nicaragua", "uzbekistan", "kyrgyzstan", "tajikistan",
)

MARKET_SCOPE_PATTERNS = (
    r"(?:only|exclusively)?\s*(?:for|from|in|based in|located in|residing in)\s+({markets})\b",
    r"\b({markets})\s*(?:based|only|candidates|applicants|residents|nationals|team)\b",
    r"\b(?:candidates|applicants|developers|engineers)\s+(?:from|in)\s+({markets})\b",
    r"\b(?:hiring|recruiting|looking)\s+(?:in|from)\s+({markets})\b",
)


REMOTE_MARKERS = (
    "fully remote", "100% remote", "fully-remote", "remote-first", "remote first",
    "work from home", "work-from-home", "wfh", "work from anywhere",
    "work-from-anywhere", "remote only", "remote-only", "distributed team",
    "fully distributed", "remotely", "telecommute", "home-based", "home based",
    "anywhere in the world", "location independent", "global remote", "remote",
)

#: Phrasing that means *some* home working alongside required office time.
#: Checked before the on-site patterns: "remote/hybrid" is hybrid, not remote,
#: and "3 days a week in the office" is hybrid, not fully on-site.
HYBRID_PATTERNS = (
    r"\bhybrid\b",
    r"\bpart[\s\-]?remote\b",
    r"\bremote\s*/\s*hybrid\b",
    r"\bhybrid\s*/\s*remote\b",
    r"\bflexible hybrid\b",
    r"\b\d\s*(?:\+)?\s*days?\s*(?:a|per)\s*week\s*(?:in|at|from)?\s*(?:the\s*)?office\b",
    r"\b\d\s*(?:\+)?\s*days?\s*(?:a|per)\s*week\s*(?:on[\s\-]?site|onsite|in[\s\-]?person)\b",
    r"\b(?:once|twice|\d+\s*times?)\s*(?:a|per)\s*(?:week|month)\s*(?:in|at)\s*(?:the\s*)?office\b",
    r"\bweekly\s*(?:office|on[\s\-]?site)\b",
    r"\bmonthly\s*(?:office|on[\s\-]?site)\s*(?:attendance|visit|presence|day)",
    r"\bmix of (?:home|remote) and office\b",
    r"\bsplit between (?:home|remote) and (?:the )?office\b",
)

#: Phrasing that means the work happens at the employer's place, full stop.
ONSITE_PATTERNS = (
    r"\bon[\s\-]?site\b",
    r"\bonsite\b",
    r"\bin[\s\-]?office\b",
    r"\boffice[\s\-]?based\b",
    r"\bbased in (?:our|the) office\b",
    r"\bcommut(?:e|able|ing)\b",
    r"\bable to travel to (?:the|our) office\b",
    r"\bin[\s\-]?person\s*(?:attendance|presence)\s*required\b",
    r"\bmust be (?:able to be )?in the office\b",
    r"\boffice attendance\b",
    r"\brelocat(?:e|ion)\s*(?:is\s*)?(?:required|expected|necessary)\b",
    r"\bwilling(?:ness)? to relocate\b",
    r"\bfully on[\s\-]?site\b",
    r"\bshift pattern\b",
    r"\bsite[\s\-]based\b",
)

#: Kept as the union, for the places that only care "is this fully remote?".
HYBRID_REJECT_PATTERNS = HYBRID_PATTERNS + ONSITE_PATTERNS

SOFT_TRAVEL_PATTERNS = (
    r"\b(?:quarterly|annual|yearly|twice a year|bi[\s\-]?annual)\s*(?:on[\s\-]?site|offsite|off[\s\-]?site|meet[\s\-]?up|retreat|gathering|team\s*week)\b",
    r"\b(?:a few|couple of|1[\s\-]2|2[\s\-]3)\s*times?\s*(?:a|per)\s*year\b",
    r"\boccasional travel\b",
    r"\bcompany retreat\b",
)

WORLDWIDE_TERMS = (
    "worldwide", "world wide", "anywhere in the world", "work from anywhere",
    "anywhere in world", "global remote", "remote global", "globally",
    "any country", "any location", "anywhere", "international remote",
    "no location restriction", "no geographic restriction", "location: global",
    "location agnostic", "any timezone", "any time zone",
)

US_ONLY_PATTERNS = (
    r"\bus[\s\-]?based\s*(?:only|candidates|applicants|employees)\b",
    r"\bmust (?:be|reside|live|be located|be based)[^.\n]{0,40}\b(?:in|within)\s*(?:the\s*)?(?:us|u\.s\.|usa|united states)\b",
    r"\b(?:us|u\.s\.|usa|united states)\s*(?:work\s*)?(?:authorization|authorisation|work permit)\s*(?:is\s*)?(?:required|necessary)\b",
    r"\bauthoriz(?:ed|ation) to work in the (?:us|u\.s\.|usa|united states)\b",
    r"\bmust be authorized to work in the united states\b",
    r"\b(?:green card|permanent resident)\s*(?:holder|status)?\s*(?:required|only)\b",
    r"\bw[\s\-]?2\s*(?:only|employees only)\b",
    r"\bus citizens? only\b",
    r"\bus citizenship (?:is )?required\b",
    r"\bsecurity clearance\b",
    r"\bmust (?:hold|have) (?:a )?(?:us|american) (?:citizenship|passport)\b",
    r"\bonly (?:considering|accepting) (?:candidates|applicants)[^.\n]{0,30}\b(?:in|from)\s*(?:the\s*)?(?:us|usa|united states)\b",
    r"\bremote\s*\(\s*(?:us|usa|united states)[^)]*\)",
    r"\bremote\s*[-–—]\s*(?:us|usa|united states)\b",
    r"\b(?:us|usa|united states)\s*(?:remote|only)\b",
    r"\bnot (?:able to )?(?:sponsor|offer sponsorship)[^.\n]{0,40}\b(?:us|united states)\b",
)

COUNTRY_RESTRICTION_PATTERNS = (
    r"must (?:be |currently )?(?:reside|live|be located|be based|be resident)[^.\n]{0,25}?\bin\s+((?:the\s+)?[A-Za-z][A-Za-z\s\.\-]{2,30}?)(?:[,\.\n\)]|$| and| or| to)",
    r"only (?:open to|available to|accepting|considering)[^.\n]{0,30}?\b(?:candidates|applicants|residents|people|developers)\s*(?:based |located |residing |living )?(?:in|from)\s+((?:the\s+)?[A-Za-z][A-Za-z\s\.\-]{2,30}?)(?:[,\.\n\)]|$)",
    r"\b([A-Za-z][A-Za-z\s\.\-]{2,30}?)\s*(?:residents?|citizens?|nationals?)\s*only\b",
    r"\bcandidates? must be located in\s+((?:the\s+)?[A-Za-z][A-Za-z\s\.\-]{2,30}?)(?:[,\.\n\)]|$)",
    r"\bthis (?:role|position) is (?:only )?(?:open|available) to (?:residents of|people in|candidates in)\s+((?:the\s+)?[A-Za-z][A-Za-z\s\.\-]{2,30}?)(?:[,\.\n\)]|$)",
)

FOREIGN_COUNTRY_TERMS = (
    "united states", "usa", "us", "u.s.", "canada", "india", "pakistan", "germany",
    "france", "spain", "portugal", "italy", "netherlands", "poland", "romania",
    "ukraine", "brazil", "mexico", "argentina", "colombia", "australia",
    "new zealand", "singapore", "japan", "china", "philippines", "indonesia",
    "vietnam", "nigeria", "kenya", "south africa", "egypt", "uae",
    "united arab emirates", "saudi arabia", "turkey", "israel", "ireland",
    "sweden", "norway", "denmark", "finland", "switzerland", "austria", "belgium",
    "czech republic", "czechia", "hungary", "bulgaria", "greece", "serbia",
    "croatia", "lithuania", "latvia", "estonia", "slovakia", "slovenia",
    "united kingdom", "uk", "britain", "england", "scotland", "wales",
)


EMPLOYMENT_PATTERNS = {
    "Part Time": (r"\bpart[\s\-]?time\b", r"\bp/t\b", r"\b(?:20|24|25|30)\s*hours?\s*(?:a|per)\s*week\b"),
    "Freelance": (r"\bfreelanc(?:e|er|ing)\b", r"\bgig\b", r"\bper[\s\-]?project\b", r"\bproject[\s\-]?based\b"),
    "Contract": (r"\bcontract(?:or|ing)?\b", r"\bb2b\b", r"\bday[\s\-]?rate\b", r"\bfixed[\s\-]?term\b",
                 r"\bir35\b", r"\bumbrella\b", r"\bstatement of work\b", r"\bsow\b", r"\b\d+\s*month\s*contract\b"),
    "Full Time": (r"\bfull[\s\-]?time\b", r"\bpermanent\b", r"\bf/t\b", r"\bpermie\b"),
}

CONTRACT_TYPE_PATTERNS = {
    "Outside IR35": (r"outside\s*ir35",),
    "Inside IR35": (r"inside\s*ir35",),
    "B2B": (r"\bb2b\b", r"business to business contract"),
    "Fixed-Term": (r"fixed[\s\-]?term", r"\bftc\b", r"\d+\s*month\s*contract"),
    "Day Rate": (r"day[\s\-]?rate", r"per\s*day", r"/\s*day\b", r"\bpd\b"),
    "Umbrella": (r"\bumbrella\b",),
    "Permanent": (r"\bpermanent\b", r"\bpermie\b"),
}

SENIORITY_PATTERNS = {
    "Principal": (r"\bprincipal\b", r"\bstaff\b", r"\barchitect\b"),
    "Lead": (r"\blead\b", r"\bhead of\b", r"\bteam lead\b", r"\btech lead\b", r"\bengineering manager\b"),
    "Senior": (r"\bsenior\b", r"\bsr\.?\b", r"\bsnr\b", r"\bexperienced\b", r"\bexpert\b", r"\biii\b"),
    "Mid": (r"\bmid[\s\-]?level\b", r"\bintermediate\b", r"\bii\b"),
    "Junior": (r"\bjunior\b", r"\bjr\.?\b", r"\bgraduate\b", r"\bentry[\s\-]?level\b", r"\btrainee\b"),
}

LEVEL_BEGINNER = "Beginner"
LEVEL_MEDIUM = "Medium"
LEVEL_SENIOR = "Senior"
LEVEL_UNSPECIFIED = "Not specified"

SENIORITY_TO_LEVEL = {
    "Junior": LEVEL_BEGINNER,
    "Mid": LEVEL_MEDIUM,
    "Senior": LEVEL_SENIOR,
    "Lead": LEVEL_SENIOR,
    "Principal": LEVEL_SENIOR,
}

LEVEL_YEAR_BANDS = ((2, LEVEL_BEGINNER), (4, LEVEL_MEDIUM))

BEGINNER_PHRASES = (
    r"\bentry[\s\-]?level\b", r"\bgraduate (?:role|position|scheme|developer|engineer)\b",
    r"\bno (?:prior )?experience (?:is )?(?:required|necessary)\b",
    r"\bjunior\b", r"\btrainee\b", r"\bapprentice\b", r"\bearly career\b",
    r"\bfirst (?:developer )?job\b", r"\bbootcamp\b", r"\bcareer changer\b",
)
SENIOR_PHRASES = (
    r"\bsenior\b", r"\bstaff engineer\b", r"\bprincipal\b", r"\btech(?:nical)? lead\b",
    r"\bteam lead\b", r"\bhead of\b", r"\barchitect\b", r"\bmentor(?:ing|ship)?\b",
    r"\blead(?:ing)? (?:a |the )?team\b", r"\bown(?:ership of)? the (?:architecture|roadmap)\b",
)

INDUSTRY_PATTERNS = {
    "Fintech / Banking": (r"\bfintech\b", r"\bbanking\b", r"\bbank\b", r"\bpayments?\b",
                          r"\bfinancial services\b", r"\bneobank\b", r"\bopen banking\b",
                          r"\btrading\b", r"\bwallet\b", r"\blending\b"),
    "Healthtech": (r"\bhealth[\s\-]?tech\b", r"\bhealthcare\b", r"\bmedical\b", r"\bclinical\b", r"\bpatient\b"),
    "E-commerce / Retail": (r"\be[\s\-]?commerce\b", r"\bretail\b", r"\bmarketplace\b", r"\bshopping\b"),
    "EdTech": (r"\bed[\s\-]?tech\b", r"\beducation\b", r"\blearning platform\b"),
    "Gaming": (r"\bgaming\b", r"\bgames?\b", r"\bcasino\b", r"\bbetting\b", r"\bigaming\b"),
    "Logistics / Mobility": (r"\blogistics\b", r"\bdelivery\b", r"\bmobility\b", r"\bfleet\b", r"\bride[\s\-]?hail"),
    "Media / Social": (r"\bmedia\b", r"\bsocial network\b", r"\bstreaming\b", r"\bcontent platform\b"),
    "Agency / Consultancy": (r"\bagency\b", r"\bconsultanc(?:y|ies)\b", r"\bsoftware house\b",
                             r"\bdev shop\b", r"\bstudio\b", r"\boutsourc"),
    "Crypto / Web3": (r"\bweb3\b", r"\bcrypto\b", r"\bblockchain\b", r"\bdefi\b", r"\bnft\b"),
    "SaaS / B2B": (r"\bsaas\b", r"\bb2b platform\b", r"\benterprise software\b"),
}

STARTUP_STAGE_PATTERNS = {
    "Pre-seed": (r"pre[\s\-]?seed",),
    "Seed": (r"\bseed[\s\-]?(?:stage|funded|round)\b", r"\bseed\b"),
    "Series A": (r"series[\s\-]?a\b",),
    "Series B": (r"series[\s\-]?b\b",),
    "Series C+": (r"series[\s\-]?[cdef]\b", r"late[\s\-]?stage"),
    "Bootstrapped": (r"\bbootstrapped\b", r"\bprofitable\b", r"\bself[\s\-]?funded\b"),
    "Early Stage": (r"early[\s\-]?stage", r"\bfounding (?:engineer|team)\b", r"\bfounder\b", r"\bstealth\b"),
}
STARTUP_SIGNALS = (
    "startup", "start-up", "scale-up", "scaleup", "yc ", "y combinator",
    "founding engineer", "early stage", "seed", "series a", "series b",
    "pre-seed", "venture backed", "vc-backed", "stealth",
)

PARTNERSHIP_SIGNALS = (
    "partner", "partnership", "subcontract", "sub-contract", "white label",
    "white-label", "outsourcing partner", "development partner", "agency partner",
    "vendor", "supplier", "rfp", "request for proposal", "statement of work",
    "retainer", "dedicated team", "staff augmentation", "nearshore", "offshore",
    "looking for an agency", "looking for a studio", "software house",
)


ENABLED_SOURCES = tuple(
    s.strip() for s in os.getenv("JOB_AGENT_SOURCES", "").split(",") if s.strip()
)

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID", "")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY", "")
REED_API_KEY = os.getenv("REED_API_KEY", "")
JOOBLE_API_KEY = os.getenv("JOOBLE_API_KEY", "")
CAREERJET_API_KEY = os.getenv("CAREERJET_API_KEY", "")
INDEED_PUBLISHER_ID = os.getenv("INDEED_PUBLISHER_ID", "")
ZIPRECRUITER_API_KEY = os.getenv("ZIPRECRUITER_API_KEY", "")
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
SEARCHAPI_KEY = os.getenv("SEARCHAPI_KEY", "")

#: Google Jobs pages to read per query. Each page is one billed SerpApi
#: search, so this is the dial between coverage and allowance.
GOOGLE_JOBS_PAGES = int(os.getenv("GOOGLE_JOBS_PAGES", "3"))

CAREERJET_REFERER = os.getenv("CAREERJET_REFERER", "http://localhost/job-agent")


ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

LLM_ENABLED = os.getenv("LLM_ENABLED", "1") not in ("0", "false", "False", "")

LLM_MODEL = os.getenv("LLM_MODEL", "claude-opus-5")

LLM_EFFORT_ELIGIBILITY = os.getenv("LLM_EFFORT_ELIGIBILITY", "low")
LLM_EFFORT_FIT = os.getenv("LLM_EFFORT_FIT", "medium")

LLM_MAX_ELIGIBILITY_CALLS = int(os.getenv("LLM_MAX_ELIGIBILITY_CALLS", "120"))
LLM_MAX_FIT_CALLS = int(os.getenv("LLM_MAX_FIT_CALLS", "60"))

LLM_MAX_SPEND_USD = float(os.getenv("LLM_MAX_SPEND_USD", "3.00"))

LLM_CACHE_DAYS = float(os.getenv("LLM_CACHE_DAYS", "21"))

LLM_CONCURRENCY = int(os.getenv("LLM_CONCURRENCY", "4"))

LLM_MAX_ADVERT_CHARS = int(os.getenv("LLM_MAX_ADVERT_CHARS", "6000"))

LLM_PROMOTED_SCORE_CAP = 84

#: Below this reply chance a lead moves to the Long Shots sheet rather than
#: the main report. Set where the model's own "long shot" and "worth applying"
#: bands divide: across 81 cached verdicts the former topped out at 33 and the
#: latter began at 28. Going higher costs strong CV matches that crowding or
#: seniority pulled down — a floor of 40 set aside five leads scoring 78+ on fit.
LLM_MIN_CHANCE_WITH_CV = int(os.getenv("LLM_MIN_CHANCE_WITH_CV", "30"))

#: How many leads a run keeps before the chance floor is allowed to set any
#: aside. Without this the floor can empty a thin search of the very adverts
#: it qualified for. Set to 0 to apply the floor literally.
LLM_CHANCE_FLOOR_MIN_KEPT = int(os.getenv("LLM_CHANCE_FLOOR_MIN_KEPT", "10"))

LLM_FIT_WEIGHT = float(os.getenv("LLM_FIT_WEIGHT", "0.4"))


QUICK_LINKEDIN_MAX_DETAILS = int(os.getenv("QUICK_LINKEDIN_MAX_DETAILS", "60"))
QUICK_DESCRIPTION_MAX_FETCHES = int(os.getenv("QUICK_DESCRIPTION_MAX_FETCHES", "60"))
QUICK_LINKEDIN_SEARCH_WORKERS = int(os.getenv("QUICK_LINKEDIN_SEARCH_WORKERS", "2"))

QUICK_LLM_MAX_ELIGIBILITY_CALLS = int(os.getenv("QUICK_LLM_MAX_ELIGIBILITY_CALLS", "30"))
QUICK_LLM_MAX_FIT_CALLS = int(os.getenv("QUICK_LLM_MAX_FIT_CALLS", "25"))
QUICK_LLM_CONCURRENCY = int(os.getenv("QUICK_LLM_CONCURRENCY", "8"))


_QUICK_FIELDS = (
    ("LINKEDIN_MAX_DETAILS", "QUICK_LINKEDIN_MAX_DETAILS"),
    ("DESCRIPTION_MAX_FETCHES", "QUICK_DESCRIPTION_MAX_FETCHES"),
    ("LINKEDIN_SEARCH_WORKERS", "QUICK_LINKEDIN_SEARCH_WORKERS"),
    ("LLM_MAX_ELIGIBILITY_CALLS", "QUICK_LLM_MAX_ELIGIBILITY_CALLS"),
    ("LLM_MAX_FIT_CALLS", "QUICK_LLM_MAX_FIT_CALLS"),
    ("LLM_CONCURRENCY", "QUICK_LLM_CONCURRENCY"),
)


def restore_budgets(saved: dict) -> None:
    """Put back budgets captured by `use_quick_mode`."""
    for name, value in saved.items():
        globals()[name] = value


#: What each tier switches on. "normal" is the default and is what the
#: unmodified constants above describe.
TIERS = {
    "quick": {"GOOGLE_JOBS_PAGES": 1, "DISCOVER_EMPLOYER_BOARDS": False, "DISCOVER_PLATFORMS": False,
              "FETCH_EMPLOYER_CONTACTS": False, "CHECK_COMPANY_SIZE_ONLINE": False},
    "normal": {"GOOGLE_JOBS_PAGES": 3, "DISCOVER_EMPLOYER_BOARDS": True, "DISCOVER_PLATFORMS": False,
               "FETCH_EMPLOYER_CONTACTS": False, "CHECK_COMPANY_SIZE_ONLINE": False},
    "deep": {"GOOGLE_JOBS_PAGES": 8, "DISCOVER_EMPLOYER_BOARDS": True, "DISCOVER_PLATFORMS": True,
             "FETCH_EMPLOYER_CONTACTS": True, "CHECK_COMPANY_SIZE_ONLINE": True},
}


def use_tier(name: str) -> dict:
    """Switch the optional, request-hungry stages on or off.

    The quick tier also shrinks the per-board fetch budgets, so one choice
    covers both how many boards are asked and how deeply each is read.

    Returns the previous values so a caller can put them back — these are
    module globals, and a test that changed them must not leak into the next.
    """
    settings = TIERS.get(name)
    if not settings:
        return {}
    saved = {key: globals()[key] for key in settings}
    globals().update(settings)
    if name == "quick":
        saved.update(use_quick_mode())
    return saved


def restore(saved: dict) -> None:
    globals().update(saved)


@contextmanager
def tier(name: str):
    """Run a block at one tier, then put the settings back.

    These are module globals. A run that changed them and walked away would
    leave the next caller in the process — the next MCP call, the next test —
    running at a tier nobody chose.
    """
    saved = use_tier(name)
    try:
        yield
    finally:
        restore(saved)


def use_quick_mode() -> dict:
    """Shrink the fetch budgets for an interactive run."""
    saved = {name: globals()[name] for name, _ in _QUICK_FIELDS}
    for name, quick_name in _QUICK_FIELDS:
        globals()[name] = globals()[quick_name]
    return saved


PROFILE_MAX_CV_CHARS = int(os.getenv("PROFILE_MAX_CV_CHARS", "12000"))

LLM_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-fable-5": (10.0, 50.0),
}


def ensure_dirs() -> None:
    for path in (REPORTS_DIR, DATA_DIR, CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)
