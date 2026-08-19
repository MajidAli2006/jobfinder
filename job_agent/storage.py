"""Persistence of previously seen vacancies so NEW leads can be distinguished from ones
carried over from an earlier run, and so application status survives.
"""

from __future__ import annotations

import logging
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


log = logging.getLogger("job_agent.storage")


def _open(path) -> sqlite3.Connection:
    """Connect and apply the schema, holding no handle if either fails.

    `connect` succeeds on any file; the schema is what rejects one that is not
    a database. The connection is open by then, and Windows refuses to rename a
    file another handle still holds — so a corrupt history could never be set
    aside there, only reported as unrebuildable.
    """
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
    except Exception:
        conn.close()
        raise
    return conn


def _connect() -> sqlite3.Connection:
    """The vacancy history, or a scratch database standing in for it.

    This file only remembers which vacancies have been seen before. Losing it
    costs the NEW markers and the application statuses; it must never cost the
    run itself, which used to end in a traceback and no report when the file
    was corrupt, unwritable, or on a full disk.
    """
    config.ensure_dirs()
    try:
        return _open(config.SEEN_DB)
    except sqlite3.DatabaseError as exc:
        log.warning("vacancy history unreadable (%s); starting a fresh one", exc)
    spoiled = config.SEEN_DB.with_suffix(config.SEEN_DB.suffix + ".unreadable")
    try:
        config.SEEN_DB.replace(spoiled)
        return _open(config.SEEN_DB)
    except (OSError, sqlite3.DatabaseError) as exc:
        log.warning("cannot rebuild the vacancy history (%s); this run will not "
                    "remember what it saw", exc)
        return _open(":memory:")


def mark_new_and_record(jobs: list[Job], run_date: date) -> int:
    """Flag each job as NEW or previously seen, then persist. Returns NEW count.

    A history that cannot be written — a read-only file, a full disk — costs
    the NEW markers for this run and nothing else. The report is the
    deliverable and is still written.
    """
    try:
        return _record(jobs, run_date)
    except sqlite3.Error as exc:
        log.warning("could not record what this run saw (%s); every lead is "
                    "reported as new", exc)
        for job in jobs:
            job.is_new = True
            job.job_status = "NEW"
            job.discovered_date = run_date
        return len(jobs)


def _record(jobs: list[Job], run_date: date) -> int:
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
    try:
        return _set_status(fingerprint, status)
    except sqlite3.Error as exc:
        log.warning("could not save the application status (%s)", exc)
        return False


def _set_status(fingerprint: str, status: str) -> bool:
    with closing(_connect()) as conn, conn:
        cursor = conn.execute(
            "UPDATE seen_jobs SET application_status = ? WHERE fingerprint = ?",
            (status, fingerprint),
        )
        return cursor.rowcount > 0


def stats() -> dict:
    try:
        return _stats()
    except sqlite3.Error as exc:
        log.warning("could not read the vacancy history (%s)", exc)
        return {"total_tracked": 0, "applied": 0}


def _stats() -> dict:
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN application_status != 'Not Applied' THEN 1 ELSE 0 END) AS applied "
            "FROM seen_jobs"
        ).fetchone()
        return {"total_tracked": row["total"] or 0, "applied": row["applied"] or 0}
