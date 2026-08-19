"""What the search is looking for, and who it is looking for it on behalf of."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Any

from . import config, region as region_mod

log = logging.getLogger("job_agent.profile")

PROFILE_VERSION = "v5"

_STOPWORDS = frozenset({
    "find", "me", "job", "jobs", "role", "roles", "work", "position", "remote",
    "developer", "engineer", "looking", "for", "the", "and", "with", "any",
    "please", "want", "need", "get", "some", "good", "best", "new",
})


@dataclass(frozen=True)
class SearchProfile:
    """One search: what to look for, for whom, and where they may work."""

    key: str = "builtin"
    label: str = "job"
    query: str = ""

    core_terms: tuple[str, ...] = ()
    secondary_terms: tuple[str, ...] = ()
    title_exclusion_regexes: tuple[str, ...] = ()
    hands_on_title_tokens: tuple[str, ...] = ()
    hard_title_exclusions: tuple[str, ...] = ()
    other_discipline_terms: tuple[str, ...] = ()
    competing_stacks: dict[str, float] = field(default_factory=dict)
    min_body_core_mentions: int = 4
    #: True only when the request asks for small employers specifically.
    small_employers_only: bool = False
    employment_types: tuple[str, ...] = ()
    startups_only: bool = False
    #: "any" (remote, hybrid and on-site), or one of "remote", "hybrid", "onsite".
    work_arrangement: str = "any"

    skills: dict[str, float] = field(default_factory=dict)
    domain_keywords: dict[str, float] = field(default_factory=dict)
    candidate_brief: str = ""
    seniority: str = "Unspecified"
    years_experience: int = 0
    has_cv: bool = False

    home_country: str = ""
    home_terms: tuple[str, ...] = ()
    home_city_terms: tuple[str, ...] = ()
    target_regions: tuple[str, ...] = ()
    region_detected: bool = False
    region_source: str = ""
    region_terms: tuple[str, ...] = ()
    region_excluding_home_terms: tuple[str, ...] = ()
    timezone: str = ""

    search_queries: tuple[str, ...] = ()
    salary_floor_usd: float = 50_000.0
    pay_floor_stated: bool = False


    def title_exclusion_patterns(self) -> tuple[str, ...]:
        """`other_discipline_terms` as anchored regexes, plus any hand-written ones."""
        return (tuple(_as_word_pattern(term) for term in self.other_discipline_terms)
                + self.title_exclusion_regexes)

    @property
    def remote_only(self) -> bool:
        return self.work_arrangement == "remote"

    def is_core(self, term: str) -> bool:
        return term.lower() in {t.lower() for t in self.core_terms}

    def fingerprint(self) -> str:
        """Stable id for caching a compiled profile."""
        payload = json.dumps({
            "v": PROFILE_VERSION, "query": self.query,
            "core": sorted(self.core_terms), "home": self.home_country,
            "cv": self.has_cv,
        }, sort_keys=True)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _as_word_pattern(term: str) -> str:
    """Anchor a plain term as a whole-word regex, safely."""
    escaped = re.escape(term.strip().lower())
    left = "" if term.strip()[:1] in ".+#" else r"\b"
    right = "" if term.strip()[-1:] in ".+#" else r"\b"
    return f"{left}{escaped}{right}"


def _builtin_profile() -> SearchProfile:
    """The profile a run starts from when nothing has been compiled yet."""
    local = _local_candidate()
    search = local.get("default_search") or {}
    if not isinstance(search, dict):
        search = {}

    stated = str(local.get("home_country") or "").strip()
    home = region_mod.build(stated) if stated else region_mod.Region("", ())

    core = _terms(search.get("core_terms"), 8)
    return SearchProfile(
        key="builtin",
        label=str(search.get("label") or "job"),
        query=str(search.get("query") or ""),
        core_terms=core,
        secondary_terms=_terms(search.get("secondary_terms"), 40),
        hands_on_title_tokens=_terms(search.get("hands_on_title_tokens"), 30),
        search_queries=_terms(search.get("search_queries"), 12),
        work_arrangement=_arrangement(search.get("work_arrangement")),
        candidate_brief=str(local.get("brief") or ""),
        seniority=str(local.get("seniority") or "Unspecified"),
        years_experience=int(local.get("years_experience") or 0),
        has_cv=bool(local.get("brief")),
        home_country=home.country,
        home_terms=home.terms,
        home_city_terms=home.cities,
        timezone=str(local.get("timezone") or ""),
    )


#: Stands in for `candidate_brief` when no CV and no `candidate.local.json`
#: describe who is searching. It deliberately describes nobody — inventing a
#: persona here would answer a question the run was never asked.
DEFAULT_CANDIDATE_BRIEF = (
    "No details about the candidate were supplied. Judge the advert against the "
    "search request alone, and treat their background as unknown rather than "
    "assuming one."
)

LOCAL_CANDIDATE_FILE = config.ROOT / "candidate.local.json"


def _local_candidate() -> dict:
    """Read `candidate.local.json` if present. A broken file is ignored, not fatal."""
    try:
        with open(LOCAL_CANDIDATE_FILE, encoding="utf-8-sig") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


_active: SearchProfile | None = None
_lock = threading.Lock()


def active() -> SearchProfile:
    """The profile the pipeline is currently running under."""
    global _active
    with _lock:
        if _active is None:
            _active = _builtin_profile()
        return _active


def set_active(profile: SearchProfile) -> None:
    global _active
    with _lock:
        _active = profile


def reset() -> None:
    global _active
    with _lock:
        _active = None


@contextmanager
def using(profile: SearchProfile):
    """Run a block under `profile`, restoring whatever was active before."""
    global _active
    with _lock:
        previous = _active
        _active = profile
    try:
        yield profile
    finally:
        with _lock:
            _active = previous


def title_exclusion_patterns() -> tuple[str, ...]:
    """Discipline-exclusion regexes for the active profile."""
    return active().title_exclusion_patterns()


PROFILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "label": {"type": "string"},
        "is_job_search": {"type": "boolean"},
        "answer": {"type": "string"},
        "core_terms": {"type": "array", "items": {"type": "string"}},
        "secondary_terms": {"type": "array", "items": {"type": "string"}},
        "hands_on_title_tokens": {"type": "array", "items": {"type": "string"}},
        "hard_title_exclusions": {"type": "array", "items": {"type": "string"}},
        "other_discipline_terms": {"type": "array", "items": {"type": "string"}},
        "competing_stacks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "weight": {"type": "number"},
                },
                "required": ["term", "weight"],
                "additionalProperties": False,
            },
        },
        "skills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "weight": {"type": "number"},
                },
                "required": ["term", "weight"],
                "additionalProperties": False,
            },
        },
        "domain_keywords": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "term": {"type": "string"},
                    "weight": {"type": "number"},
                },
                "required": ["term", "weight"],
                "additionalProperties": False,
            },
        },
        "search_queries": {"type": "array", "items": {"type": "string"}},
        "min_body_core_mentions": {"type": "integer"},
        "small_employers_only": {"type": "boolean"},
        "employment_types": {
            "type": "array",
            "items": {"type": "string",
                      "enum": ["Full Time", "Part Time", "Contract", "Freelance"]},
        },
        "startups_only": {"type": "boolean"},
        "work_arrangement": {"type": "string",
                             "enum": ["any", "remote", "hybrid", "onsite"]},
        "candidate_brief": {"type": "string"},
        "seniority": {
            "type": "string",
            "enum": ["Junior", "Mid", "Senior", "Lead", "Unspecified"],
        },
        "years_experience": {"type": "integer"},
        "needs_clarification": {"type": "boolean"},
        "questions": {"type": "array", "items": {"type": "string"}},
        "target_regions": {"type": "array", "items": {"type": "string"}},
        "home_country": {"type": "string"},
        "home_terms": {"type": "array", "items": {"type": "string"}},
        "home_city_terms": {"type": "array", "items": {"type": "string"}},
        "region_terms": {"type": "array", "items": {"type": "string"}},
        "region_excluding_home_terms": {"type": "array", "items": {"type": "string"}},
        "timezone": {"type": "string"},
        "salary_floor_usd": {"type": "number"},
        "pay_floor_stated": {"type": "boolean"},
    },
    "required": [
        "label", "is_job_search", "answer", "core_terms", "secondary_terms",
        "hands_on_title_tokens",
        "hard_title_exclusions", "other_discipline_terms", "competing_stacks",
        "skills", "domain_keywords", "search_queries", "min_body_core_mentions",
        "small_employers_only", "employment_types", "startups_only",
        "work_arrangement",
        "candidate_brief", "seniority", "years_experience",
        "needs_clarification", "questions", "target_regions", "home_country",
        "home_terms", "home_city_terms", "region_terms",
        "region_excluding_home_terms", "timezone", "salary_floor_usd",
        "pay_floor_stated",
    ],
    "additionalProperties": False,
}

SYSTEM_COMPILE = """\
You configure a job-search engine. You are given what someone is looking for —
a role, a technology, a sentence, sometimes a whole CV — and you return the
vocabulary the engine filters and scores with.

You are not searching. You are writing the dictionary the search will use. The
engine is deterministic: it matches your words against job adverts as whole
words and phrases, case-insensitively, and it can only ever be as good as the
words you give it.

FIRST, decide what kind of request this is.

If it names a role, a trade, a technology, a discipline or a career direction —
"electrician", "plumbing work in Leeds", "iOS", "AI engineer", "care assistant",
"HGV driving", "something with Python and remote", a pasted CV — set
`is_job_search` to true and fill in the vocabulary. Leave `answer` empty.

If it is not a job search at all — a general question, a greeting, a request the
engine cannot serve by looking for job adverts — set `is_job_search` to false,
answer the person's question directly and helpfully in `answer`, and return
empty lists and zeros for everything else. Do not invent a search nobody asked
for.

FOR A JOB SEARCH, fill in each field as follows.

`core_terms` — the two to six words that *are* this role. A job whose title
    names one of these is unambiguously the right kind of job. For plumbing that
    is ["plumbing", "plumber", "pipefitting"]; for nursing ["nurse", "nursing"];
    for iOS ["ios", "swift", "swiftui"]; for AI engineering ["machine learning",
    "llm", "pytorch"]. Any trade, not only technology. Precise, not aspirational
    — a word that appears in every advert in the industry belongs nowhere near
    this list.

    **Include the local language when the market advertises in it.** A search in
    Germany should carry "elektriker" alongside "electrician"; in Finland
    "sähköasentaja"; in France "électricien". Adverts written in the local
    language are invisible to an English-only vocabulary, and in most of the
    world they are the majority. Where the market advertises in English — the
    Gulf, India, Pakistan, Nigeria — English alone is right.

`secondary_terms` — 6 to 15 broader phrases describing the same kind of work,
    used when the core terms are absent from the title. Multi-word where you can
    ("heating engineer", "gas installer", "mobile engineer"), because single
    common words generate false matches.

`hard_title_exclusions` — titles that are never *this* job however the advert is
    worded. Usually recruiters, sales, marketing, support and management-only
    roles, plus the discipline's own near-misses.

    **Never exclude the thing they asked for.** This list is relative to the
    request, not a fixed list of undesirable work. Someone looking for an
    internship, a graduate scheme, an apprenticeship, a contract, a part-time
    role or a junior position wants exactly those adverts — excluding them
    returns an empty report, which reads as the tool being broken. Check every
    entry against the request before you include it.

`other_discipline_terms` — trades and disciplines whose presence in a *title*
    means the advert belongs to someone else. Employers list every skill they
    use in the boilerplate, so this list is what stops a "Python Engineer"
    advert becoming a Flutter lead, or a "Groundworker" advert becoming a
    plumbing one. Never include a core term here.
    Plain words and short phrases only — no regular expressions, no wildcards,
    no punctuation tricks. The engine anchors them itself.

`competing_stacks` — trades or technologies that solve the same problem a
    different way, with a penalty weight from 3 to 8 (higher = more strongly a
    different job). React Native competes with Flutter; carpentry competes with
    joinery. Leave empty when the trade has no such rival.

`skills` — 15 to 40 skills this candidate can claim, each weighted 1 to 10 by
    how central it is. With a CV, take them from the CV and weight by evidence:
    something they shipped for years scores near 10, something listed once near
    2. Without a CV, describe the skills a strong candidate for this role would
    have, weighted by how much the market asks for them.

`domain_keywords` — 5 to 15 industries or problem domains worth extra credit,
    weighted 1 to 5. From a CV, the industries they have actually worked in.

`search_queries` — 4 to 8 short phrases to send to job boards. These are search
    box queries, not filters: they should be the phrases employers put in job
    titles. Short and common beats clever and precise.

`employment_types` — how they want to be engaged, when they say. Fill it only
    from an explicit signal in the request or the CV: "part time" gives
    ["Part Time"], "contract or freelance" gives both, "outside IR35" means
    ["Contract"], "permanent role" means ["Full Time"]. Leave it **empty** when
    nothing was said — an empty list means every kind of engagement is welcome,
    and guessing one here silently discards most of the market.

`work_arrangement` — one of "any", "remote", "hybrid", "onsite".

    **"any" by default**, which returns remote, hybrid and on-site work
    together. Do not infer an arrangement from the kind of work: a software
    engineer who did not mention remote wants to see office roles too, and
    deciding otherwise hides most of their market from them.

    Set a specific value **only on an explicit signal** in the request or CV:

      "remote", "work from home", "fully distributed", "wfh"  -> "remote"
      "hybrid", "two days in the office", "part remote"       -> "hybrid"
      "onsite", "on site", "in the office", "office based"    -> "onsite"

    A named place to work ("plumbing jobs in Manchester") is **not** a signal
    for "onsite" on its own — they may be happy with remote work based there
    too. Leave it "any" unless the arrangement itself was stated.

    Narrowing this without being asked is the expensive mistake: it silently
    discards most adverts and the person never learns what they missed.

`startups_only` — true only when the request asks for small companies in so
    many words ("startups only", "early-stage", "no big corporates").

`small_employers_only` — **false unless asked.** Employers of every size are
    searched, because a job at a 5,000-person company is still a job and hiding
    it means they never learn it existed. Smaller employers already rank higher,
    which carries the preference without discarding anything.

    Set this **true** only when the request asks for small companies in so many
    words — "startups only", "early-stage", "no big corporates", "small firms".

`hands_on_title_tokens` — words that mark a job title as the actual work rather
    than a job *about* the work. They stop "Publisher Partnerships, Mobile App"
    and "Recruiter — Plumbing" riding in on a keyword.

    Write them for the trade in hand: for software, "developer", "engineer",
    "programmer"; for plumbing, "plumber", "fitter", "installer", "engineer";
    for care work, "nurse", "carer", "assistant", "practitioner". Include the
    common title nouns someone doing this job would actually have, in the local
    language too where the market advertises in it. Leave the list **empty** if
    you cannot name them confidently — an empty list disables the check, which
    is far better than a list that rejects the real titles.

`min_body_core_mentions` — how many times a core term must appear in an advert's
    body before a posting that doesn't name it in the title counts as this role.
    3 or 4 for a specific technology, 5 or 6 for a common word that appears in
    stack lists everywhere.

`candidate_brief` — one paragraph, written for another model to read, describing
    who this search is for: location and work rights, years of experience, what
    they have actually built, and their hard constraints. From a CV, ground every
    claim in the CV; invent nothing, and if the CV does not state something, do
    not supply it. Without a CV, describe the profile the search implies and say
    plainly that no CV was provided.

`target_regions`, `home_country`, `home_terms`, `home_city_terms` — where this
    person can work, and every way an advert might name those places.

    `target_regions` is where they want to work. Fill it when the request names
    places: "find me jobs in the USA, UK and Australia" gives
    ["united states", "united kingdom", "australia"]. Leave it empty when the
    request names none.

    `home_country` is where they physically live and will work from. Work it out
    in this order:
      1. A region stated in the request wins ("remote in Germany", "US-based").
      2. Otherwise read it off the CV — the address, the phone country code, the
         locations of recent employers, the right-to-work they mention. Recent
         and explicit beats old and inferred.
      3. If neither tells you, leave it empty. Do not guess, and do not fall
         back to a popular country.

    `home_terms` is the match list the filters actually run on, so put in it
    every naming variant for **every** region in `target_regions` — or for
    `home_country` when no targets were named. For the United States that is
    "united states", "usa", "us", "u.s."; for the United Kingdom "united
    kingdom", "uk", "england", "scotland", "wales", "britain". Include the main
    cities in `home_city_terms` the same way.

    Judge all of this on where someone will *work from*, never on nationality or
    where they were born. A CV mentioning origin, citizenship or a birthplace
    tells you nothing about these fields.

WHEN TO ASK INSTEAD OF GUESSING

Set `needs_clarification` to true and put one to three short questions in
`questions` when an answer would materially change which jobs are worth
returning, and you cannot get it from the request or the CV. Return the
vocabulary you have alongside them — the questions refine a search, they do not
replace one.

Ask when:

- **They named a country they may not be able to work in.** "Find me jobs in the
  USA" from someone whose CV shows they live and work in Europe raises a real
  question: do they hold US work authorisation, or are they looking for a
  company that hires internationally? The honest question is *"Do you have the
  right to work in the US, or should I look for employers that hire contractors
  internationally?"* — that single answer changes almost every result.
- **No region is determinable at all** — nothing in the request, nothing in the
  CV. Ask where they will be working from.
- **The role is genuinely ambiguous** in a way that splits the search in two —
  "developer" with no CV and no other signal.

Do not ask when:

- The answer would not change the results.
- You are merely unsure — prefer a sensible vocabulary you can state plainly.
- The request is already specific enough to act on. A person who says "remote
  iOS jobs" and provides a CV showing they live in Spain has told you enough.

Never ask about nationality, origin, age, or anything else you do not need to
match jobs. Ask about the right to work in a *place*, never about citizenship
as an identity.

`pay_floor_stated` — true whenever **they wrote a number**, however they hedged
    it. "from 20k usd", "at least £600 a day", "paying 90k+", "around 50k or
    above", "roughly 80k", "~£70,000", "no less than 60k" are all stated
    figures: the hedge describes their flexibility, not their certainty, and
    they still gave you the number. False only when *you* supplied the figure
    because they mentioned no pay at all. The difference matters: a figure they
    stated is a requirement, so adverts that publish no pay cannot be shown to
    meet it and are set aside for them to check; a figure you estimated is a
    hint, and hiding every unpublished advert on it would empty the report.

`salary_floor_usd` — a sensible annual floor in USD for this role and seniority,
    below which an advert is not worth the candidate's time. **Use 0** when you
    have no basis for a figure, and whenever pay is not the point: internships,
    graduate schemes, apprenticeships, volunteer and open-source work, or any
    request that does not imply a salary expectation. A floor inherited from a
    senior role would silently drop every advert an intern wants.

Two standing rules:

1. Judge eligibility by where someone lives and what they are entitled to do,
   never by nationality or where they are from. If a CV mentions origin, it is
   irrelevant to every field here.
2. Prefer precision over reach. A vocabulary that returns 40 real matches is
   worth more than one that returns 400 postings someone has to read through.\
"""


@dataclass
class Compiled:
    """The result of reading a request: a search, an answer, or a question."""

    profile: SearchProfile | None = None
    answer: str = ""
    questions: tuple[str, ...] = ()
    #: True when no search could be built at all. Answering a question that was
    #: never a job search is not a failure; producing no report is.
    failed: bool = False

    def __iter__(self):
        """Unpack as `(profile, answer)` — the shape callers used before."""
        return iter((self.profile, self.answer))


def _fetch_payload(request: str, cv_text: str, region: str, budget: Any) -> dict | str:
    """The model's raw answer for this request — from cache, or from the API."""
    from . import cache, llm

    key_material = json.dumps(
        [PROFILE_VERSION, config.LLM_MODEL, request, region,
         hashlib.sha1(cv_text.encode("utf-8")).hexdigest() if cv_text else ""],
        sort_keys=True,
    )
    cache_key = "profile:" + hashlib.sha1(key_material.encode("utf-8")).hexdigest()[:20]

    payload = cache.get(cache_key, config.LLM_CACHE_DAYS)
    if payload is not None:
        return payload

    ok, why = llm.available()
    if not ok:
        return (f"A custom search needs the Claude judgement layer, which is not "
                f"available: {why}. The built-in search still works.")

    payload = llm.ask(SYSTEM_COMPILE, PROFILE_SCHEMA,
                       _compile_prompt(request, cv_text, region), "medium",
                       budget or llm.Budget(config.LLM_MAX_SPEND_USD))
    if payload is None:
        return "Could not work out what to search for. Try naming a role or a technology."

    cache.put(cache_key, payload)
    return payload


def _apply_region(compiled: SearchProfile, stated: str,
                  questions: tuple[str, ...]) -> tuple[SearchProfile, tuple[str, ...]]:
    """Settle where this person works, and prune questions already answered."""
    named = region_mod.parse_list(stated)
    if named:
        compiled = replace(
            compiled,
            target_regions=tuple(r.country for r in named),
            home_country=named[0].country,
            home_terms=tuple(dict.fromkeys(t for r in named for t in r.terms)),
            home_city_terms=tuple(dict.fromkeys(c for r in named for c in r.cities)),
            region_detected=False,
            region_source="stated in the request",
        )
        return compiled, tuple(q for q in questions if not _asks_about_location(q))

    if compiled.home_country or compiled.target_regions:
        return compiled, tuple(q for q in questions if not _asks_about_location(q))

    detected = region_mod.detect()
    if not detected:
        return compiled, questions

    compiled = replace(
        compiled,
        home_country=detected.country,
        home_terms=detected.terms,
        home_city_terms=detected.cities,
        timezone=compiled.timezone or detected.timezone,
        region_detected=True,
        region_source=detected.source,
    )
    note = (f"I am assuming you are working from {detected.country}, based on "
            f"{detected.source}. If that is wrong, say where you are — it decides "
            f"which roles you can actually take.")
    return compiled, (note,) + tuple(q for q in questions if not _asks_about_location(q))


def compile_profile(request: str, cv_text: str = "", *,
                    region: str = "", budget: Any = None) -> Compiled:
    """Turn a free-text request (and optional CV) into a search profile."""
    request = (request or "").strip()
    if not request and not cv_text:
        return Compiled(answer="Tell me what kind of work you are looking for, "
                               "or share a CV and I will work it out.", failed=True)

    payload = _fetch_payload(request, cv_text, region, budget)
    if isinstance(payload, str):
        return Compiled(answer=payload, failed=True)

    if not payload.get("is_job_search"):
        return Compiled(answer=str(payload.get("answer") or "").strip())

    questions: tuple[str, ...] = ()
    if payload.get("needs_clarification"):
        questions = tuple(str(q).strip() for q in (payload.get("questions") or [])
                          if str(q).strip())[:3]

    compiled = _from_payload(payload, request, bool(cv_text))

    if not compiled.core_terms:
        return Compiled(
            answer=("I could not tell what kind of work you are looking for. "
                    "Name a role or a technology — \"iOS developer\", \"data "
                    "engineer\", \"React\" — or share a CV and I will work it out."),
            questions=questions or ("What kind of work are you looking for?",),
            failed=True,
        )

    compiled, questions = _apply_region(compiled, region, questions)
    return Compiled(profile=compiled, questions=questions[:3])


def _compile_prompt(request: str, cv_text: str, region: str) -> str:
    parts = [f"What they are looking for:\n{request or '(not stated — read it from the CV)'}"]
    if region:
        parts.append(f"\nRegion stated explicitly, use it: {region}")
    else:
        parts.append("\nNo region was stated in the request. Read it from the CV "
                     "if you can; otherwise leave the region fields empty and ask.")
    if cv_text:
        parts.append("\nTheir CV:\n" + cv_text.strip()[: config.PROFILE_MAX_CV_CHARS])
    else:
        parts.append("\nNo CV was provided.")
    return "\n".join(parts)


def _weighted(rows: Any, floor: float, ceiling: float) -> dict[str, float]:
    """`[{term, weight}]` -> `{term: weight}`, clamped and de-duplicated."""
    out: dict[str, float] = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        term = str(row.get("term", "")).strip().lower()
        if not term:
            continue
        try:
            weight = float(row.get("weight", 0))
        except (TypeError, ValueError):
            continue
        out[term] = max(floor, min(ceiling, weight))
    return out


def _terms(values: Any, limit: int = 80) -> tuple[str, ...]:
    """Terms from a list, tolerating a bare string where a list belongs.

    A hand-written `candidate.local.json` saying `"core_terms": "warehouse"`
    used to be iterated letter by letter, leaving a search that matched on
    single characters. A model can send the same shape, so it is settled here
    rather than at either caller.
    """
    if isinstance(values, str):
        values = [part for part in values.split(",") if part.strip()] or [values]
    seen: list[str] = []
    for value in values or []:
        term = str(value).strip().lower()
        if term and term not in seen:
            seen.append(term)
    return tuple(seen[:limit])


def _regions_from(payload: dict) -> tuple[str, tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """(home country, match terms, city terms, target countries) from a payload."""
    named = [str(r).strip() for r in (payload.get("target_regions") or []) if str(r).strip()]
    targets = tuple(r for r in (region_mod.build(n) for n in named) if r)

    country = str(payload.get("home_country") or "").strip()
    home = region_mod.build(country) if country else region_mod.Region("", ())

    if targets:
        return (
            home.country or targets[0].country,
            tuple(dict.fromkeys(t for r in targets for t in r.terms)),
            tuple(dict.fromkeys(c for r in targets for c in r.cities)),
            tuple(r.country for r in targets),
        )
    if home:
        return home.country, home.terms, home.cities, ()
    return "", (), (), ()


def _arrangement(value) -> str:
    """One of any/remote/hybrid/onsite, defaulting to any."""
    text = str(value or "").strip().lower()
    if text in ("remote", "hybrid", "onsite"):
        return text
    if text in ("on-site", "on site", "office", "in-office"):
        return "onsite"
    return "any"


def _core_terms_from(payload: dict, request: str) -> tuple[str, ...]:
    """The words that *are* this role, or nothing if they cannot be determined."""
    core = _terms(payload.get("core_terms"), 8)
    if core:
        return core
    return tuple(w for w in re.findall(r"[a-z0-9+#.]{3,}", request.lower())
                 if w not in _STOPWORDS)[:4]


def _from_payload(payload: dict, request: str, has_cv: bool) -> SearchProfile:
    """Build a profile from the model's answer, sanitising every field."""
    core = _core_terms_from(payload, request)
    country, home_terms, city_terms, targets = _regions_from(payload)

    exclusions = tuple(t for t in _terms(payload.get("other_discipline_terms"), 60)
                       if t not in core)

    return SearchProfile(
        key="compiled:" + hashlib.sha1(request.encode("utf-8")).hexdigest()[:10],
        label=str(payload.get("label") or request or "Custom search").strip(),
        query=request,
        core_terms=core,
        secondary_terms=_terms(payload.get("secondary_terms"), 40),
        hands_on_title_tokens=_terms(payload.get("hands_on_title_tokens"), 30),
        hard_title_exclusions=_terms(payload.get("hard_title_exclusions"), 60),
        other_discipline_terms=exclusions,
        competing_stacks=_weighted(payload.get("competing_stacks"), 1.0, 10.0),
        min_body_core_mentions=_clamp_int(payload.get("min_body_core_mentions"), 1, 10, 4),
        small_employers_only=bool(payload.get("small_employers_only")
                                  or payload.get("startups_only")),
        employment_types=_employment_types(payload.get("employment_types")),
        startups_only=bool(payload.get("startups_only")),
        work_arrangement=_arrangement(payload.get("work_arrangement")),
        skills=_weighted(payload.get("skills"), 0.5, 10.0),
        domain_keywords=_weighted(payload.get("domain_keywords"), 0.5, 6.0),
        candidate_brief=str(payload.get("candidate_brief") or "").strip(),
        seniority=str(payload.get("seniority") or "Unspecified"),
        years_experience=_int(payload.get("years_experience")),
        has_cv=has_cv,
        home_country=country,
        target_regions=targets,
        home_terms=home_terms,
        home_city_terms=city_terms or _terms(payload.get("home_city_terms"), 40),
        region_terms=_terms(payload.get("region_terms"), 30),
        region_excluding_home_terms=_terms(payload.get("region_excluding_home_terms"), 30),
        timezone=str(payload.get("timezone") or ""),
        search_queries=_terms(payload.get("search_queries"), 12) or (request.lower(),),
        salary_floor_usd=_positive_float(payload.get("salary_floor_usd")),
        pay_floor_stated=bool(payload.get("pay_floor_stated")),
    )


_LOCATION_QUESTION_HINTS = (
    "which country", "what country", "where will you", "where are you",
    "which city", "what city", "living and working", "based in", "located",
)


def _asks_about_location(question: str) -> bool:
    """True when a question is asking where the person is."""
    low = question.lower()
    return any(hint in low for hint in _LOCATION_QUESTION_HINTS)


EMPLOYMENT_TYPES = ("Full Time", "Part Time", "Contract", "Freelance")


def _employment_types(values: Any) -> tuple[str, ...]:
    wanted = {str(v).strip().lower() for v in (values or [])}
    return tuple(t for t in EMPLOYMENT_TYPES if t.lower() in wanted)


def _clamp_int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _positive_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return max(0, min(60, int(value)))
    except (TypeError, ValueError):
        return 0


def narrowed_standing(region: str, query: str = "", cv: str = "") -> SearchProfile | None:
    """A standing search pointed at `region`, when that is all the caller gave.

    None when the caller said more than where, or when nothing is standing to
    narrow — both cases have to be compiled instead.
    """
    if not region or query or cv:
        return None
    standing = active()
    return with_region(standing, region) if standing.core_terms else None


def with_region(base: SearchProfile, stated: str) -> SearchProfile:
    """The same search, pointed somewhere the caller named.

    Lets a standing search be narrowed by region without recompiling it, so
    `--region` reads as the modifier it is documented to be.
    """
    settled, _ = _apply_region(base, stated, ())
    return settled


def describe(profile: SearchProfile) -> str:
    """A short human summary, for the CLI header and the MCP reply."""
    if profile.target_regions:
        where = "targeting " + ", ".join(profile.target_regions)
    elif profile.home_country and profile.region_detected:
        where = f"working from {profile.home_country} (detected from {profile.region_source})"
    elif profile.home_country:
        where = f"working from {profile.home_country}"
    else:
        where = "no region set — worldwide roles only"
    bits = [
        f"{profile.label} — core: {', '.join(profile.core_terms)}",
        "remote only" if profile.remote_only else "remote and on-site",
        where,
        f"{len(profile.skills)} skills weighted",
    ]
    if profile.has_cv:
        bits.append("ranked against the supplied CV")
    if profile.salary_floor_usd:
        bits.append(f"floor ${profile.salary_floor_usd:,.0f}")
    return "; ".join(bits)
