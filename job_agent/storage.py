"""Persistence of previously seen vacancies so NEW leads can be distinguished from ones
carried over from an earlier run, and so application status survives.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import date

from . import config
from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen_jobs (
    fingerprint        TEXT PRIMARY KEY,
    title              TEXT,
    company            TEXT,
    url                TEXT,
    first_seen         TEXT NOT NULL,
    last_seen          TEXT NOT NULL,
    times_seen         INTEGER NOT NULL DEFAULT 1,
    best_match_score   INTEGER DEFAULT 0,
    application_status TEXT DEFAULT 'Not Applied',
    notes              TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_seen_company ON seen_jobs(company);
"""


def _connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.SEEN_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def mark_new_and_record(jobs: list[Job], run_date: date) -> int:
    """Flag each job as NEW or previously seen, then persist. Returns NEW count."""
    new_count = 0
    today = run_date.isoformat()
    with closing(_connect()) as conn, conn:
        for job in jobs:
            row = conn.execute(
                "SELECT first_seen, times_seen, best_match_score, application_status "
                "FROM seen_jobs WHERE fingerprint = ?",
                (job.fingerprint,),
            ).fetchone()

            if row is None:
                job.is_new = True
                job.job_status = "NEW"
                job.discovered_date = run_date
                new_count += 1
                conn.execute(
                    "INSERT INTO seen_jobs (fingerprint, title, company, url, first_seen, "
                    "last_seen, times_seen, best_match_score, application_status) "
                    "VALUES (?, ?, ?, ?, ?, ?, 1, ?, 'Not Applied')",
                    (job.fingerprint, job.title, job.company, job.original_job_url,
                     today, today, job.match_score),
                )
            else:
                job.is_new = False
                job.job_status = "Seen previously"
                job.discovered_date = date.fromisoformat(row["first_seen"])
                job.application_status = row["application_status"] or "Not Applied"
                conn.execute(
                    "UPDATE seen_jobs SET last_seen = ?, times_seen = times_seen + 1, "
                    "best_match_score = MAX(best_match_score, ?), title = ?, company = ?, url = ? "
                    "WHERE fingerprint = ?",
                    (today, job.match_score, job.title, job.company,
                     job.original_job_url, job.fingerprint),
                )
    return new_count


def set_application_status(fingerprint: str, status: str) -> bool:
    with closing(_connect()) as conn, conn:
        cursor = conn.execute(
            "UPDATE seen_jobs SET application_status = ? WHERE fingerprint = ?",
            (status, fingerprint),
        )
        return cursor.rowcount > 0


def stats() -> dict:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN application_status != 'Not Applied' THEN 1 ELSE 0 END) AS applied "
            "FROM seen_jobs"
        ).fetchone()
        return {"total_tracked": row["total"] or 0, "applied": row["applied"] or 0}
