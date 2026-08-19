"""Tests for the shortlist-chance estimate.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from job_agent import chance, profile
from job_agent.models import Job

from .fixtures import flutter_uk_profile


def job(**kwargs) -> Job:
    base = {"title": "Senior Engineer", "company": "Acme", "source": "greenhouse",
                "remote_status": "Remote — United Kingdom", "seniority": "Senior",
                "core_skill_required": "Yes", "job_age_days": 1}
    base.update(kwargs)
    return Job(**base)


class ApplicantCountTests(unittest.TestCase):
    def test_reads_the_common_captions(self):
        cases = {
            "Be among the first 25 applicants": 10,
            "Over 200 applicants": 250,
            "48 applicants": 48,
            "1,024 applicants": 1024,
            "": None,
            "no numbers here": None,
        }
        for caption, expected in cases.items():
            with self.subTest(caption=caption):
                self.assertEqual(chance.applicant_count(caption), expected)


class EstimateTests(unittest.TestCase):
    def setUp(self):
        profile.set_active(flutter_uk_profile())

    def tearDown(self):
        profile.reset()

    def test_fit_is_the_ceiling(self):
        # Every multiplier is capped at 1.05, so the score cannot run away.
        best = chance.estimate(job(applicants="Be among the first 25 applicants"), 100)
        self.assertLessEqual(best.score, chance.MAX_SCORE)
        self.assertLessEqual(best.multiplier, chance.CEILING)

    def test_a_crowded_advert_scores_below_a_quiet_one(self):
        quiet = chance.estimate(job(applicants="12 applicants"), 80)
        crowded = chance.estimate(job(applicants="Over 200 applicants"), 80)
        self.assertGreater(quiet.score, crowded.score)

    def test_an_old_advert_scores_below_a_fresh_one(self):
        fresh = chance.estimate(job(job_age_days=0), 80)
        stale = chance.estimate(job(job_age_days=60), 80)
        self.assertGreater(fresh.score, stale.score)

    def test_a_worldwide_pool_scores_below_a_home_scoped_one(self):
        home = chance.estimate(job(remote_status="Remote — United Kingdom"), 80)
        world = chance.estimate(job(remote_status="Remote — Worldwide"), 80)
        self.assertGreater(home.score, world.score)

    def test_direct_ats_beats_an_agency_posting(self):
        direct = chance.estimate(job(source="greenhouse"), 80)
        agency = chance.estimate(job(source="linkedin", company="Bright Recruitment Ltd"), 80)
        self.assertGreater(direct.score, agency.score)

    def test_a_bid_marketplace_is_heavily_discounted(self):
        marketplace = chance.estimate(job(source="freelancer"), 90)
        self.assertLess(marketplace.score, 50)

    def test_an_over_qualified_junior_role_collapses(self):
        junior = chance.estimate(job(seniority="Junior"), 80)
        senior = chance.estimate(job(seniority="Senior"), 80)
        self.assertLess(junior.score, senior.score / 1.5)

    def test_a_published_contact_helps(self):
        # From a base that is not already at the ceiling, or the clamp hides it.
        weak = {"applicants": "120 applicants", "job_age_days": 30,
                    "remote_status": "Remote — Worldwide"}
        with_contact = chance.estimate(job(public_email="careers@acme.com", **weak), 80)
        without = chance.estimate(job(**weak), 80)
        self.assertGreater(with_contact.score, without.score)

    def test_the_ceiling_stops_good_signals_compounding_without_limit(self):
        stacked = chance.estimate(
            job(applicants="Be among the first 25 applicants",
                public_email="careers@acme.com", company_size="11-50"), 80)
        self.assertEqual(stacked.multiplier, chance.CEILING)

    def test_the_multiplier_is_clamped_at_both_ends(self):
        awful = chance.estimate(
            job(applicants="Over 500 applicants", job_age_days=200, seniority="Junior",
                source="freelancer", remote_status="", core_skill_required="No"), 100)
        self.assertGreaterEqual(awful.multiplier, chance.FLOOR)
        self.assertGreaterEqual(awful.score, 1)

    def test_every_adjustment_explains_itself(self):
        estimate = chance.estimate(job(applicants="12 applicants"), 80)
        self.assertTrue(estimate.adjustments)
        for adjustment in estimate.adjustments:
            self.assertTrue(adjustment.reason.strip())
            self.assertRegex(adjustment.describe(), r"\([+-]?\d+%\)$")

    def test_the_explanation_shows_the_arithmetic(self):
        estimate = chance.estimate(job(), 80)
        self.assertIn("fit 80", estimate.explain())
        self.assertIn(str(estimate.score), estimate.explain())


class NoVocabularyTests(unittest.TestCase):
    """With an empty profile the estimate must not invent a trade or a country."""

    def tearDown(self):
        profile.reset()

    def test_core_skill_rule_is_silent_without_core_terms(self):
        profile.set_active(replace(flutter_uk_profile(), core_terms=()))
        estimate = chance.estimate(job(core_skill_required="No"), 80)
        self.assertFalse(any("no " in a.reason.lower() and "generalist" in a.reason
                             for a in estimate.adjustments))

    def test_home_rule_is_silent_without_a_home_country(self):
        profile.set_active(replace(flutter_uk_profile(), home_country=""))
        estimate = chance.estimate(job(remote_status="Remote — United Kingdom"), 80)
        self.assertFalse(any("right to work is clear" in a.reason
                             for a in estimate.adjustments))


if __name__ == "__main__":
    unittest.main()
