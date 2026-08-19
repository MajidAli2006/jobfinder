"""MCP server — the job finder as a tool any MCP client can call."""

from __future__ import annotations

import logging
import sys

from . import config, llm, platforms, profile
from .pipeline import run as run_pipeline
from .report_excel import build_workbook
from .__main__ import report_label
from .report_html import build_html
from .sources import source_catalogue
from .utils import now_local

log = logging.getLogger("job_agent.mcp")

try:
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # pragma: no cover - depends on the installed SDK
    from mcp.server.fastmcp import FastMCP as _Server

INSTRUCTIONS = (
    "Finds remote jobs and ranks them by how likely the person is to actually "
    "get a reply. Call check_setup first on a fresh install. Use preview_search "
    "to confirm a request compiled sensibly before calling find_jobs, which "
    "takes several minutes and produces an Excel workbook."
)


def _build_server():
    """Construct the server against whichever SDK generation is installed."""
    import inspect

    accepted = set(inspect.signature(_Server.__init__).parameters)
    kwargs = {"name": "job-finder"}
    for key, value in (("version", "1.0.0"), ("instructions", INSTRUCTIONS)):
        if key in accepted:
            kwargs[key] = value
    try:
        return _Server(**kwargs)
    except TypeError:
        return _Server("job-finder")


server = _build_server()


def _cv_from(cv_text: str, cv_path: str) -> str:
    """Accept a CV as pasted text or as a path, and say clearly when neither works."""
    if cv_text and cv_text.strip():
        return cv_text
    if cv_path:
        from .__main__ import read_cv
        return read_cv(cv_path)
    return ""


@server.tool()
def check_setup() -> str:
    """Report what is configured and what is missing. Call this first on a new install."""
    lines = ["# Job finder setup", ""]

    ok, why = llm.available()
    lines += [
        f"**Claude layer:** {'ready — ' + why if ok else 'NOT available — ' + why}",
        "",
    ]
    if not ok:
        lines += [
            "Without it a request cannot be compiled into a search, so nothing",
            "runs. Set `ANTHROPIC_API_KEY` in the `.env` file next to the",
            "project, or in the MCP server's environment.",
            "",
        ]

    catalogue = source_catalogue()
    ready = [label for _, label, on in catalogue if on]
    lines += [f"**Connectors ready:** {len(ready)} of {len(catalogue)}", ""]

    groups = platforms.by_access(profile.active().home_country)
    lines += [
        f"**Working now, no key:** {len(groups[platforms.FREE])} platforms — "
        "LinkedIn, employer ATS boards, the remote boards, Hacker News, and any "
        "regional board that publishes job markup.",
        "",
    ]

    free_missing = [p for p in groups[platforms.FREE_KEY] if not p.configured]
    if free_missing:
        lines += ["**Free keys not yet set** (minutes to obtain):"]
        lines += [f"- {p.label} — {p.signup}" for p in free_missing]
        lines += [""]

    partner_missing = [p for p in groups[platforms.PARTNER] if not p.configured]
    if partner_missing:
        lines += [
            "**Partner keys** — the big national boards block ordinary requests "
            "by design, so reaching them means applying, and sometimes paying:",
        ]
        lines += [f"- {p.label} — {p.blocked or 'partner access'} — {p.signup}"
                  for p in partner_missing]
        lines += [""]

    lines += [
        "No key is required. Each missing one means that platform is skipped and "
        "named in the run's funnel, never a failed search.",
        "",
        f"**Reports are written to:** `{config.output_dir()}`",
    ]
    return "\n".join(lines)


@server.tool()
def list_platforms(region: str = "") -> str:
    """Job platforms serving a country, and what each one needs to be reachable."""
    country = region.strip() or profile.active().home_country
    if not country:
        return "Name a country, for example region=\"Pakistan\"."

    groups = platforms.by_access(country)
    lines = [f"# Job platforms for {country}", ""]
    for level in (platforms.FREE, platforms.FREE_KEY, platforms.PARTNER):
        entries = groups[level]
        if not entries:
            continue
        lines += [f"## {platforms.ACCESS_LABEL[level]}", ""]
        for platform in entries:
            state = ""
            if platform.env:
                state = " — configured" if platform.configured else " — not set"
            lines.append(f"- **{platform.label}**{state}")
            if platform.blocked:
                lines.append(f"  - blocked: {platform.blocked}")
            if platform.note:
                lines.append(f"  - {platform.note}")
            if platform.signup and not platform.configured:
                lines.append(f"  - {platform.signup}")
        lines.append("")
    return "\n".join(lines)


@server.tool()
def preview_search(query: str, cv_text: str = "", cv_path: str = "",
                   region: str = "") -> str:
    """Show what a search request compiles to, without fetching any jobs."""
    result = profile.compile_profile(query, _cv_from(cv_text, cv_path), region=region)
    if result.profile is None:
        return result.answer or "Nothing to search for."
    compiled = result.profile

    where = (", ".join(compiled.target_regions) if compiled.target_regions
             else compiled.home_country or "not determined — worldwide roles only")
    questions = ""
    if result.questions:
        questions = "\n".join(
            ["", "**Worth answering before you run this:**", ""]
            + [f"- {q}" for q in result.questions]
            + ["", "The search will still run without an answer — these only sharpen it."]
        )

    return "\n".join([
        f"# {compiled.label}",
        "",
        f"- **Core terms:** {', '.join(compiled.core_terms)}",
        f"- **Also matches:** {', '.join(compiled.secondary_terms[:10])}",
        f"- **Board queries:** {', '.join(compiled.search_queries)}",
        f"- **Excludes titles naming:** {', '.join(compiled.other_discipline_terms[:12])}",
        f"- **Skills weighted:** {len(compiled.skills)} "
        f"(top: {', '.join(sorted(compiled.skills, key=compiled.skills.get, reverse=True)[:6])})",
        f"- **Region:** {where}",
        f"- **Pay floor:** ${compiled.salary_floor_usd:,.0f}"
        if compiled.salary_floor_usd else "- **Pay floor:** none set",
        f"- **Ranked against a CV:** {'yes' if compiled.has_cv else 'no'}",
        "",
        "If the core terms or exclusions look wrong, rephrase the request — that "
        "vocabulary is what the whole search runs on.",
        questions,
    ])


@server.tool()
def find_jobs(query: str = "", cv_text: str = "", cv_path: str = "",
              region: str = "", days: int = 30, min_salary_usd: float = 0.0,
              tier: str = "normal", offline: bool = False) -> str:
    """Search every configured job board and produce a ranked Excel workbook."""
    with config.tier(tier if tier in config.TIERS else "normal"):
        return _find_jobs(query, cv_text, cv_path, region, days,
                          min_salary_usd, offline, tier)


def _settle_search(query: str, cv: str, region: str):
    """(profile, questions, refusal) for a request.

    A refusal is a message to hand back unchanged; a profile of None with no
    refusal means run whatever search is already standing.
    """
    narrowed = profile.narrowed_standing(region, query, cv)
    if narrowed is not None:
        return narrowed, (), None
    if not (query or cv or region):
        return None, (), None
    result = profile.compile_profile(query, cv, region=region)
    if result.profile is None:
        return None, (), result.answer or "Nothing to search for."
    return result.profile, result.questions, None


def _find_jobs(query: str, cv_text: str, cv_path: str, region: str, days: int,
               min_salary_usd: float, offline: bool, tier: str) -> str:
    cv = _cv_from(cv_text, cv_path)
    compiled, questions, refusal = _settle_search(query, cv, region)
    if refusal:
        return refusal

    result = run_pipeline(
        offline=offline,
        days=max(1, min(120, days)),
        min_salary_usd=min_salary_usd or config.SALARY_FLOOR_USD,
        search_profile=compiled,
    )
    stats = result.stats

    out_dir = config.output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_local().strftime("%Y-%m-%d %H%M")
    base = out_dir / f"{report_label(compiled or profile.active())} {stamp}"
    xlsx = build_workbook(result, base.with_suffix(".xlsx"))
    build_html(result, base.with_suffix(".html"))

    lines = [
        f"# {stats.profile_label} — {stats.qualified} matching roles",
        "" if tier != "quick" else
        "_Quick run — fewer adverts fetched, so more roles sit in Prospects. "
        "Run `jobfinder daily --query \"...\"` from a terminal for full coverage._",
        "",
        f"Searched {stats.sources_searched} sources over the last {days} days, "
        f"working from {stats.profile_home}."
        + (" Ranked against the CV you supplied." if cv else ""),
        "",
        f"- {stats.raw_found:,} postings read",
        f"- {stats.qualified} qualified, {stats.hot_leads} of them strong",
        f"- {stats.new_since_last_run} new since the last run",
        f"- {stats.prospects} prospects still needing a human check",
    ]
    if stats.llm_ran:
        lines.append(
            f"- Claude cleared {stats.llm_promoted} prospects and ruled out "
            f"{stats.llm_confirmed_ineligible} (${stats.llm_cost_usd:.2f})"
        )

    if result.hot_leads:
        lines += ["", "## Best leads", ""]
        for job in result.hot_leads[:10]:
            verdict = f" — _{job.llm_verdict}_" if job.llm_verdict else ""
            lines.append(
                f"{job.match_score}. **{job.title}** at {job.company} "
                f"({job.job_age_label}){verdict}"
            )
            lines.append(f"   {job.application_url or job.original_job_url}")

    failed = [s.name for s in stats.sources if not s.ok]
    if failed:
        lines += ["", f"_Unavailable this run: {', '.join(failed)}_"]

    if questions:
        lines += ["", "## Worth answering", ""]
        lines += [f"- {q}" for q in questions]
        lines.append("")
        lines.append("Answering these and re-running would sharpen the region filter.")

    lines += ["", f"**Workbook:** `{xlsx}`"]
    return "\n".join(lines)


def main() -> None:
    """Entry point for `python -m job_agent.mcp_server` and the console script."""
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr, force=True)
    server.run("stdio")


if __name__ == "__main__":
    main()
