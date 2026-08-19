"""Tests for the per-job gap description.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from job_agent import gaps, profile
from job_agent.models import Job

from .fixtures import flutter_uk_profile


def job(**kwargs) -> Job:
    base = {"title": "Senior Engineer", "company": "Acme", "seniority": "Senior",
                "core_skill_required": "Yes"}
    base.update(kwargs)
    return Job(**base)


def with_cv(**overrides):
    search = replace(flutter_uk_profile(), has_cv=True, seniority="Senior",
                     years_experience=7, **overrides)
    profile.set_active(search)


class WithoutACvTests(unittest.TestCase):
    def tearDown(self):
        profile.reset()

    def test_it_says_no_cv_rather_than_inventing_gaps(self):
        profile.set_active(replace(flutter_uk_profile(), has_cv=False))
        self.assertEqual(gaps.describe(job(required_years=20)), gaps.NO_CV)


class WithACvTests(unittest.TestCase):
    def tearDown(self):
        profile.reset()

    def test_a_years_shortfall_is_named(self):
        with_cv()
        self.assertIn("12+ years", gaps.describe(job(required_years=12)))

    def test_enough_years_is_not_a_gap(self):
        with_cv()
        self.assertEqual(gaps.describe(job(required_years=5)), gaps.NONE_FOUND)

    def test_a_missing_core_skill_is_named(self):
        with_cv()
        self.assertIn("Flutter", gaps.describe(job(core_skill_required="No")))

    def test_a_role_above_your_level_is_named(self):
        with_cv()
        self.assertIn("Principal", gaps.describe(job(seniority="Principal")))

    def test_a_role_just_below_your_level_is_not_a_gap(self):
        with_cv()
        self.assertEqual(gaps.describe(job(seniority="Mid")), gaps.NONE_FOUND)

    def test_a_role_far_below_your_level_is_named(self):
        with_cv()
        self.assertIn("Junior", gaps.describe(job(seniority="Junior")))

    def test_unconfirmed_eligibility_is_named(self):
        with_cv()
        described = gaps.describe(job(eligibility="Unconfirmed — ask before applying"))
        self.assertIn("Unconfirmed", described)

    def test_several_gaps_are_joined(self):
        with_cv()
        described = gaps.describe(job(required_years=15, core_skill_required="No"))
        self.assertIn("15+ years", described)
        self.assertIn("Flutter", described)

    def test_a_clean_match_says_so(self):
        with_cv()
        self.assertEqual(gaps.describe(job()), gaps.NONE_FOUND)


class JudgementLayerTests(unittest.TestCase):
    """When the model has read the CV and the advert, its gaps win."""

    def tearDown(self):
        profile.reset()

    def test_named_gaps_replace_the_rules_based_line(self):
        from unittest.mock import patch
        from job_agent import llm
        with_cv()
        target = job()
        target.fingerprint = "fp1"
        target.potential_gaps = gaps.NONE_FOUND
        verdicts = {"fp1": {"fit": 70, "chance": 60, "verdict": "worth applying",
                            "strengths": ["Ships mobile apps"],
                            "gaps": ["No Kubernetes", "No public sector work"]}}

        class Stats:
            llm_cache_hits = 0
            llm_fit_calls = 0

        with patch.object(llm, "_run_stage", return_value=(verdicts, 0, 1)):
            llm.assess_fit([target], Stats(), llm.Budget(limit_usd=1.0))
        self.assertEqual(target.potential_gaps, "No Kubernetes · No public sector work")


if __name__ == "__main__":
    unittest.main()
