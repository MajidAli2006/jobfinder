"""Claude-based judgement for the two calls the keyword rules cannot make."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from collections.abc import Callable

from . import cache, config, profile

log = logging.getLogger("job_agent.llm")

PROMPT_VERSION = "v1"


_client: Any = None
_client_lock = threading.Lock()


def available() -> tuple[bool, str]:
    """(usable, human-readable reason) — checked once before any stage runs."""
    if not config.LLM_ENABLED:
        return False, "disabled (LLM_ENABLED=0)"
    if not config.ANTHROPIC_API_KEY:
        return False, "no ANTHROPIC_API_KEY in the environment or .env"
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False, "the `anthropic` package is not installed (pip install anthropic)"
    return True, f"ready ({config.LLM_MODEL})"


def _get_client() -> Any:
    global _client
    with _client_lock:
        if _client is None:
            import anthropic
            _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY, max_retries=3)
        return _client


class Budget:
    """Running cost of one run, at published list prices."""

    def __init__(self, limit_usd: float) -> None:
        self.limit = limit_usd
        self.spent = 0.0
        self.calls = 0
        self._lock = threading.Lock()

    def exhausted(self) -> bool:
        with self._lock:
            return self.spent >= self.limit

    def record(self, usage: Any) -> None:
        price_in, price_out = config.LLM_PRICES.get(config.LLM_MODEL, (5.0, 25.0))
        fresh = getattr(usage, "input_tokens", 0) or 0
        written = getattr(usage, "cache_creation_input_tokens", 0) or 0
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cost = ((fresh + written * 1.25 + read * 0.1) * price_in
                + out * price_out) / 1_000_000
        with self._lock:
            self.spent += cost
            self.calls += 1


def _candidate_brief() -> str:
    """Who this search is for, as the models should understand it."""
    active = profile.active()
    brief = (active.candidate_brief or profile.DEFAULT_CANDIDATE_BRIEF).strip()
    if not active.home_country:
        return (
            f"{brief}\n\n"
            f"Where they will be working from is not stated. Do not assume a "
            f"country, and treat eligibility as unclear unless the advert itself "
            f"settles it."
        )
    return (
        f"{brief}\n\n"
        f"They will be working from: {active.home_country}. "
        f"Judge eligibility on where they live and what they are entitled to do "
        f"there — never on nationality or where they are originally from."
    )


def system_eligibility() -> str:
    """The eligibility prompt for the active profile."""
    active = profile.active()
    home = active.home_country or "the country they are based in"
    region_note = ""
    if active.region_excluding_home_terms:
        listed = ", ".join(f'"{t}"' for t in active.region_excluding_home_terms[:4])
        region_note = (
            f"\n\nRegional phrasings that specifically EXCLUDE {home} — {listed} — "
            f"do not make someone in {home} eligible unless the advert also names "
            f"{home} itself."
        )

    return f"""\
You settle one question about a job advert: could this candidate actually do
this job, remotely, while living in {home}?

{_candidate_brief()}

A rule-based filter has already read this advert and could not decide. You are
the second reader. Answer from the advert's own words — do not assume, and do
not fill gaps with what is typical for the industry.

REMOTE — is the work fully remote?
- "yes": the advert states the role is remote, fully remote, distributed,
  work-from-anywhere, or home-based, with no required office attendance.
  Occasional travel — quarterly meetups, an annual offsite, "a few times a
  year" — is still fully remote.
- "no": hybrid, on-site, office-based, "X days a week in the office", any
  required weekly or monthly attendance, or a relocation requirement.
- "unclear": the advert never says.

ELIGIBLE — may someone living in {home} take this role?
- "yes" requires positive evidence that a resident of {home} qualifies. Any one
  of: the location field names {home} or one of its cities; the role is open
  worldwide or "work from anywhere"; a stated region explicitly includes {home};
  or the employer says it hires international contractors or through an
  employer-of-record service (Deel, Remote.com, Oyster, Velocity Global,
  Globalization Partners and the like).
- "no" for a residency or work-authorisation requirement they cannot meet: a
  single named country other than {home}, a work-authorisation or citizenship
  requirement elsewhere, a security clearance, or a working timezone that cannot
  be covered from {home}.
- "unclear": the advert is silent on location or region.{region_note}

Two traps the rule-based filter gets wrong, and you should not:

1. "Work from anywhere in the US" is a US restriction, not a worldwide offer.
   Read the whole phrase before deciding. The same trick appears with any
   country name.
2. A company mentioning an office, customers or a legal entity in {home} in its
   *About us* boilerplate does not make a role restricted to another country
   open to someone in {home}. The eligibility statement is what counts, not
   incidental geography.

Be decisive when the advert is decisive, and say "unclear" when it genuinely is.
A wrong "yes" wastes an afternoon on an application they cannot accept; a wrong
"no" costs them a real opportunity. Both are worse than an honest "unclear".

In `reason`, give one sentence explaining the verdict. In `evidence`, quote the
words from the advert you decided on — verbatim, at most 25 words. If nothing in
the advert supports a verdict, leave `evidence` empty and say "unclear"."""


def system_fit() -> str:
    """The fit-assessment prompt for the active profile."""
    active = profile.active()
    no_cv_note = "" if active.has_cv else (
        "\n\nNo CV was provided, so the background above is inferred from the "
        "search request rather than evidenced. Weight `fit` accordingly and say "
        "in `gaps` where an actual CV would change the answer."
    )
    return f"""\
You estimate how a specific candidate would fare applying for a job advert.

{_candidate_brief()}{no_cv_note}

The advert has already passed the hard filters — it is a relevant, remote,
eligible role. Do not re-litigate that. Your job is the question the keyword
scorer cannot answer: is this worth an afternoon of their time?

Return two numbers, and keep them distinct:

`fit` (0-100) — how closely their experience matches what the advert asks for.
    90+ : the core of the role is what they do, and the seniority matches
    70-89: clearly a role they can do, with some stretch
    50-69: adjacent — the core skill is secondary here, or a seniority mismatch
           in either direction
    below 50: they could apply, but they are not who the advert describes

`chance` (0-100) — how likely this application is to get a human reply. Fit is
    the floor, then adjust for what the advert reveals about the process:
      + a small or growing employer, a named hiring contact, a direct
        application on the company's own site, a niche or specific requirement
        that narrows the field, an advert posted in the last few days
      - a large employer with a queue, an applicant count in the hundreds, a
        recruitment agency listing "our client", a generic advert that a
        thousand others also match, an advert months old

    `chance` is usually below `fit`. A perfect match behind 300 applicants is a
    worse use of an afternoon than a good match nobody has found yet.

`verdict` — a one-word call: "strong", "worth applying", "long shot", or "skip".

`strengths` — up to 4 short phrases, each naming something concrete from *their*
    background that this specific advert asks for. A bare technology name is not
    useful; "regulated fintech — they want PSD2 experience" is. Skip anything the
    advert does not actually ask for.

`gaps` — up to 3 short phrases naming what they would have to talk their way
    past: a named technology they lack, a seniority or domain mismatch, an
    unstated salary, heavy competition. If there are none worth flagging, return
    an empty list rather than inventing one.

Be honest and calibrated. Inflating every advert to 90 makes the ranking
worthless, which is the only thing these numbers are for."""


ELIGIBILITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "remote": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "eligible": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "reason": {"type": "string"},
        "evidence": {"type": "string"},
    },
    "required": ["remote", "eligible", "reason", "evidence"],
    "additionalProperties": False,
}

FIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "fit": {"type": "integer"},
        "chance": {"type": "integer"},
        "verdict": {
            "type": "string",
            "enum": ["strong", "worth applying", "long shot", "skip"],
        },
        "strengths": {"type": "array", "items": {"type": "string"}},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["fit", "chance", "verdict", "strengths", "gaps"],
    "additionalProperties": False,
}


def advert_text(job: Any) -> str:
    """The advert as the model should see it: facts first, then the body text."""
    facts = [
        f"Title: {job.title}",
        f"Company: {job.company or 'Undisclosed'}",
        f"Location as published: {job.location or 'not stated'}",
    ]
    if job.employment_type and job.employment_type != "Unknown":
        facts.append(f"Employment type: {job.employment_type}")
    if job.company_size and job.company_size != "Unknown":
        facts.append(f"Company size: {job.company_size}")
    if job.applicants:
        facts.append(f"Applicants so far: {job.applicants}")
    if job.job_age_label and job.job_age_label != "Unknown":
        facts.append(f"Posted: {job.job_age_label}")
    if job.salary_min or job.salary_max or job.day_rate_min or job.day_rate_max:
        facts.append("Compensation: published in the advert")
    else:
        facts.append("Compensation: not published")

    body = (job.description or "").strip()
    if not body:
        body = "(The board published no description for this posting.)"
    body = body[: config.LLM_MAX_ADVERT_CHARS]

    return "\n".join(facts) + "\n\nAdvert text:\n" + body


def ask(system: str, schema: dict, user_text: str, effort: str,
         budget: Budget) -> dict | None:
    """One structured request. Returns the parsed object, or None on any failure."""
    if budget.exhausted():
        return None

    import anthropic

    try:
        response = _get_client().messages.create(
            model=config.LLM_MODEL,
            max_tokens=3000,
            system=[{
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }],
            output_config={
                "format": {"type": "json_schema", "schema": schema},
                "effort": effort,
            },
            messages=[{"role": "user", "content": user_text}],
        )
    except anthropic.AuthenticationError:
        log.warning("Claude rejected the API key — skipping the judgement layer")
        return None
    except anthropic.RateLimitError:
        log.warning("Claude rate limit reached — this posting keeps its rules verdict")
        return None
    except anthropic.APIStatusError as exc:
        log.warning("Claude returned %s — this posting keeps its rules verdict",
                    exc.status_code)
        return None
    except anthropic.APIConnectionError:
        log.warning("Could not reach Claude — this posting keeps its rules verdict")
        return None

    budget.record(response.usage)

    if response.stop_reason == "refusal":
        return None

    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        parsed = json.loads(text)
    except ValueError:
        log.debug("Claude returned unparseable JSON: %.120s", text)
        return None
    return parsed if isinstance(parsed, dict) else None


def _candidate_fingerprint() -> str:
    """The candidate half of a prompt, as a cache key.

    A verdict answers a question about a job *and* a person. Keyed on the job
    alone, one formed against a mobile engineer's CV is served unchanged to the
    next search — so a full-stack search run for somebody else was told its
    leads wanted skills "the CV" did not evidence, naming a CV it never saw.

    The prompts themselves are what is hashed, rather than the fields they are
    built from. Listing the fields means the key drifts the moment the prompt
    reads one the list forgot: `has_cv` alone changes what the model is asked
    to do with a thin background, and would have gone unnoticed.
    """
    prompts = system_eligibility() + "\x00" + system_fit()
    return hashlib.sha1(prompts.encode("utf-8")).hexdigest()[:16]


def _run_stage(jobs: list, cache_kind: str, build: Callable[[Any], dict | None],
               budget: Budget, limit: int) -> tuple[dict[str, dict], int, int]:
    """Fetch a verdict per job, from cache where possible and Claude otherwise."""
    verdicts: dict[str, dict] = {}
    pending: list = []
    hits = 0

    candidate = _candidate_fingerprint()

    for job in jobs:
        key = (f"llm:{cache_kind}:{PROMPT_VERSION}:{config.LLM_MODEL}:"
               f"{candidate}:{job.fingerprint}")
        cached = cache.get(key, config.LLM_CACHE_DAYS)
        if cached is not None:
            verdicts[job.fingerprint] = cached
            hits += 1
        else:
            pending.append((job, key))

    pending = pending[:limit]
    if not pending:
        return verdicts, hits, 0

    def work(item):
        job, key = item
        result = build(job)
        if result is not None:
            cache.put(key, result)
        return job.fingerprint, result

    calls = 0
    with ThreadPoolExecutor(max_workers=max(1, config.LLM_CONCURRENCY)) as pool:
        for fingerprint, result in pool.map(work, pending):
            calls += 1
            if result is not None:
                verdicts[fingerprint] = result

    return verdicts, hits, calls


def adjudicate_eligibility(prospects: list, stats: Any, budget: Budget) -> list:
    """Decide the postings the rules could not, and return the ones that clear."""
    if not prospects:
        return []

    verdicts, hits, calls = _run_stage(
        prospects, "eligibility",
        lambda job: ask(system_eligibility(), ELIGIBILITY_SCHEMA, advert_text(job),
                         config.LLM_EFFORT_ELIGIBILITY, budget),
        budget, config.LLM_MAX_ELIGIBILITY_CALLS,
    )
    stats.llm_cache_hits += hits
    stats.llm_eligibility_calls += calls

    promoted: list = []
    for job in prospects:
        verdict = verdicts.get(job.fingerprint)
        if not verdict:
            continue

        reason = str(verdict.get("reason", "")).strip()
        evidence = str(verdict.get("evidence", "")).strip()
        note = f"Assessed: {reason}" if reason else ""
        if evidence:
            note = f'{note} — advert says: "{evidence}"' if note else f'Advert says: "{evidence}"'

        job.llm_eligibility = str(verdict.get("eligible", "")).strip().lower()
        job.llm_eligibility_reason = note

        if job.llm_eligibility == "yes" and str(verdict.get("remote", "")).lower() == "yes":
            job.is_prospect = False
            job.rejected = False
            job.rejection_reason = ""
            job.rejection_category = ""
            job.eligibility = "UK eligible (AI-confirmed)"
            job.remote_status = job.remote_status or "Remote"
            job.opportunity_type = "Job"
            job.job_status = "NEW"
            job.application_status = "Not Applied"
            job.llm_promoted = True
            job.match_score = min(job.match_score, config.LLM_PROMOTED_SCORE_CAP)
            if note:
                job.match_reasons.insert(0, note)
            job.concerns = [c for c in job.concerns if "eligib" not in c.lower()]
            promoted.append(job)
            stats.llm_promoted += 1
        elif job.llm_eligibility == "no":
            job.rejection_reason = note or job.rejection_reason
            job.job_status = "Not eligible"
            job.application_status = "Do not apply"
            stats.llm_confirmed_ineligible += 1
        elif note:
            job.concerns.insert(0, note)

    return promoted


def _set_aside_long_shots(jobs: list, verdicts: dict) -> list:
    """Mark the leads too unlikely to be worth reading first.

    The floor judges a lead, not a search. Applied literally it empties a thin
    run, leaving someone in a hard market with none of the adverts they did
    qualify for, so leads are only set aside once enough remain — weakest first.
    """
    floor = config.LLM_MIN_CHANCE_WITH_CV
    if not profile.active().has_cv or floor <= 0:
        return []

    room = len(jobs) - config.LLM_CHANCE_FLOOR_MIN_KEPT
    if room <= 0:
        return []

    below_floor = sorted(
        (job for job in jobs
         if job.fingerprint in verdicts and job.llm_chance < floor),
        key=lambda job: job.llm_chance,
    )

    for job in below_floor[:room]:
        job.rejected = True
        job.rejection_category = "low_chance"
        job.rejection_reason = (
            f"Reply chance {job.llm_chance} against your CV, below the {floor} "
            f"floor — {job.llm_verdict or 'a long shot'}"
        )
        job.job_status = "Long shot"
        job.application_status = "Only if you have time"
    return below_floor[:room]


def assess_fit(jobs: list, stats: Any, budget: Budget) -> list:
    """Score how likely each lead is to land, re-rank on it, and return the long shots."""
    if not jobs:
        return []

    ranked = sorted(jobs, key=lambda j: j.match_score, reverse=True)

    verdicts, hits, calls = _run_stage(
        ranked, "fit",
        lambda job: ask(system_fit(), FIT_SCHEMA, advert_text(job),
                         config.LLM_EFFORT_FIT, budget),
        budget, config.LLM_MAX_FIT_CALLS,
    )
    stats.llm_cache_hits += hits
    stats.llm_fit_calls += calls

    weight = max(0.0, min(1.0, config.LLM_FIT_WEIGHT))
    for job in jobs:
        verdict = verdicts.get(job.fingerprint)
        if not verdict:
            continue

        job.llm_fit = _clamp(verdict.get("fit"))
        job.llm_chance = _clamp(verdict.get("chance"))
        job.llm_verdict = str(verdict.get("verdict") or "").strip()

        for strength in list(verdict.get("strengths") or [])[:4]:
            text = str(strength).strip()
            if text:
                job.match_reasons.append(f"Assessed: {text}")
        named_gaps = [str(gap).strip() for gap in (verdict.get("gaps") or [])[:3]]
        named_gaps = [gap for gap in named_gaps if gap]
        for gap in named_gaps:
            job.concerns.append(f"Assessed: {gap}")
        if named_gaps:
            job.potential_gaps = " · ".join(named_gaps)

        if job.llm_verdict:
            job.match_reasons.insert(
                0,
                f"Verdict: {job.llm_verdict} — fit {job.llm_fit}, "
                f"reply chance {job.llm_chance}",
            )

        blended = job.match_score * (1 - weight) + job.llm_chance * weight
        job.match_score = int(max(0, min(100, round(blended))))

    return _set_aside_long_shots(jobs, verdicts)


def _clamp(value: Any) -> int:
    try:
        return max(0, min(100, int(value)))
    except (TypeError, ValueError):
        return 0
