"""Command line entry point."""

from __future__ import annotations

import argparse
import getpass
import logging
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import cache, config, filters, llm, platforms, profile, storage
from .models import RawJob
from .pipeline import judge_arrangement, run as run_pipeline
from .report_data import write_csv, write_json
from .report_excel import build_workbook
from .report_html import build_html
from .sources import keyed_sources, known_source_names, source_catalogue
from .utils import now_local
import contextlib

log = logging.getLogger("job_agent")


#: Formats that are archives rather than text, so reading them as text yields
#: markup and no words. Named so the refusal can say which one it is.
WORD_PROCESSOR_SUFFIXES = (".docx", ".doc", ".odt", ".rtf", ".pages")


def read_cv(path: str) -> str:
    """Read a CV from disk. Plain text and Markdown always; PDF when pypdf is present."""
    file = Path(path).expanduser()
    if not file.exists():
        raise SystemExit(f"No CV at {file}")

    if file.suffix.lower() in WORD_PROCESSOR_SUFFIXES:
        raise SystemExit(
            f"{file.name} is a {file.suffix.lower()} file, which this reads as "
            f"the compressed archive it is rather than as words. Export the CV "
            f"as PDF or plain text and pass that instead."
        )

    if file.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise SystemExit(
                f"{file.name} is a PDF and pypdf is not installed. Either run "
                f"`pip install pypdf`, or export the CV as .txt and pass that."
            ) from exc
        try:
            pages = [page.extract_text() or "" for page in PdfReader(str(file)).pages]
        except Exception as exc:  # noqa: BLE001 - any damaged PDF, not one kind
            raise SystemExit(
                f"{file.name} could not be read as a PDF ({type(exc).__name__}). "
                f"If the file is damaged or only partly downloaded, open it, "
                f"re-save it, or export the CV as .txt and pass that."
            ) from exc
        text = "\n".join(pages)
    else:
        text = file.read_text(encoding="utf-8", errors="replace")

    if len(text.strip()) < 200:
        raise SystemExit(
            f"{file.name} yielded almost no text ({len(text.strip())} characters). "
            f"If it is a scanned PDF, export a text version instead."
        )
    return text


def _setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.DEBUG if verbose else (logging.WARNING if quiet else logging.INFO)
    logging.basicConfig(level=level, format="%(message)s", stream=sys.stdout)
    if not verbose:
        for noisy in ("httpx", "httpcore", "anthropic", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def print_header(args, llm_ready: bool, llm_why: str) -> None:
    """What this run is about to do, before it does any of it."""
    print("╭─ Job hunt " + "─" * 54)
    print(f"│ Run date     : {now_local():%Y-%m-%d %H:%M %Z}")
    print(f"│ Window       : last {args.days} calendar days")
    print(f"│ Mode         : {'offline fixtures' if args.offline else 'live sources'}"
          f"{' (deep sweep)' if config.LINKEDIN_DEEP else ''}"
          f"{' — quick' if getattr(args, 'quick', False) else ''}")
    if args.min_salary:
        print(f"│ Pay floor    : ${args.min_salary:,.0f}/yr USD equivalent"
              f"{' (a compiled search may raise this)' if args.query or args.cv else ''}")
    else:
        print("│ Pay floor    : none"
              f"{' (a compiled search may set one)' if args.query or args.cv else ''}")
    print(f"│ Judgement    : "
          f"{'on — ' + llm_why if llm_ready and not args.no_llm else 'rules only'}")
    print("╰" + "─" * 66)


def print_funnel(stats, args) -> None:
    """The rejection ledger: what came in, what left, and by which door."""
    print("Funnel")
    print(f"  raw postings found        {stats.raw_found}")
    print(f"  full adverts fetched      +{stats.descriptions_filled}")
    print(f"  not a match for the role  -{stats.rejected_irrelevant}")
    print(f"  older than {args.days} days        -{stats.rejected_stale}")
    print(f"  kept, date not published   {stats.undated_kept}")
    print(f"  not remote (hybrid/office) -{stats.rejected_not_remote}")
    print(f"  not eligible where you are -{stats.rejected_ineligible}")
    if stats.region_unknown:
        print(f"  region-restricted, no home {stats.region_unknown}  "
              f"(prospects — set --region)")
    print(f"  low-rate market           -{stats.rejected_low_rate_market}")
    print(f"  large employer            -{stats.rejected_large_employer}")
    floor = stats.salary_floor_usd or args.min_salary
    if floor:
        print(f"  below ${floor:,.0f} pay floor    -{stats.rejected_low_pay}")
    if stats.pay_unstated:
        print(f"  pay not published          {stats.pay_unstated}  "
              f"(prospects — your minimum can't be checked)")
    if stats.rejected_wrong_engagement:
        print(f"  wrong kind of engagement  -{stats.rejected_wrong_engagement}")
    print(f"  below match threshold     -{stats.rejected_low_score}")
    print(f"  closed / expired adverts  -{stats.rejected_expired}")
    print(f"  duplicates removed        -{stats.duplicates_removed}")
    if stats.llm_ran:
        print(f"  cleared from prospects    +{stats.llm_promoted}")
        if stats.rejected_low_chance:
            print(f"  low chance against your CV -{stats.rejected_low_chance}")
    print(f"  QUALIFIED                  {stats.qualified}")


def print_summary(result, stats) -> None:
    """What was found, how it splits, and the best of it."""
    print()
    print("Breakdown")
    print(f"  hot leads (>= {config.HOT_LEAD_SCORE})        {stats.hot_leads}")
    print(f"  new since last run         {stats.new_since_last_run}")
    print(f"  full time / part time      {stats.full_time} / {stats.part_time}")
    print(f"  contract / freelance       {stats.contract} / {stats.freelance}")
    print(f"  startups / partnerships    {stats.startups} / {stats.partnerships}")
    print(f"  prospects (unconfirmed)    {stats.prospects}")
    print(f"  companies to contact       {stats.companies}")

    if result.hot_leads:
        print()
        print("Top leads")
        for job in result.hot_leads[:8]:
            flag = "NEW" if job.is_new else "   "
            print(f"  {job.match_score:3d} {flag} {job.title[:46]:46s} "
                  f"{job.company[:24]:24s} {job.job_age_label}")

    if stats.llm_ran:
        print()
        print("Judgement layer")
        print(f"  eligibility decided        {stats.llm_eligibility_calls} "
              f"({stats.llm_promoted} cleared, {stats.llm_confirmed_ineligible} ruled out)")
        print(f"  leads assessed for fit     {stats.llm_fit_calls}")
        print(f"  served from cache          {stats.llm_cache_hits}")
        print(f"  estimated cost             ${stats.llm_cost_usd:.2f}")

    failed = [s.name for s in stats.sources if not s.ok]
    if failed:
        print()
        print("Sources unavailable this run: " + ", ".join(failed))


def whole_days(text: str) -> int:
    """A freshness window argparse will not let go negative."""
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number of days") from None
    if value < 1:
        raise argparse.ArgumentTypeError("a freshness window must be at least 1 day")
    return value


def pay_floor(text: str) -> float:
    """A pay floor argparse will not let go negative."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an amount") from None
    if value < 0:
        raise argparse.ArgumentTypeError("a pay floor cannot be negative")
    return value


def compile_search(args):
    """The search to run, or the exit code to stop with.

    Stopping with 0 means the request was answered rather than searched.
    Stopping with 1 means no search could be built, so no report exists —
    a scheduled run needs to be able to tell those apart.
    """
    if not (args.query or args.cv or args.region):
        return None, None

    settled = profile.narrowed_standing(args.region, args.query, args.cv)
    if settled is not None:
        print(f"│ Search       : {profile.describe(settled)}")
        return settled, None

    cv_text = read_cv(args.cv) if args.cv else ""
    compiled = profile.compile_profile(args.query, cv_text, region=args.region)
    if compiled.profile is None:
        print()
        print(compiled.answer or "Nothing to search for.")
        for question in compiled.questions:
            print(f"  • {question}")
        return None, 1 if compiled.failed else 0

    print(f"│ Search       : {profile.describe(compiled.profile)}")
    if compiled.questions:
        print("│")
        print("│ Worth answering to sharpen this search:")
        for question in compiled.questions:
            print(f"│   • {question}")
    return compiled.profile, None


def interactive() -> bool:
    """Is a person watching this run, rather than a test or a scheduler?"""
    try:
        return sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def open_file(path: Path) -> None:
    """Show the finished workbook. A desktop that cannot open it is not an error."""
    opener = {"darwin": "open", "win32": "start"}.get(sys.platform, "xdg-open")
    with contextlib.suppress(OSError):
        subprocess.run([opener, str(path)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def report_label(search) -> str:
    """A readable filename stem: "Plumbing Jobs", "iOS Jobs"."""
    words = re.findall(r"[A-Za-z0-9+#]+", search.label or search.query or "Job")
    return " ".join(words[:4] or ["Job"]) + " Jobs"


def cmd_daily(args: argparse.Namespace) -> int:
    chosen = "quick" if getattr(args, "quick", False) else getattr(args, "tier", "normal")
    with config.tier(chosen):
        return _run_daily(args)


def reject_unknown_sources(only: tuple[str, ...]) -> int | None:
    """1 when `--sources` names a connector that does not exist, else None.

    A mistyped name matched nothing and searched nothing, which read as a
    quiet empty run rather than the typo it was.
    """
    known = known_source_names()
    unknown = [name for name in only if name not in known]
    if not unknown:
        return None
    print(f"No such source: {', '.join(unknown)}")
    print(f"  Available: {', '.join(sorted(known))}")
    return 1


def _run_daily(args: argparse.Namespace) -> int:
    started = datetime.now()

    only = tuple(s.strip() for s in (args.sources or "").split(",") if s.strip())
    stop = reject_unknown_sources(only)
    if stop is not None:
        return stop
    config.LINKEDIN_DEEP = bool(getattr(args, "deep", False))
    args.days = profile.freshness_window_days(args.days, args.query or "")

    llm_ready, llm_why = llm.available()
    print_header(args, llm_ready, llm_why)

    search_profile, stop = compile_search(args)
    if stop is not None:
        return stop

    if getattr(args, "small_only", False):
        from dataclasses import replace
        base = search_profile or profile.active()
        search_profile = replace(base, small_employers_only=True)

    # Offer the keys this region could use, before the run rather than after
    # it. Skipping is always allowed and never blocks the search.
    if interactive() and not getattr(args, "no_prompt", False):
        country = (search_profile or profile.active()).home_country
        prompt_for_keys(platforms.needing_keys(country))

    if not (search_profile or profile.active()).core_terms:
        print()
        print("I do not know what kind of work to look for.")
        print()
        print("  Say what you want:   jobfinder daily --query \"electrician jobs in Leeds\"")
        print("  Or hand me a CV:     jobfinder daily --cv ~/cv.pdf")
        print()
        print(f"  To make a search the default for every run, add a "
              f"\"default_search\" block to\n  {profile.LOCAL_CANDIDATE_FILE}")
        return 1

    if args.no_cache:
        cache.ENABLED = False

    result = run_pipeline(offline=args.offline, only=only, days=args.days,
                          min_salary_usd=args.min_salary,
                          require_salary=args.require_salary,
                          skip_low_rate_markets=not args.allow_low_rate_markets,
                          verify_live=not args.no_verify,
                          use_llm=not args.no_llm,
                          search_profile=search_profile)
    stats = result.stats

    stamp = (stats.run_at or now_local()).strftime("%Y-%m-%d %H%M")
    out_dir = Path(args.output_dir).expanduser() if args.output_dir else config.output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    label = report_label(search_profile or profile.active())
    prefix = f"DEMO FIXTURES {label}" if args.offline else label
    base = out_dir / f"{prefix} {stamp}"

    xlsx = build_workbook(result, base.with_suffix(".xlsx"))
    html = build_html(result, base.with_suffix(".html"))
    csv_path = write_csv(result, base.with_suffix(".csv"))
    json_path = write_json(result, base.with_suffix(".json"))

    print()
    print_funnel(stats, args)
    print_summary(result, stats)

    print()
    print("Reports")
    for path in (xlsx, html, csv_path, json_path):
        print(f"  {path}")
    print(f"\nCompleted in {(datetime.now() - started).total_seconds():.1f}s")

    # Only when a person is watching. Tests, cron jobs and piped runs write
    # into temporary directories, and launching a viewer at one of those pops
    # an error dialog over whatever the user was actually doing.
    if not args.no_open and interactive():
        open_file(xlsx)

    return 0


def _mask(value: str) -> str:
    """Show enough of a secret to identify it, never enough to leak it."""
    if not value:
        return "(not set)"
    if len(value) <= 4:
        return "*" * len(value)
    return f"{'*' * (len(value) - 4)}{value[-4:]}  ({len(value)} chars)"


def cmd_sources(args: argparse.Namespace) -> int:
    print(f"{'source':<20} {'status':<14} description")
    print("-" * 78)
    hints = {
        "adzuna": "set ADZUNA_APP_ID and ADZUNA_APP_KEY",
        "reed": "set REED_API_KEY",
        "jooble": "set JOOBLE_API_KEY",
    }
    for name, label, configured in source_catalogue():
        status = "ready" if configured else "needs API key"
        note = label if configured else f"{label} — {hints.get(name, 'credentials missing')}"
        print(f"{name:<20} {status:<14} {note}")

    if not args.test:
        print("\nRestrict a run with:  python -m job_agent daily --sources remoteok,remotive")
        print("Verify your API keys:  python -m job_agent sources --test")
        return 0

    print("\nChecking API keys (this calls each provider once)")
    print("-" * 78)
    failures = 0
    for source in keyed_sources():
        for variable in source.credentials:
            print(f"  {variable:<18} = {_mask(os.getenv(variable, ''))}")
        ok, message = source.probe()
        if not ok and any(os.getenv(v) for v in source.credentials):
            failures += 1
        marker = "OK  " if ok else "-- "
        print(f"  {marker}{source.label}: {message}\n")

    if failures:
        print(f"{failures} configured source(s) failed. A rejected key is usually a typo, "
              "a swapped id/key pair, or a key that has not been activated yet.")
        return 1
    print("Keys that are set are working. Unset sources are skipped, not failed.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Explain exactly why one posting would be accepted or rejected."""
    raw = RawJob(
        source="manual",
        source_id="manual-1",
        title=args.title,
        company=args.company or "Unknown",
        url=args.url or "https://example.com/job",
        description=args.description or "",
        location_raw=args.location or "",
        posted_at=now_local(),
    )

    relevance = filters.check_relevance(raw)
    print(f"Relevance : {'PASS' if relevance.passed else 'REJECT — ' + relevance.reason}")
    for detail in relevance.details:
        print(f"            + {detail}")

    verdict = judge_arrangement(raw, profile.active().work_arrangement)
    print(f"Eligible  : {'PASS' if verdict.passed else 'REJECT — ' + verdict.reason}")
    print(f"            arrangement : {verdict.remote_status or '—'}")
    print(f"            eligibility : {verdict.eligibility or '—'}")
    for detail in verdict.details:
        print(f"            + {detail}")
    for concern in list(relevance.concerns) + list(verdict.concerns):
        print(f"            ! {concern}")
    if not verdict.passed and verdict.prospect_worthy:
        print("            → would be listed as a Prospect (ask before applying)")

    return 0 if (relevance.passed and verdict.passed) else 1


def prompt_for_keys(missing) -> tuple[str, ...]:
    """Offer to collect the credentials this search could use.

    Lives here rather than in the registry because it is user interface: it
    writes to the terminal and reads from it. The registry stays pure data, so
    importing it from the MCP server cannot print anything into the protocol
    stream.

    Every key is optional. Pressing enter skips that platform for good.
    """
    if not missing or not sys.stdin.isatty():
        return ()

    saved: list[str] = []
    print()
    print("Some platforms for this search have no key yet. Each is optional —")
    print("press enter to skip one and it will simply not be searched.")
    for platform in missing:
        print()
        print(f"  {platform.label}")
        if platform.note:
            print(f"    {platform.note}")
        if platform.signup:
            print(f"    get a key: {platform.signup}")
        for name in platform.missing():
            try:
                value = getpass.getpass(f"    {name} (enter to skip): ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return tuple(saved)
            if not value:
                continue
            path = platforms.save_key(name, value)
            os.environ[name] = value
            saved.append(name)
            print(f"    saved {name} to {path}")
    return tuple(saved)


def cmd_platforms(args: argparse.Namespace) -> int:
    """Show what discovery proposes for a country, so it can be reviewed."""
    from . import discovery
    country = args.region.strip() or profile.active().home_country
    if not country:
        print("Name a country: jobfinder platforms --region Nigeria")
        return 1

    trade = args.trade.strip() or profile.active().label
    found = discovery.discover(country, trade)
    print()
    print(f"Regional platforms for {country}" + (f" — {trade}" if trade else ""))
    if not found:
        print("  none discovered. The judgement layer may be unavailable, or the")
        print("  model did not recognise this market.")
        return 0
    for candidate in found:
        key = " (needs a key)" if candidate.needs_key else ""
        print(f"  {candidate.label:28} {candidate.kind:7} {candidate.country}{key}")
        print(f"    {candidate.url_template}")
    print()
    print("Every URL above passed validation: HTTPS, a public address, standard")
    print("port, no embedded credentials, and a {query} placeholder.")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Every platform for this region, grouped by what it takes to reach it."""
    country = args.region.strip() or profile.active().home_country
    groups = platforms.by_access(country)

    print()
    print(f"Job platforms{f' for {country}' if country else ''}")
    if not country:
        print("  no region set — narrow this with --region \"Pakistan\"")

    _print_group("Working now, no key needed", groups[platforms.FREE])
    _print_group("Free key, a few minutes to set up", groups[platforms.FREE_KEY])
    _print_group("Partner key, approval needed", groups[platforms.PARTNER])

    missing = platforms.needing_keys(country)
    free_keys = [p for p in missing if p.access == platforms.FREE_KEY]
    partner = [p for p in missing if p.access == platforms.PARTNER]

    print()
    print("Where this stands")
    print(f"  {len(groups[platforms.FREE])} platforms work right now with no setup at all.")
    if free_keys:
        print(f"  {len(free_keys)} more need a free key you can get yourself in minutes.")
    if partner:
        print(f"  {len(partner)} need a partner key. The big national boards block ordinary")
        print("  requests by design, so this is the difference between a free MVP and")
        print("  a commercial product.")
        print()
        print("  The shortcut: one SerpApi key reaches Indeed, Glassdoor, Bayt, Naukri,")
        print("  Rozee and the rest through Google's job index, in every country, without")
        print("  applying to any of them individually. Free monthly allowance to start.")
    print()
    print("  Nothing here is required. Every missing key means one platform is")
    print("  skipped and named in the run's funnel, never a failed search.")
    if missing:
        print()
        print(f"  Keys go in {config.ROOT / '.env'}, which is git-ignored.")
    return 0


def _print_group(heading: str, entries) -> None:
    if not entries:
        return
    print()
    print(f"  {heading}")
    for platform in entries:
        if platform.env and platform.configured:
            state = "configured"
        elif platform.env:
            state = "not set"
        else:
            state = ""
        print(f"    {platform.label:38} {state}")
        if platform.blocked:
            print(f"      blocked: {platform.blocked}")
        if platform.note:
            print(f"      {platform.note}")
        if platform.signup and not platform.configured:
            print(f"      {platform.signup}")


def cmd_status(_: argparse.Namespace) -> int:
    data = storage.stats()
    entries, oldest_days = cache.stats()
    print(f"Vacancies tracked : {data['total_tracked']}")
    print(f"Marked as applied : {data['applied']}")
    print(f"Descriptions cached: {entries}"
          + (f"  (oldest {oldest_days:.1f} days)" if entries else "")
          + "  — cached descriptions are re-read for free next run")
    reports = sorted(config.REPORTS_DIR.glob("*_jobs_*.xlsx"))
    print(f"Reports on disk   : {len(reports)}")
    for path in reports[-5:]:
        print(f"  {path.name}  ({path.stat().st_size // 1024} KB)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true",
                        help="show each step as it runs")
    common.add_argument("-q", "--quiet", action="store_true",
                        help="warnings and errors only")

    parser = argparse.ArgumentParser(
        prog="python -m job_agent",
        description="Find work you are eligible for, anywhere, from a free-text request.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", parser_class=lambda **kw: argparse.ArgumentParser(
        parents=[common], **kw))

    daily = sub.add_parser("daily", help="run the full search and write all reports")
    daily.add_argument("--offline", action="store_true",
                       help="use the bundled fixture corpus instead of live sources")
    daily.add_argument("--days", type=whole_days, default=0,
                       help="freshness window in calendar days (default: read from "
                            f"--query, else {config.FRESHNESS_DAYS})")
    daily.add_argument("--deep", action="store_true",
                       help="sweep every LinkedIn keyword against every region, three pages "
                            "deep (roughly triples the LinkedIn stage)")
    daily.add_argument("--sources", default="",
                       help="comma-separated source names to restrict the run to")
    daily.add_argument("--min-salary", type=pay_floor, default=config.SALARY_FLOOR_USD,
                       metavar="USD",
                       help="minimum annual pay in USD equivalent. No floor unless "
                            "you set one here or state one in the query")
    daily.add_argument("--require-salary", action="store_true",
                       help="also reject roles that publish no pay at all")
    daily.add_argument("--allow-low-rate-markets", action="store_true",
                       help="do not skip roles scoped to low-rate markets")
    daily.add_argument("--small-only", action="store_true",
                       help="only small employers — startups, scale-ups and mid-size "
                            "firms. Off by default: employers of every size are "
                            "searched and smaller ones simply rank higher")
    daily.add_argument("--query", default="", metavar="TEXT",
                       help='what to search for — "electrician in Leeds", "AI engineer", '
                            '"react developer in Germany". Required unless '
                            'candidate.local.json sets a default_search.')
    daily.add_argument("--region", default="", metavar="PLACE",
                       help='where you want to work — "USA", "UK, Germany", '
                            '"Australia". Omitted, it is read from your CV; if '
                            'that is silent too, only worldwide roles qualify.')
    daily.add_argument("--cv", default="", metavar="PATH",
                       help="path to a CV (.txt, .md or .pdf). Rankings become "
                            "personal to that CV, favouring roles with a real chance.")
    daily.add_argument("--tier", choices=("quick", "normal", "deep"), default="normal",
                       help="how hard to look. quick: existing APIs only. "
                            "normal (default): every board, plus employer boards "
                            "discovered from what the run collects. deep: also "
                            "regional platform discovery, employer contact "
                            "lookup and company size — many more requests")
    daily.add_argument("--quick", action="store_true",
                       help="shorthand for --tier quick")
    daily.add_argument("--no-llm", action="store_true",
                       help="skip the Claude judgement layer and run rules-only")
    daily.add_argument("--no-cache", action="store_true",
                       help="judge every advert afresh instead of reusing a "
                            "stored verdict; slower, and costs API calls")
    daily.add_argument("--no-verify", action="store_true",
                       help="skip re-opening each advert to confirm it is still live")
    daily.add_argument("--output-dir", default="", metavar="DIR",
                       help="where to write the reports (default: the Desktop, or "
                            "JOBFINDER_OUTPUT_DIR if set)")
    daily.add_argument("--no-prompt", action="store_true",
                       help="never ask for a missing API key; skip those platforms")
    daily.add_argument("--no-open", action="store_true",
                       help="do not open the workbook when the run finishes")
    daily.set_defaults(func=cmd_daily)

    listing = sub.add_parser("sources", help="list available sources")
    listing.add_argument("--test", action="store_true",
                         help="live-check every configured API key")
    listing.set_defaults(func=cmd_sources)

    check = sub.add_parser("check", help="explain the filter verdict for a single posting")
    check.add_argument("--title", required=True, metavar="TITLE",
                       help="the advert's job title")
    check.add_argument("--company", default="", metavar="NAME",
                       help="the employer, when the advert names one")
    check.add_argument("--location", default="", metavar="PLACE",
                       help="where the advert says the work is")
    check.add_argument("--description", default="", metavar="TEXT",
                       help="the advert body, as much of it as you have")
    check.add_argument("--url", default="", metavar="LINK",
                       help="the advert's address, used to judge how you would apply")
    check.set_defaults(func=cmd_check)

    status = sub.add_parser("status", help="show tracker and report status")
    status.set_defaults(func=cmd_status)

    plat = sub.add_parser("platforms",
                          help="regional platforms discovered for a country")
    plat.add_argument("--region", default="", metavar="PLACE")
    plat.add_argument("--trade", default="", metavar="WORK",
                      help="the kind of work, when it differs from your standing search")
    plat.set_defaults(func=cmd_platforms)

    setup = sub.add_parser("setup", help="every API key, and which ones your region needs")
    setup.add_argument("--region", default="", metavar="PLACE",
                       help='only platforms serving this place — "Nigeria", "India"')
    setup.set_defaults(func=cmd_setup)

    return parser


def _use_utf8_output() -> None:
    """Let the rules and arrows survive a console that defaults to cp1252.

    Windows consoles do, and every header this tool prints opens with a box
    rule, so the first line of output would raise UnicodeEncodeError before
    any work began. `errors="replace"` keeps a stream that cannot be
    reconfigured from taking the run down with it.
    """
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, OSError, ValueError):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _use_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.quiet)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
