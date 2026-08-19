"""CSV and JSON exports — every field, unlike the workbook."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, fields
from datetime import date, datetime
from pathlib import Path

from .models import Job
from .pipeline import RunResult

CSV_FIELDS = [f.name for f in fields(Job)]


def _cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return " • ".join(str(v) for v in value if v)
    return str(value)


def row_for(job: Job) -> dict[str, str]:
    data = job.to_dict()
    return {name: _cell(data.get(name)) for name in CSV_FIELDS}


#: Leading characters a spreadsheet reads as the start of a formula.
FORMULA_LEADERS = ("=", "+", "-", "@")


def defuse(value):
    """Text a spreadsheet will show rather than evaluate.

    A CSV is usually opened in Excel, which reads a leading "=", "+", "-" or
    "@" as the start of a formula, and this file is full of third-party text.

    Two things are deliberately left alone, because quoting them would corrupt
    ordinary content for no safety gain: a plain negative number, and a "+" or
    "-" that begins prose rather than an expression — advert descriptions
    routinely open with a bulleted "- Qualifications". An "=" or "@" is never
    prose, so it is always quoted.
    """
    if not isinstance(value, str) or not value.startswith(FORMULA_LEADERS):
        return value
    if value[0] in "+-":
        if value[1:2].isspace():
            return value
        try:
            float(value)
        except ValueError:
            pass
        else:
            return value
    return "'" + value


def defused_row(row: dict) -> dict:
    return {key: defuse(value) for key, value in row.items()}


def write_csv(result: RunResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = CSV_FIELDS + ["Sheet"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for job in result.qualified:
            row = row_for(job)
            row["Sheet"] = "Hot Lead" if job in result.hot_leads else "Qualified"
            writer.writerow(defused_row(row))
        for job in result.prospects:
            row = row_for(job)
            row["Sheet"] = "Prospect"
            writer.writerow(defused_row(row))
        for job in result.long_shots:
            row = row_for(job)
            row["Sheet"] = "Long Shot"
            writer.writerow(defused_row(row))
    return path


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def write_json(result: RunResult, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": result.stats.run_at.isoformat() if result.stats.run_at else None,
        "search_period": {
            "start": result.stats.period_start,
            "end": result.stats.period_end,
            "timezone": "Europe/London",
        },
        "summary": asdict(result.stats),
        "counts": {
            "qualified": len(result.qualified),
            "hot_leads": len(result.hot_leads),
            "full_time": len(result.full_time),
            "part_time": len(result.part_time),
            "contract": len(result.contract),
            "freelance": len(result.freelance),
            "startups": len(result.startups),
            "partnerships": len(result.partnerships),
            "prospects": len(result.prospects),
            "long_shots": len(result.long_shots),
        },
        "hot_leads": [job.to_dict() for job in result.hot_leads],
        "qualified_jobs": [job.to_dict() for job in result.qualified],
        "prospects": [job.to_dict() for job in result.prospects],
        "long_shots": [job.to_dict() for job in result.long_shots],
        "companies": result.companies,
        "rejected": [
            {
                "title": job.title,
                "company": job.company,
                "location": job.location,
                "url": job.original_job_url,
                "posted_date": job.posted_date,
                "reason": job.rejection_reason,
                "category": job.rejection_category,
            }
            for job in result.rejected
        ],
    }
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=_json_default),
        encoding="utf-8",
    )
    return path
