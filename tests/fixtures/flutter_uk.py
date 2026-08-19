"""The hand-tuned Flutter/UK search this project began as, kept as test data."""

from __future__ import annotations

from job_agent.profile import SearchProfile


CANDIDATE = {
    "location": "United Kingdom",
    "timezone": "Europe/London",
    "headline": "Senior Flutter / Mobile Engineer",
    "years_mobile": 7,
    "years_flutter": 5,
    "work_mode": "Remote only",
}

PROFILE_SKILLS: dict[str, float] = {
    "flutter": 10.0,
    "dart": 6.0,
    "bloc": 4.0,
    "cubit": 3.0,
    "clean architecture": 4.0,
    "riverpod": 3.0,
    "getx": 2.5,
    "provider": 1.0,
    "state management": 2.0,
    "modular architecture": 2.0,
    "dependency injection": 1.5,
    "streams": 1.5,
    "isolates": 1.5,

    "backend for frontend": 5.0,
    "backend-for-frontend": 5.0,
    "bff": 4.0,
    "server-driven ui": 5.0,
    "server driven ui": 5.0,
    "api contract": 2.0,
    "serverless": 2.5,

    "firebase": 3.5,
    "firestore": 3.0,
    "cloud functions": 2.5,
    "fcm": 1.5,
    "cloud messaging": 1.5,
    "crashlytics": 1.5,
    "remote config": 1.5,
    "security rules": 1.5,
    "supabase": 1.5,

    "psd2": 4.0,
    "sca": 3.0,
    "strong customer authentication": 3.0,
    "kyc": 2.5,
    "oauth": 2.5,
    "jwt": 2.0,
    "certificate pinning": 2.5,
    "tls": 1.5,
    "authentication": 2.0,
    "security": 2.0,
    "compliance": 1.5,

    "unit test": 2.5,
    "widget test": 2.5,
    "integration test": 2.5,
    "tdd": 2.0,
    "mockito": 1.5,
    "test coverage": 2.0,
    "testing": 1.0,

    "ci/cd": 2.5,
    "cicd": 2.0,
    "codemagic": 3.0,
    "github actions": 2.5,
    "fastlane": 2.5,
    "gitlab": 1.5,

    "offline-first": 2.5,
    "offline first": 2.5,
    "drift": 2.0,
    "sqlite": 2.0,
    "mongodb": 1.0,
    "rest api": 2.0,
    "restful": 1.5,

    "android": 3.0,
    "ios": 2.5,
    "kotlin": 2.0,
    "swift": 1.5,
    "java": 1.0,
    "platform channel": 2.0,
    "cross-platform": 1.5,
    "cross platform": 1.5,

    "accessibility": 2.5,
    "wcag": 3.0,
    "a11y": 2.0,
    "mentor": 2.0,
    "code review": 1.5,
    "technical ownership": 2.0,
    "agile": 0.5,
    "scrum": 0.5,

    "langchain": 2.5,
    "langgraph": 2.5,
    "ai assistant": 1.5,
    "llm": 1.0,

    "graphql": 1.0,
    "dio": 0.5,
    "deep link": 1.0,
    "app store": 1.0,
    "play store": 1.0,
    "azure": 1.5,
}

DOMAIN_KEYWORDS = {
    "fintech": 5.0,
    "banking": 5.0,
    "challenger bank": 5.0,
    "neobank": 4.5,
    "payments": 4.0,
    "financial services": 4.0,
    "open banking": 4.5,
    "regulated": 2.5,
    "transactions": 2.0,
    "wallet": 2.5,
    "trading": 2.0,
    "insurtech": 1.5,
    "crypto": 1.0,
    "point of sale": 2.5,
    "pos": 1.5,
    "retail": 1.5,
    "navigation": 1.0,
}

COMPETING_STACKS = {
    "react native": 8.0,
    "kotlin multiplatform": 5.0,
    "kmp": 3.0,
    "xamarin": 6.0,
    "ionic": 5.0,
    "cordova": 5.0,
    "maui": 5.0,
    "unity": 6.0,
    "nativescript": 5.0,
}

HARD_TITLE_EXCLUSIONS = (
    "recruiter", "sales", "account executive", "account manager", "business development",
    "marketing", "designer", "ui/ux designer", "data scientist", "data engineer",
    "devops engineer", "sre", "site reliability", "salesforce", "sap",
    "customer support", "customer success", "project manager", "scrum master",
    "product owner", "qa manager", "teacher", "tutor", "intern", "internship",
    "unpaid", "volunteer",
)


PRIMARY_RELEVANCE = ("flutter", "dart")
SECONDARY_RELEVANCE = (
    "mobile developer", "mobile engineer", "mobile application", "app developer",
    "android developer", "ios developer", "cross-platform", "cross platform",
    "mobile app", "mobile software",
    "android", "ios", "app", "apps",
)

ENGINEERING_TITLE_TOKENS = (
    "developer", "engineer", "engineering", "programmer", "programming",
    "architect", "dev", "devs", "swe", "coder", "software", "development",
    "technologist", "consultant", "contractor", "freelancer", "specialist",
    "tech lead", "cto", "app builder",
)

OTHER_DISCIPLINE_TITLE = (
    r"\bpython\b", r"\bgolang\b", r"\bgo engineer\b", r"\brust\b", r"\bruby\b",
    r"\bphp\b", r"\bscala\b", r"\bclojure\b", r"\berlang\b", r"\belixir\b",
    r"\.net\b", r"\bc\+\+\b", r"\bc#\b", r"\bjava (?:developer|engineer)\b",
    r"\bubuntu\b", r"\blinux\b", r"\bkernel\b", r"\bdevops\b", r"\bsre\b",
    r"\binfrastructure\b", r"\bkubernetes\b", r"\bcloud\b", r"\bnetwork\b",
    r"\bdatabase\b", r"\bdata (?:engineer|scientist|analyst)\b", r"\bml\b",
    r"\bmachine learning\b", r"\bai engineer\b", r"\bsecurity\b", r"\bqa\b",
    r"\btest engineer\b", r"\bback[\s\-]?end\b", r"\bserver[\s\-]?side\b",
    r"\bembedded\b", r"\bfirmware\b", r"\bhardware\b", r"\bsolutions architect\b",
)

MIN_BODY_FLUTTER_MENTIONS = 4

SEARCH_QUERIES = (
    "flutter",
    "flutter developer",
    "dart",
    "mobile developer",
    "mobile engineer",
    "android developer",
)


UK_TERMS = (
    "uk", "u.k.", "united kingdom", "great britain", "britain", "gb", "gbr",
    "england", "scotland", "wales", "northern ireland",
)

UK_CITY_TERMS = (
    "london", "manchester", "birmingham", "leeds", "glasgow", "edinburgh",
    "bristol", "cardiff", "belfast", "liverpool", "sheffield", "newcastle",
    "nottingham", "cambridge", "oxford", "reading", "brighton",
)

EUROPE_TERMS = ("europe", "emea", "european")

EU_ONLY_TERMS = ("eu only", "eu-only", "european union only", "eea only")


def flutter_uk_profile() -> SearchProfile:
    """The original search, as a SearchProfile."""
    return SearchProfile(
        key="flutter-uk",
        label="Flutter / mobile",
        query="flutter",
        core_terms=PRIMARY_RELEVANCE,
        secondary_terms=SECONDARY_RELEVANCE,
        hands_on_title_tokens=ENGINEERING_TITLE_TOKENS,
        hard_title_exclusions=HARD_TITLE_EXCLUSIONS,
        title_exclusion_regexes=OTHER_DISCIPLINE_TITLE,
        competing_stacks=dict(COMPETING_STACKS),
        min_body_core_mentions=MIN_BODY_FLUTTER_MENTIONS,
        skills=dict(PROFILE_SKILLS),
        domain_keywords=dict(DOMAIN_KEYWORDS),
        candidate_brief="Senior Flutter / mobile engineer based in the United Kingdom.",
        seniority="Senior",
        years_experience=CANDIDATE["years_mobile"],
        has_cv=False,
        home_country="United Kingdom",
        home_terms=UK_TERMS,
        home_city_terms=UK_CITY_TERMS,
        region_terms=EUROPE_TERMS,
        region_excluding_home_terms=EU_ONLY_TERMS,
        timezone="Europe/London",
        search_queries=SEARCH_QUERIES,
        work_arrangement="remote",
    )
