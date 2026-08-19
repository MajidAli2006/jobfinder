"""Tests for the full-advert fetch stage."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from job_agent import config, descriptions, profile
from job_agent.models import RawJob
from job_agent.utils import now_local

from .fixtures import flutter_uk_profile


def setUpModule():
    """Judging an advert irrelevant needs a search for it to be irrelevant to."""
    profile.set_active(flutter_uk_profile())


def tearDownModule():
    profile.reset()


FULL_ADVERT = (
    "We are hiring a Senior Flutter Engineer to work fully remote from anywhere "
    "in the United Kingdom. You will own our Dart codebase end to end, ship to "
    "iOS and Android, and mentor two engineers. " * 4
)


def truncated(source="adzuna", source_id="adzuna-1", title="Senior Flutter Developer",
              location="London", snippet="We are looking for a Flutter developer to…",
              posted_days_ago=1) -> RawJob:
    return RawJob(
        source=source,
        source_id=source_id,
        title=title,
        company="Acme",
        url="https://example.com/job/1",
        description=snippet,
        location_raw=location,
        posted_at=now_local() - timedelta(days=posted_days_ago),
        extra={"truncated_description": True},
    )


class CandidateSelectionTests(unittest.TestCase):

    def test_only_truncated_postings_are_selected(self):
        full = truncated(source_id="adzuna-2")
        full.extra["truncated_description"] = False
        picked = descriptions.candidates([truncated(), full])
        self.assertEqual([j.source_id for j in picked], ["adzuna-1"])

    def test_sources_without_a_fetcher_are_skipped(self):
        for source in ("careerjet", "jooble"):
            self.assertNotIn(source, descriptions.FETCHERS)
            self.assertEqual(descriptions.candidates([truncated(source=source)]), [])

    def test_irrelevant_postings_are_not_worth_a_fetch(self):
        junk = truncated(title="Salesforce Administrator", snippet="CRM administration")
        self.assertEqual(descriptions.candidates([junk]), [])

    def test_uk_flutter_roles_are_fetched_first(self):
        far = truncated(source_id="adzuna-far", title="Mobile Developer", location="Berlin")
        near = truncated(source_id="adzuna-near", title="Flutter Developer",
                         location="United Kingdom")
        order = [j.source_id for j in descriptions.candidates([far, near])]
        self.assertEqual(order[0], "adzuna-near")


class ExtractionTests(unittest.TestCase):

    def test_job_posting_is_read_out_of_json_ld(self):
        page = ('<html><head><script type="application/ld+json">'
                + json.dumps({"@type": "JobPosting", "title": "Flutter Dev",
                              "description": "<p>Remote from the UK</p>"})
                + "</script></head><body>ignored</body></html>")
        posting = descriptions.job_posting_ld(page)
        self.assertIsNotNone(posting)
        self.assertEqual(posting["title"], "Flutter Dev")

    def test_json_ld_inside_a_graph_is_found(self):
        page = ('<script type="application/ld+json">'
                + json.dumps({"@context": "https://schema.org",
                              "@graph": [{"@type": "Organisation"},
                                         {"@type": "JobPosting", "title": "Found"}]})
                + "</script>")
        self.assertEqual(descriptions.job_posting_ld(page)["title"], "Found")

    def test_a_page_without_json_ld_yields_nothing(self):
        self.assertIsNone(descriptions.job_posting_ld("<html><body>Remote jobs</body></html>"))

    def test_malformed_json_ld_is_ignored(self):
        self.assertIsNone(descriptions.job_posting_ld(
            '<script type="application/ld+json">{not json</script>'))

    def test_named_container_is_read_when_there_is_no_json_ld(self):
        page = ('<nav>Remote jobs · Work from home</nav>'
                '<section class="adp-body mx-4 text-sm">We need a '
                '<br />Flutter developer.</section>')
        body = descriptions.advert_container(page)
        self.assertIn("Flutter developer.", body)
        self.assertNotIn("Remote jobs", body, "navigation must never reach the description")

    def test_container_extraction_never_falls_back_to_the_whole_page(self):
        page = '<html><body><nav>Remote jobs</nav><div>Some advert</div></body></html>'
        self.assertEqual(descriptions.advert_container(page), "")


class ApplyTests(unittest.TestCase):

    def test_full_body_lifts_the_truncated_flag(self):
        job = truncated()
        self.assertTrue(descriptions._apply(job, {"description": FULL_ADVERT}))
        self.assertFalse(job.extra["truncated_description"])
        self.assertIn("United Kingdom", job.description)

    def test_a_body_no_better_than_the_snippet_changes_nothing(self):
        job = truncated(snippet=FULL_ADVERT)
        self.assertFalse(descriptions._apply(job, {"description": "Short blurb"}))
        self.assertTrue(job.extra["truncated_description"])

    def test_a_short_body_leaves_the_posting_a_prospect(self):
        job = truncated()
        self.assertFalse(descriptions._apply(job, {"description": "Apply on our site"}))
        self.assertTrue(job.extra["truncated_description"])

    def test_a_missing_date_is_filled_from_the_advert(self):
        job = truncated()
        job.posted_at = None
        descriptions._apply(job, {"description": FULL_ADVERT,
                                  "posted_at": "2026-08-06T09:19:50"})
        self.assertIsNotNone(job.posted_at)
        self.assertEqual(job.posted_at.date().isoformat(), "2026-08-06")

    def test_a_known_date_is_never_overwritten(self):
        job = truncated(posted_days_ago=2)
        original = job.posted_at
        descriptions._apply(job, {"description": FULL_ADVERT, "posted_at": "2020-01-01"})
        self.assertEqual(job.posted_at, original)

    def test_applicant_count_is_carried_across(self):
        job = truncated()
        descriptions._apply(job, {"description": FULL_ADVERT, "applicants": "20 applicants"})
        self.assertEqual(job.extra["applicants"], "20 applicants")


class FillTests(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._real_cache_dir = config.CACHE_DIR
        config.CACHE_DIR = Path(self._tmp.name)
        self._real_fetchers = dict(descriptions.FETCHERS)
        self.calls: list[str] = []

        def fake(raw: RawJob) -> dict:
            self.calls.append(raw.source_id)
            return {"description": FULL_ADVERT}

        descriptions.FETCHERS["adzuna"] = fake

    def tearDown(self):
        descriptions.FETCHERS.clear()
        descriptions.FETCHERS.update(self._real_fetchers)
        config.CACHE_DIR = self._real_cache_dir
        self._tmp.cleanup()

    def test_fills_and_counts(self):
        jobs = [truncated(source_id=f"adzuna-{i}") for i in range(3)]
        self.assertEqual(descriptions.fill_descriptions(jobs), 3)
        self.assertTrue(all(not j.extra["truncated_description"] for j in jobs))

    def test_budget_caps_the_fetches(self):
        jobs = [truncated(source_id=f"adzuna-{i}") for i in range(5)]
        self.assertEqual(descriptions.fill_descriptions(jobs, budget=2), 2)
        self.assertEqual(len(self.calls), 2)

    def test_second_run_is_served_from_cache(self):
        first = [truncated(source_id="adzuna-9")]
        descriptions.fill_descriptions(first)
        self.calls.clear()

        second = [truncated(source_id="adzuna-9")]
        self.assertEqual(descriptions.fill_descriptions(second), 1)
        self.assertEqual(self.calls, [], "a cached advert must not be re-fetched")
        self.assertFalse(second[0].extra["truncated_description"])

    def test_a_stale_applicant_count_is_not_restored(self):
        import sqlite3
        import time as time_mod
        from contextlib import closing

        descriptions.FETCHERS["adzuna"] = lambda raw: {
            "description": FULL_ADVERT, "applicants": "12 applicants"}
        first = [truncated(source_id="adzuna-7")]
        descriptions.fill_descriptions(first)
        self.assertEqual(first[0].extra["applicants"], "12 applicants")

        path = config.CACHE_DIR / "fetch_cache.sqlite3"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE fetch_cache SET fetched_at = ?",
                         (time_mod.time() - 10 * 86400,))

        second = [truncated(source_id="adzuna-7")]
        descriptions.fill_descriptions(second)
        self.assertFalse(second[0].extra.get("applicants"),
                         "a 10-day-old applicant count must not be reused")
        self.assertFalse(second[0].extra["truncated_description"],
                         "the description itself is still good")

    def test_a_failing_fetcher_never_breaks_the_run(self):
        def boom(raw: RawJob) -> dict:
            raise RuntimeError("board is down")

        descriptions.FETCHERS["adzuna"] = boom
        jobs = [truncated()]
        self.assertEqual(descriptions.fill_descriptions(jobs), 0)
        self.assertTrue(jobs[0].extra["truncated_description"])

    def test_nothing_to_do_is_free(self):
        self.assertEqual(descriptions.fill_descriptions([]), 0)


class CacheTtlTests(unittest.TestCase):

    def test_descriptions_outlive_a_single_run(self):
        self.assertGreaterEqual(config.DESCRIPTION_CACHE_DAYS, 7)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
