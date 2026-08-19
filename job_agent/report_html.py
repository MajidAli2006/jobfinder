"""Self-contained HTML dashboard."""

from __future__ import annotations

import html
from datetime import date
from pathlib import Path

from . import config, profile
from .models import Job
from .pipeline import RunResult
from .utils import local_timezone, safe_url

CSS = """
:root{--bg:#f6f7fb;--card:#fff;--ink:#141821;--muted:#5d6677;--line:#e3e7ef;
--accent:#1f3864;--hot:#b8860b;--hotbg:#fff6dd;--good:#0b7a3b;--goodbg:#e7f7ed;
--warn:#9a3412;--warnbg:#fdeee2;--new:#1d4ed8;--newbg:#e8efff}
@media(prefers-color-scheme:dark){:root{--bg:#0f1218;--card:#161b24;--ink:#e8ecf3;
--muted:#9aa4b6;--line:#252c39;--accent:#8ab4f8;--hot:#f5c451;--hotbg:#2c2510;
--good:#6ee7a8;--goodbg:#0f2a1c;--warn:#fca97a;--warnbg:#2e1a10;--new:#93b4ff;--newbg:#131c33}}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px 64px;background:var(--bg);color:var(--ink);
font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
.wrap{max-width:1400px;margin:0 auto}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}
.sub{color:var(--muted);margin:0 0 28px;font-size:14px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:32px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.card .n{font-size:26px;font-weight:650;letter-spacing:-.02em}
.card .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-top:2px}
.card.hot .n{color:var(--hot)}
h2{font-size:18px;margin:34px 0 12px;padding-bottom:8px;border-bottom:2px solid var(--line)}
.tools{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px}
input[type=search]{flex:1;min-width:220px;padding:9px 12px;border:1px solid var(--line);
border-radius:9px;background:var(--card);color:var(--ink);font-size:14px}
.tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--card)}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{position:sticky;top:0;background:var(--accent);color:#fff;text-align:left;
padding:10px 12px;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em;white-space:nowrap}
@media(prefers-color-scheme:dark){th{background:#1b2433;color:var(--accent)}}
td{padding:10px 12px;border-top:1px solid var(--line);vertical-align:top}
tr.hot td{background:var(--hotbg)}
tr.prospect td{background:var(--warnbg)}
.score{font-weight:700;font-variant-numeric:tabular-nums}
.score.high{color:var(--hot)}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:11px;
font-weight:600;white-space:nowrap}
.pill.new{background:var(--newbg);color:var(--new)}
.pill.fresh{background:var(--goodbg);color:var(--good)}
.pill.type{background:var(--line);color:var(--muted)}
.pill.lvl-beginner{background:#e2efda;color:#375623}
.pill.lvl-medium{background:#ddebf7;color:#1f4e79}
.pill.lvl-senior{background:#e4dfec;color:#4a2e7a}
.pill.lvl-not-specified{background:var(--line);color:var(--muted)}
@media(prefers-color-scheme:dark){.pill.lvl-beginner{background:#16301f;color:#8fd6a6}
.pill.lvl-medium{background:#13233a;color:#8fbaf0}
.pill.lvl-senior{background:#241b38;color:#c3aaf0}}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.reasons{color:var(--muted);font-size:12.5px;max-width:380px}
.concerns{color:var(--warn);font-size:12.5px;max-width:280px}
.title{font-weight:600;display:block;margin-bottom:2px}
.co{color:var(--muted);font-size:12.5px}
.empty{padding:22px;color:var(--muted);font-style:italic}
footer{margin-top:40px;color:var(--muted);font-size:12px;border-top:1px solid var(--line);padding-top:16px}
.rules{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 20px}
.rules li{margin:5px 0;color:var(--muted);font-size:13px}
"""

JS = """
document.querySelectorAll('input[data-target]').forEach(function(box){
  box.addEventListener('input', function(){
    var q = box.value.toLowerCase();
    document.querySelectorAll('#'+box.dataset.target+' tbody tr').forEach(function(tr){
      tr.style.display = tr.textContent.toLowerCase().indexOf(q) === -1 ? 'none' : '';
    });
  });
});
"""


def _e(value) -> str:
    return html.escape(str(value or ""))


def _money(job: Job) -> str:
    symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get((job.salary_currency or "").upper(),
                                                      (job.salary_currency or "") + " ")
    if job.day_rate_min:
        top = f"–{symbol}{job.day_rate_max:,.0f}" if job.day_rate_max and job.day_rate_max != job.day_rate_min else ""
        return f"{symbol}{job.day_rate_min:,.0f}{top} /day"
    if job.salary_min:
        top = f"–{symbol}{job.salary_max:,.0f}" if job.salary_max and job.salary_max != job.salary_min else ""
        return f"{symbol}{job.salary_min:,.0f}{top}"
    return "—"


def _row(job: Job) -> str:
    classes = []
    if job.match_score >= config.HOT_LEAD_SCORE:
        classes.append("hot")
    if job.is_prospect:
        classes.append("prospect")

    pills = []
    if job.is_new:
        pills.append('<span class="pill new">NEW</span>')
    if job.job_age_days is not None and job.job_age_days <= 1:
        pills.append(f'<span class="pill fresh">{_e(job.job_age_label)}</span>')
    pills.append(f'<span class="pill type">{_e(job.employment_type)}</span>')

    apply_href = safe_url(job.application_url)
    apply_link = (f'<a href="{_e(apply_href)}" target="_blank" rel="noopener">Apply</a>'
                  if apply_href else _e(job.application_url) or "—")
    contact = job.public_email and (
        f'<a href="mailto:{_e(job.public_email)}">{_e(job.public_email)}</a>') or "—"
    if job.best_contact_name:
        contact = f"{_e(job.best_contact_name)}<br>{contact}"

    score_class = "score high" if job.match_score >= config.HOT_LEAD_SCORE else "score"

    return f"""<tr class="{' '.join(classes)}">
<td class="{score_class}">{job.match_score}</td>
<td class="score">{job.networking_score}</td>
<td>{_e(job.posted_date)}<br>{' '.join(pills)}</td>
<td><span class="title">{_e(job.title)}</span><span class="co">{_e(job.company)}</span></td>
<td>{_e(job.remote_status)}<br><span class="co">{_e(job.eligibility)}</span></td>
<td>{_money(job)}</td>
<td><span class="pill lvl-{_e(job.experience_level.lower().replace(' ','-'))}">{_e(job.experience_level)}</span>
<br><span class="co">{_e(job.seniority)} · {_e(job.industry)}</span></td>
<td class="reasons">{_e(' • '.join(job.match_reasons[:4]))}</td>
<td class="concerns">{_e(' • '.join(job.concerns[:3])) or '—'}</td>
<td>{contact}</td>
<td>{apply_link}</td>
</tr>"""


def _table(table_id: str, jobs: list[Job], empty: str) -> str:
    if not jobs:
        return f'<div class="tablewrap"><p class="empty">{_e(empty)}</p></div>'
    headers = ("Match", "Net", "Posted", "Role", "Remote / eligibility", "Pay",
               "Level / Industry", "Why it fits", "Concerns", "Contact", "Link")
    head = "".join(f"<th>{h}</th>" for h in headers)
    body = "\n".join(_row(job) for job in jobs)
    return (f'<div class="tools"><input type="search" data-target="{table_id}" '
            f'placeholder="Filter {len(jobs)} rows…"></div>'
            f'<div class="tablewrap"><table id="{table_id}"><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def build_html(result: RunResult, path: Path) -> Path:
    stats = result.stats
    run_date = stats.run_at.date() if stats.run_at else date.today()

    search = profile.active()
    where = search.home_country or "your location"
    headline = search.label
    if search.remote_only:
        headline = f"Remote {headline} opportunities"
    if search.home_country:
        headline = f"{headline} — eligible from {search.home_country}"

    if search.remote_only:
        remote_rule = (
            "<li><strong>Remote only.</strong> Hybrid, on-site, office-based and any required "
            f"weekly or monthly office attendance are rejected outright — "
            f"{stats.rejected_not_remote} rejected this run.</li>")
    else:
        remote_rule = (
            "<li><strong>Remote and on-site.</strong> Both are included, because remote was "
            "not asked for. On-site roles are judged on whether they are within reach of "
            f"{_e(where)}.</li>")

    cards = [
        ("Qualified", stats.qualified, ""),
        ("Hot leads", stats.hot_leads, "hot"),
        ("New today", stats.new_since_last_run, ""),
        ("Full time", stats.full_time, ""),
        ("Contract", stats.contract, ""),
        ("Freelance", stats.freelance, ""),
        ("Startups", stats.startups, ""),
        ("Partnerships", stats.partnerships, ""),
        ("Prospects", stats.prospects, ""),
        ("Beginner level", stats.level_beginner, ""),
        ("Medium level", stats.level_medium, ""),
        ("Senior level", stats.level_senior, ""),
    ]
    card_html = "".join(
        f'<div class="card {cls}"><div class="n">{value}</div><div class="l">{_e(label)}</div></div>'
        for label, value, cls in cards
    )

    sections = [
        ("Hot leads — score ≥ 85", "hotleads", result.hot_leads,
         "Nothing scored 85 or above in this run."),
        ("All qualified jobs", "allqualified", result.qualified,
         f"No qualifying {search.label} roles were found in this window."),
        ("Contract & freelance", "contractfreelance", result.contract + result.freelance,
         "No contract or freelance opportunities in this run."),
        ("Startups", "startups", result.startups, "No startup roles in this run."),
        ("Partnerships", "partnerships", result.partnerships,
         "No agency/partnership opportunities in this run."),
        ("Prospects — eligibility unconfirmed", "prospects", result.prospects,
         "No borderline leads worth a speculative approach."),
    ]
    section_html = "\n".join(
        f"<h2>{_e(title)}</h2>{_table(table_id, jobs, empty)}"
        for title, table_id, jobs, empty in sections
    )

    failed = [s for s in stats.sources if not s.ok]
    source_note = ""
    if failed:
        source_note = ("<p class=\"sub\">Sources unavailable this run: "
                       + _e(", ".join(s.name for s in failed)) + "</p>")

    document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(search.label)} jobs — {run_date}</title>
<style>{CSS}</style></head>
<body><div class="wrap">
<h1>{_e(headline)}</h1>
<p class="sub">Run {_e(run_date)} · window {_e(stats.period_start)} → {_e(stats.period_end)} ({_e(local_timezone().key)})
· {stats.raw_found} raw postings screened from {stats.sources_searched} sources</p>
{source_note}
<div class="cards">{card_html}</div>
{section_html}
<h2>How this list was filtered</h2>
<div class="rules"><ul>
{remote_rule}
<li><strong>Eligibility must be proven,</strong> not assumed: {_e(where)} named,
worldwide/anywhere, a region with {_e(where)} explicitly included, or a company that hires
international contractors — {stats.rejected_ineligible} rejected this run.</li>
<li><strong>Last {stats.freshness_days} calendar days</strong> in {_e(local_timezone().key)}
— {stats.rejected_stale} rejected as too old.</li>
<li><strong>Duplicates collapsed</strong> across boards — {stats.duplicates_removed} removed.</li>
<li>Anything marked <em>Prospect</em> is <strong>not</strong> confirmed eligible; ask the
company before investing time in an application.</li>
</ul></div>
<footer>Generated by the job finder · verify eligibility and contract status
with the employer before applying.</footer>
</div><script>{JS}</script></body></html>"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")
    return path
