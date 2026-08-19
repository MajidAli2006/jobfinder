"""Tests for the employer-board adapter layer and runtime discovery.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from job_agent import profile
from job_agent.models import RawJob
from job_agent.sources import ats, base
from job_agent.sources.company_boards import CompanyBoards, DiscoveredBoards, serves


def url_job(url: str) -> RawJob:
    return RawJob(source="test", source_id=url, title="", company="", url=url)


class SlugTests(unittest.TestCase):
    """Company names reduce to the shape boards use as a token."""

    def test_legal_suffixes_and_punctuation_are_dropped(self):
        self.assertEqual(ats.slug("Lagos Builders Ltd"), "lagosbuilders")
        self.assertEqual(ats.slug("Acme Technologies, Inc."), "acme")
        self.assertEqual(ats.slug("Müller & Söhne GmbH"), "mullersohne")

    def test_an_already_clean_name_is_unchanged(self):
        self.assertEqual(ats.slug("Monzo"), "monzo")


class TokenDiscoveryTests(unittest.TestCase):
    """Aggregators link to employers' own systems; those links carry tokens."""

    def test_each_supported_system_is_recognised(self):
        blob = "\n".join([
            "https://boards.greenhouse.io/airtable/jobs/12",
            "https://jobs.lever.co/plumbcorp/abc",
            "https://jobs.ashbyhq.com/posthog/xyz",
            "https://apply.workable.com/lagosbuilders/j/9",
            "https://acme.recruitee.com/o/fitter",
            "https://careers.smartrecruiters.com/Visa/123",
        ])
        found = ats.discover_tokens(blob)
        self.assertEqual(found["greenhouse"], {"airtable"})
        self.assertEqual(found["lever"], {"plumbcorp"})
        self.assertEqual(found["ashby"], {"posthog"})
        self.assertEqual(found["workable"], {"lagosbuilders"})
        self.assertEqual(found["recruitee"], {"acme"})
        self.assertEqual(found["smartrecruiters"], {"visa"})

    def test_an_unrelated_url_yields_nothing(self):
        self.assertEqual(ats.discover_tokens("https://example.com/careers"), {})

    def test_every_pattern_has_an_adapter(self):
        self.assertEqual(set(ats.TOKEN_PATTERNS), set(ats.ADAPTERS))


class FetchBoardTests(unittest.TestCase):
    def test_an_unknown_system_returns_nothing(self):
        self.assertEqual(ats.fetch_board("myspace", "acme"), [])

    def test_an_empty_token_returns_nothing(self):
        self.assertEqual(ats.fetch_board("greenhouse", ""), [])

    def test_a_failing_board_does_not_stop_the_run(self):
        with patch.dict(ats.ADAPTERS, {"greenhouse": lambda t, b: 1 / 0}):
            self.assertEqual(ats.fetch_board("greenhouse", "acme"), [])


class SeedRegionTests(unittest.TestCase):
    """A seeded employer is only asked about where it actually hires."""

    def tearDown(self):
        profile.reset()

    def _search_in(self, country):
        profile.set_active(replace(profile.active(), key="compiled:test",
                                   home_country=country, target_regions=()))

    def test_an_untagged_seed_is_asked_anywhere(self):
        self._search_in("Nigeria")
        self.assertTrue(serves({"region": ""}))

    def test_a_uk_seed_is_skipped_for_a_nigerian_search(self):
        self._search_in("Nigeria")
        self.assertFalse(serves({"region": "United Kingdom"}))

    def test_a_uk_seed_is_used_for_a_uk_search(self):
        self._search_in("United Kingdom")
        self.assertTrue(serves({"region": "United Kingdom"}))

    def test_an_unknown_country_keeps_everything(self):
        self._search_in("")
        self.assertTrue(serves({"region": "United Kingdom"}))

    def test_the_curated_list_shrinks_outside_its_regions(self):
        self._search_in("United Kingdom")
        uk = len(CompanyBoards().boards())
        self._search_in("Nigeria")
        self.assertLess(len(CompanyBoards().boards()), uk)


class DiscoveredBoardsTests(unittest.TestCase):
    def setUp(self):
        base.clear_seen()

    def tearDown(self):
        base.clear_seen()
        profile.reset()

    def test_tokens_come_from_urls_collected_this_run(self):
        base.remember_urls([url_job("https://jobs.lever.co/plumbcorp/abc")])
        self.assertIn(("lever", "plumbcorp"), DiscoveredBoards().tokens())

    def test_nothing_is_discovered_from_an_empty_run(self):
        self.assertEqual(DiscoveredBoards().tokens(), [])

    def test_the_number_of_boards_tried_is_capped(self):
        base.remember_urls([url_job(f"https://jobs.lever.co/co{i}/x") for i in range(200)])
        self.assertLessEqual(len(DiscoveredBoards().tokens()), DiscoveredBoards.MAX_BOARDS)

    def test_discovery_can_be_switched_off(self):
        from job_agent import config
        base.remember_urls([url_job("https://jobs.lever.co/plumbcorp/abc")])
        original = config.DISCOVER_EMPLOYER_BOARDS
        try:
            config.DISCOVER_EMPLOYER_BOARDS = False
            self.assertEqual(DiscoveredBoards().fetch(), [])
        finally:
            config.DISCOVER_EMPLOYER_BOARDS = original

    def test_discovered_jobs_are_labelled_with_their_source(self):
        base.remember_urls([url_job("https://jobs.lever.co/plumbcorp/abc")])
        found = [RawJob(source="", source_id="1", title="Fitter", company="PlumbCorp",
                        url="https://jobs.lever.co/plumbcorp/abc")]
        with patch.object(ats, "fetch_board", return_value=found):
            jobs = DiscoveredBoards().fetch()
        self.assertTrue(jobs)
        self.assertEqual(jobs[0].source, "discovered_boards")


if __name__ == "__main__":
    unittest.main()
