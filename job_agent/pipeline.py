"""The end-to-end run: collect -> filter -> classify -> enrich -> score -> dedupe."""

from __future__ import annotations

import logging
import time
from collections import Counter
from dataclasses import dataclass, field

from . import (
    cache, chance, classify, config, contacts, descriptions,
    enrich as enrich_mod, filters, gaps, llm, money, profile,
    remote, scoring, storage,
)
from .dedupe import deduplicate, fingerprint, same_employer_and_role
from .models import Job, RawJob, RunStats, SourceStat
from .sources import build_sources
from .utils import (
    freshness_window, job_age, normalize, now_local, parse_compensation, scrub,
    word_present,
)

log = logging.getLogger("job_agent.pipeline")


@dataclass
class RunResult:
    stats: RunStats
    qualified: list[Job] = field(default_factory=list)
    prospects: list[Job] = field(default_factory=list)
    rejected: list[Job] = field(default_factory=list)

    hot_leads: list[Job] = field(default_factory=list)
    full_time: list[Job] = field(default_factory=list)
    part_time: list[Job] = field(default_factory=list)
    contract: list[Job] = field(default_factory=list)
    freelance: list[Job] = field(default_factory=list)
    startups: list[Job] = field(default_factory=list)
    partnerships: list[Job] = field(default_factory=list)
    #: Passed every filter, but the model rated the reply chance low. Kept
    #: visible and ranked last rather than dropped: they are still the best
    #: available when a search returns little else.
    long_shots: list[Job] = field(default_factory=list)
    companies: list[dict] = field(default_factory=list)


def collect(offline: bool = False, only: tuple[str, ...] = ()) -> tuple[list[RawJob], list[SourceStat]]:
    raws: list[RawJob] = []
    source_stats: list[SourceStat] = []

    for source in build_sources(offline=offline, only=only):
        started = time.time()
        stat = SourceStat(name=source.label)
        try:
            jobs = source.collect()
            stat.raw_count = len(jobs)
            raws.extend(jobs)
            log.info("%-28s %4d postings", source.label, len(jobs))
        except Exception as exc:  # noqa: BLE001
            stat.ok = False
            # Scrubbed: a failed request raises with the full URL, and some
            # boards carry their key in the path. This string is logged and
            # printed in the funnel.
            stat.error = scrub(f"{type(exc).__name__}: {exc}")[:200]
            log.warning("%-28s FAILED (%s)", source.label, stat.error)
        stat.elapsed = round(time.time() - started, 1)
        source_stats.append(stat)

    return raws, source_stats


PAY_LINE_TOKENS = (
    "salary", "£", "$", "€", "day rate", "per day", "per hour",
    "compensation", "rate:", "gbp", "usd", "eur",
)

#: Below this, a figure presented as an annual salary is really a day rate.
DAY_RATE_CEILING = 2_000


def _pay_from_description(raw: RawJob) -> dict:
    """Read pay out of the advert body, for adverts that publish no salary field."""
    snippet = " ".join(
        line for line in (raw.description or "").splitlines()
        if any(token in line.lower() for token in PAY_LINE_TOKENS)
    )
    return parse_compensation(snippet[:2000])


def _in_order(low, high):
    """A range quoted backwards is still a range."""
    if low and high and low > high:
        return high, low
    return low, high


def _money(raw: RawJob) -> dict:
    """Combine structured salary fields with anything parseable from the text."""
    parsed = parse_compensation(raw.salary_raw or "")
    nothing_parsed = not any(
        parsed[k] for k in ("salary_min", "salary_max", "day_rate_min", "day_rate_max"))
    if nothing_parsed and not (raw.salary_min or raw.salary_max):
        parsed = _pay_from_description(raw)

    salary_min = raw.salary_min or parsed["salary_min"]
    salary_max = raw.salary_max or parsed["salary_max"]
    currency = raw.salary_currency or parsed["currency"] or ""
    day_min, day_max = parsed["day_rate_min"], parsed["day_rate_max"]

    if salary_min and salary_min < DAY_RATE_CEILING and not day_min:
        day_min, day_max = salary_min, salary_max
        salary_min = salary_max = None

    salary_min, salary_max = _in_order(salary_min, salary_max)
    day_min, day_max = _in_order(day_min, day_max)

    return {
        "salary_min": salary_min, "salary_max": salary_max,
        "salary_currency": currency,
        "day_rate_min": day_min, "day_rate_max": day_max,
    }


def add_contacts(jobs: list[Job], *, offline: bool) -> None:
    """Look up published contact details for leads worth writing to.

    Last, and only for survivors: it costs several requests per employer, so
    it never runs offline and never for adverts the filters already rejected.
    """
    if offline or not config.FETCH_EMPLOYER_CONTACTS:
        return
    for job in jobs:
        contacts.enrich(job)


def build_job(raw: RawJob, verdict: remote.RemoteVerdict,
              relevance: filters.Verdict, run_date) -> Job:
    text = normalize(raw.haystack())
    traits = classify.classify_all(raw)
    pay = _money(raw)
    age_days, age_label = job_age(raw.posted_at)

    job = Job(
        fingerprint=fingerprint(raw.company, raw.title),
        source=raw.source,
        sources=[raw.source],
        posted_at=raw.posted_at,
        posted_date=raw.posted_at.date() if raw.posted_at else None,
        job_age_days=age_days,
        job_age_label=age_label,
        discovered_date=run_date,
        verified_date=run_date,
        title=raw.title.strip(),
        company=(raw.company or "Undisclosed").strip(),
        location=raw.location_raw.strip() or "Remote",
        remote_status=verdict.remote_status,
        eligibility=verdict.eligibility,
        employment_type=traits["employment_type"],
        contract_type=traits["contract_type"],
        seniority=traits["seniority"],
        experience_level=traits["experience_level"],
        industry=traits["industry"],
        startup_stage=traits["startup_stage"],
        company_size=traits["company_size"],
        applicants=str(raw.extra.get("applicants") or ""),
        opportunity_type=traits["opportunity_type"],
        required_years=traits.get("required_years"),
        description_excerpt=raw.description[:4000],
        core_skill_required=traits["core_skill_required"],
        secondary_skill_required=traits["secondary_skill_required"],
        is_startup=traits["is_startup"],
        is_partnership=traits["is_partnership"],
        tags=raw.tags,
        **pay,
    )

    job.match_reasons = list(relevance.details) + list(verdict.details)
    job.concerns = list(relevance.concerns) + list(verdict.concerns)
    job.salary_usd_equivalent = money.annual_usd(job)[0]

    enrich_mod.enrich(job, raw)

    fit, reasons, concerns = scoring.score_match(job, text, traits.get("required_years"))
    job.cv_fit_score = fit
    job.match_reasons.extend(reasons)
    job.concerns.extend(concerns)

    net_score, notes = scoring.score_networking(job, text)
    job.networking_score = net_score
    job.match_reasons.extend(notes)

    # Ranking is by chance of being shortlisted, not by overlap. Networking and
    # contact details are already on the job, so the estimate can see them.
    estimate = chance.estimate(job, fit)
    job.match_score = estimate.score
    job.chance_explained = estimate.explain()
    job.potential_gaps = gaps.describe(job)

    job.match_reasons = list(dict.fromkeys(r for r in job.match_reasons if r))
    job.concerns = list(dict.fromkeys(c for c in job.concerns if c))

    return job


UNDETERMINED = ("truncated", "not stated", "not confirmed", "unconfirmed",
                "cannot be verified", "is not confirmed")


def keep_prospect(job: Job) -> bool:
    """Is this near-miss worth an email, or is it just noise on the sheet?"""
    if job.rejection_category == "pay_unstated":
        return job.match_score >= config.MIN_QUALIFY_SCORE

    reason = (job.rejection_reason or "").lower()
    if not any(marker in reason for marker in UNDETERMINED):
        return job.match_score >= config.PROSPECT_MIN_SCORE

    title = normalize(job.title)
    if any(word_present(term, title) for term in profile.active().core_terms):
        return True
    return job.match_score >= config.PROSPECT_UNVERIFIED_MIN_SCORE


@dataclass
class Screened:
    """What the per-advert filters made of a batch of raw postings."""

    qualified: list[Job] = field(default_factory=list)
    prospects: list[Job] = field(default_factory=list)
    rejected: list[Job] = field(default_factory=list)


def _wrong_arrangement(arrangement: str, wanted: str) -> remote.RemoteVerdict:
    return remote.RemoteVerdict(
        False,
        f"{remote.arrangement_label(arrangement)} role, and this search "
        f"asked for {wanted} work",
        "wrong_arrangement",
        remote_status=remote.arrangement_label(arrangement),
        eligibility="N/A",
    )


def judge_arrangement(raw: RawJob, wanted: str) -> remote.RemoteVerdict:
    """The advert's arrangement verdict, settled against what the search asked for.

    The single place that answers "would this advert survive the remote gate",
    so `jobfinder check` explains the run rather than a stricter rule of its own.
    """
    return _reconcile_arrangement(raw, remote.assess_remote(raw), wanted)


def _reconcile_arrangement(raw: RawJob, verdict: remote.RemoteVerdict,
                           wanted: str) -> remote.RemoteVerdict:
    """Judge the advert's arrangement against what this search asked for.

    `assess_remote` reads only the advert. Whether "hybrid, three days in the
    office" disqualifies it is a question about the search, not the advert, so
    it is settled here.
    """
    arrangement = remote.classify_arrangement(
        normalize(raw.haystack()),
        source_says_remote=bool(raw.extra.get("is_remote")))

    if not verdict.passed and verdict.category == "not_remote":
        if remote.arrangement_wanted(arrangement):
            return remote.RemoteVerdict(
                True, "", "",
                remote_status=remote.arrangement_label(arrangement),
                eligibility=verdict.eligibility or "Judged on location",
            )
        return _wrong_arrangement(arrangement, wanted)

    if verdict.passed and not remote.arrangement_wanted(arrangement):
        return _wrong_arrangement(arrangement, wanted)

    return verdict


def _as_prospect(raw: RawJob, verdict: remote.RemoteVerdict,
                 relevance: filters.Verdict, run_date) -> Job:
    """A lead worth keeping, with the reason it could not be confirmed on it."""
    prospect = build_job(raw, verdict, relevance, run_date)
    prospect.is_prospect = True
    prospect.rejected = True
    prospect.rejection_reason = verdict.reason
    prospect.rejection_category = verdict.category
    prospect.opportunity_type = "Prospect (eligibility unconfirmed)"
    prospect.job_status = "Needs verification"
    prospect.application_status = "Ask before applying"
    prospect.concerns.insert(0, verdict.reason)
    return prospect


def screen(raws: list[RawJob], stats: RunStats, cutoff, run_date, *,
           min_salary_usd: float, require_salary: bool,
           skip_low_rate_markets: bool) -> Screened:
    """Run every per-advert filter, in the order that discards fastest first."""
    active_profile = profile.active()
    qualified: list[Job] = []
    prospects: list[Job] = []
    rejected: list[Job] = []

    for raw in raws:
        if not raw.title or not raw.url:
            continue

        relevance = filters.check_relevance(raw)
        if not relevance.passed:
            stats.rejected_irrelevant += 1
            continue

        fresh = filters.check_freshness(raw, cutoff)
        if not fresh.passed:
            stats.rejected_stale += 1
            rejected.append(_rejection_stub(raw, fresh.reason, "stale", run_date))
            continue
        if raw.posted_at is None:
            stats.undated_kept += 1
        relevance.concerns.extend(fresh.concerns)

        if skip_low_rate_markets:
            market = filters.check_market(raw)
            if not market.passed:
                stats.rejected_low_rate_market += 1
                rejected.append(_rejection_stub(raw, market.reason, market.category, run_date))
                continue

        size = filters.check_employer_size(raw, classify.company_size(raw.haystack()))
        if not size.passed:
            stats.rejected_large_employer += 1
            rejected.append(_rejection_stub(raw, size.reason, size.category, run_date))
            continue

        verdict = judge_arrangement(raw, active_profile.work_arrangement)
        if not verdict.passed:
            if verdict.category in ("not_remote", "wrong_arrangement"):
                stats.rejected_not_remote += 1
            elif verdict.category == "region_unknown":
                stats.region_unknown += 1
            else:
                stats.rejected_ineligible += 1

            stub = _rejection_stub(raw, verdict.reason, verdict.category, run_date)
            rejected.append(stub)

            if verdict.prospect_worthy:
                prospects.append(_as_prospect(raw, verdict, relevance, run_date))
            continue

        job = build_job(raw, verdict, relevance, run_date)

        pay = filters.check_pay_floor(job, min_salary_usd, require_salary)
        job.match_reasons.extend(pay.details)
        job.concerns.extend(pay.concerns)
        if not pay.passed:
            job.rejected = True
            job.rejection_reason = pay.reason
            job.rejection_category = pay.category
            if pay.category == "pay_unstated":
                stats.pay_unstated += 1
                job.is_prospect = True
                job.opportunity_type = "Prospect (pay unstated)"
                job.job_status = "Needs verification"
                job.concerns.insert(0, pay.reason)
                prospects.append(job)
            else:
                stats.rejected_low_pay += 1
                rejected.append(job)
            continue

        engagement = filters.check_engagement(job)
        if not engagement.passed:
            stats.rejected_wrong_engagement += 1
            job.rejected = True
            job.rejection_reason = engagement.reason
            job.rejection_category = engagement.category
            rejected.append(job)
            continue

        if job.match_score < config.MIN_QUALIFY_SCORE:
            stats.rejected_low_score += 1
            job.rejected = True
            job.rejection_reason = f"Match score {job.match_score} below threshold {config.MIN_QUALIFY_SCORE}"
            job.rejection_category = "low_score"
            rejected.append(job)
            continue

        qualified.append(job)


    return Screened(qualified, prospects, rejected)


def judge(qualified: list[Job], prospects: list[Job], rejected: list[Job],
          stats: RunStats, run_date, *,
          verify_live: bool) -> tuple[list[Job], list[Job], list[Job]]:
    """Settle what the keyword rules could not, and return the revised lists."""
    ok, why = llm.available()
    if not ok:
        log.info("Judgement layer not used: %s", why)
        return qualified, prospects, []

    stats.llm_ran = True
    budget = llm.Budget(config.LLM_MAX_SPEND_USD)
    log.info("Judgement layer: %s", why)

    promoted = llm.adjudicate_eligibility(prospects, stats, budget)
    if promoted:
        if verify_live:
            still_open = []
            for job in promoted:
                alive, note = filters.verify_live(job)
                job.verified_date = run_date
                if alive:
                    if note:
                        job.concerns.append(note)
                    still_open.append(job)
                else:
                    stats.rejected_expired += 1
                    job.rejected = True
                    job.rejection_reason = note
                    job.rejection_category = "expired"
                    job.job_status = "Closed"
                    rejected.append(job)
            promoted = still_open

        kept = {id(job) for job in promoted}
        prospects = [p for p in prospects if id(p) not in kept and not p.llm_promoted]
        qualified = qualified + promoted
        stats.llm_promoted = len(promoted)

    long_shots = llm.assess_fit(qualified, stats, budget)
    if long_shots:
        set_aside = {id(job) for job in long_shots}
        qualified = [job for job in qualified if id(job) not in set_aside]
        stats.rejected_low_chance = len(long_shots)

    stats.llm_cost_usd = round(budget.spent, 4)
    return qualified, prospects, long_shots


def run(offline: bool = False, only: tuple[str, ...] = (),
        days: int = config.FRESHNESS_DAYS,
        min_salary_usd: float = config.SALARY_FLOOR_USD,
        require_salary: bool = False,
        skip_low_rate_markets: bool = True,
        verify_live: bool = True,
        use_llm: bool = True,
        search_profile: profile.SearchProfile | None = None) -> RunResult:
    """One end-to-end run."""
    if search_profile is not None:
        profile.set_active(search_profile)
    active_profile = profile.active()
    if min_salary_usd == config.SALARY_FLOOR_USD and active_profile.salary_floor_usd:
        min_salary_usd = active_profile.salary_floor_usd
    config.ensure_dirs()
    started = now_local()
    cutoff, period_start, period_end = freshness_window(days)
    run_date = started.date()

    raws, source_stats = collect(offline=offline, only=only)

    descriptions_filled = 0 if offline else descriptions.fill_descriptions(raws)

    stats = RunStats(
        run_at=started,
        period_start=period_start,
        period_end=period_end,
        sources_searched=len(source_stats),
        sources=source_stats,
        raw_found=len(raws),
        descriptions_filled=descriptions_filled,
        profile_label=active_profile.label,
        profile_query=active_profile.query,
        profile_home=active_profile.home_country,
        profile_has_cv=active_profile.has_cv,
        salary_floor_usd=min_salary_usd,
    )

    pruned = cache.prune(config.LINKEDIN_CACHE_DAYS)
    if pruned:
        log.debug("pruned %d expired cache entries", pruned)

    screened = screen(raws, stats, cutoff, run_date,
                      min_salary_usd=min_salary_usd,
                      require_salary=require_salary,
                      skip_low_rate_markets=skip_low_rate_markets)
    qualified, prospects, rejected = (
        screened.qualified, screened.prospects, screened.rejected)

    qualified, removed = deduplicate(qualified)
    stats.duplicates_removed = removed

    prospects, prospect_dupes = deduplicate(prospects)

    prospects = [p for p in prospects if keep_prospect(p)]
    stats.duplicates_removed += prospect_dupes

    qualified_keys = {j.fingerprint for j in qualified}
    prospects = [
        p for p in prospects
        if p.fingerprint not in qualified_keys
        and not any(same_employer_and_role(p, j) for j in qualified)
    ]

    if verify_live and qualified:
        qualified = _drop_closed_adverts(qualified, rejected, stats, run_date)

    if verify_live:
        _clear_dead_careers_pages(qualified + prospects)

    long_shots: list[Job] = []
    if use_llm:
        qualified, prospects, long_shots = judge(qualified, prospects, rejected, stats,
                                                 run_date, verify_live=verify_live)

    add_contacts(qualified, offline=offline)

    stats.new_since_last_run = storage.mark_new_and_record(qualified, run_date)
    storage.mark_new_and_record(prospects, run_date)
    _label_prospects(prospects)

    qualified.sort(key=_sort_key, reverse=True)
    prospects.sort(key=_sort_key, reverse=True)
    long_shots.sort(key=_sort_key, reverse=True)

    result = RunResult(stats=stats, qualified=qualified, prospects=prospects,
                       rejected=rejected, long_shots=long_shots)
    _bucket(result)
    return result


def _drop_closed_adverts(qualified: list[Job], rejected: list[Job],
                         stats: RunStats, run_date) -> list[Job]:
    """Re-read each advert and move the ones that have closed to rejected."""
    still_open: list[Job] = []
    for job in qualified:
        ok, note = filters.verify_live(job)
        job.verified_date = run_date
        if ok:
            if note:
                job.concerns.append(note)
            still_open.append(job)
        else:
            stats.rejected_expired += 1
            job.rejected = True
            job.rejection_reason = note
            job.rejection_category = "expired"
            job.job_status = "Closed"
            rejected.append(job)
    return still_open


def _clear_dead_careers_pages(jobs: list[Job]) -> None:
    """Drop guessed careers-page URLs that do not resolve, checking each host once."""
    verdicts: dict[str, bool] = {}
    for job in jobs:
        url = job.careers_page
        if not url or not job.careers_page_guessed:
            continue
        if url not in verdicts:
            resp = filters.http_get(url, timeout=10, retries=0)
            verdicts[url] = resp is not None and resp.status_code == 200
        if not verdicts[url]:
            job.careers_page = ""


def _label_prospects(prospects: list[Job]) -> None:
    """A prospect is unconfirmed, so it carries what still needs checking."""
    for prospect in prospects:
        if prospect.llm_eligibility == "no":
            prospect.job_status = "Not eligible (AI-checked)"
            prospect.application_status = "Do not apply"
            continue
        prospect.job_status = "NEW — needs verification" if prospect.is_new else "Needs verification"
        prospect.application_status = (
            "Ask about pay before applying"
            if prospect.rejection_category == "pay_unstated"
            else "Ask before applying")


def _sort_key(job: Job):
    return (job.match_score, job.posted_at.timestamp() if job.posted_at else 0)


def _rejection_stub(raw: RawJob, reason: str, category: str, run_date) -> Job:
    age_days, age_label = job_age(raw.posted_at)
    return Job(
        fingerprint=fingerprint(raw.company, raw.title),
        source=raw.source,
        title=raw.title,
        company=raw.company,
        location=raw.location_raw,
        posted_at=raw.posted_at,
        posted_date=raw.posted_at.date() if raw.posted_at else None,
        job_age_days=age_days,
        job_age_label=age_label,
        original_job_url=raw.url,
        application_url=raw.apply_url or raw.url,
        discovered_date=run_date,
        rejected=True,
        rejection_reason=reason,
        rejection_category=category,
        job_status="Rejected",
    )


def _bucket(result: RunResult) -> None:
    jobs = result.qualified
    stats = result.stats

    result.hot_leads = [j for j in jobs if j.match_score >= config.HOT_LEAD_SCORE]
    result.full_time = [j for j in jobs if j.employment_type == "Full Time"]
    result.part_time = [j for j in jobs if j.employment_type == "Part Time"]
    result.contract = [j for j in jobs if j.employment_type == "Contract"]
    result.freelance = [j for j in jobs if j.employment_type == "Freelance"]
    result.startups = [j for j in jobs if j.is_startup]
    result.partnerships = [j for j in jobs if j.is_partnership]
    result.companies = build_company_directory(jobs + result.prospects)

    stats.qualified = len(jobs)
    for name in ("hot_leads", "full_time", "part_time", "contract", "freelance",
                 "startups", "partnerships", "prospects", "companies"):
        setattr(stats, name, len(getattr(result, name)))

    levels = Counter(j.experience_level for j in jobs)
    stats.level_beginner = levels[config.LEVEL_BEGINNER]
    stats.level_medium = levels[config.LEVEL_MEDIUM]
    stats.level_senior = levels[config.LEVEL_SENIOR]
    stats.level_unspecified = levels[config.LEVEL_UNSPECIFIED]


def build_company_directory(jobs: list[Job]) -> list[dict]:
    """One row per company, aggregating every opportunity and contact route."""
    by_company: dict[str, dict] = {}
    for job in jobs:
        key = job.company.lower().strip()
        if not key:
            continue
        entry = by_company.setdefault(key, {
            "company": job.company,
            "opportunities": 0,
            "best_match_score": 0,
            "networking_score": 0,
            "industry": job.industry,
            "company_size": job.company_size,
            "startup_stage": job.startup_stage,
            "best_contact_name": "",
            "contact_role": "",
            "public_email": "",
            "public_phone": "",
            "linkedin": "",
            "company_website": "",
            "careers_page": "",
            "latest_posted": None,
            "roles": [],
            "outreach_status": "Not contacted",
        })
        entry["opportunities"] += 1
        entry["best_match_score"] = max(entry["best_match_score"], job.match_score)
        entry["networking_score"] = max(entry["networking_score"], job.networking_score)
        entry["roles"].append(job.title)
        for field_name in ("best_contact_name", "contact_role", "public_email",
                           "public_phone", "linkedin", "company_website", "careers_page"):
            if not entry[field_name]:
                entry[field_name] = getattr(job, field_name)
        if job.posted_date and (entry["latest_posted"] is None or job.posted_date > entry["latest_posted"]):
            entry["latest_posted"] = job.posted_date
        if job.industry != "Unknown / General Tech":
            entry["industry"] = job.industry
        if job.startup_stage != "N/A":
            entry["startup_stage"] = job.startup_stage

    directory = list(by_company.values())
    for entry in directory:
        entry["roles"] = "; ".join(dict.fromkeys(entry["roles"]))
    directory.sort(key=lambda e: (e["best_match_score"], e["networking_score"]), reverse=True)
    return directory
