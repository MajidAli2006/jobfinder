"""Tests for the Claude judgement layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dataclasses import replace

from job_agent import config, llm, profile
from job_agent.models import Job, RunStats

from .fixtures import flutter_uk_profile


def make_job(**overrides) -> Job:
    defaults = {
        "fingerprint": overrides.pop("fingerprint", "fp-" + str(id(overrides))),
        "title": "Senior Flutter Engineer",
        "company": "Northwind Labs",
        "location": "Remote",
        "description": "We are a fully distributed team hiring through Deel.",
        "match_score": 70,
        "is_prospect": True,
        "rejected": True,
        "rejection_reason": "Region not stated",
        "rejection_category": "uk_unconfirmed",
        "concerns": ["Eligibility unconfirmed"],
    }
    defaults.update(overrides)
    return Job(**defaults)


class CacheIsolatedTest(unittest.TestCase):
    """Every test writes to a throwaway cache directory."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._real_cache_dir = config.CACHE_DIR
        self._real_ask = llm.ask
        config.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self):
        config.CACHE_DIR = self._real_cache_dir
        llm.ask = self._real_ask
        self._tmp.cleanup()


class AvailabilityTests(unittest.TestCase):

    def test_no_api_key_means_unavailable(self):
        real = config.ANTHROPIC_API_KEY
        config.ANTHROPIC_API_KEY = ""
        try:
            ok, why = llm.available()
            self.assertFalse(ok)
            self.assertIn("ANTHROPIC_API_KEY", why)
        finally:
            config.ANTHROPIC_API_KEY = real

    def test_master_switch_wins_over_a_present_key(self):
        real_key, real_flag = config.ANTHROPIC_API_KEY, config.LLM_ENABLED
        config.ANTHROPIC_API_KEY, config.LLM_ENABLED = "sk-test", False
        try:
            ok, why = llm.available()
            self.assertFalse(ok)
            self.assertIn("disabled", why)
        finally:
            config.ANTHROPIC_API_KEY, config.LLM_ENABLED = real_key, real_flag


class AdvertTextTests(unittest.TestCase):

    def test_facts_precede_the_body(self):
        text = llm.advert_text(make_job(applicants="Over 200 applicants"))
        self.assertTrue(text.startswith("Title: Senior Flutter Engineer"))
        self.assertIn("Applicants so far: Over 200 applicants", text)
        self.assertIn("Compensation: not published", text)
        self.assertIn("Advert text:", text)

    def test_body_is_truncated_to_the_configured_ceiling(self):
        job = make_job(description="x" * (config.LLM_MAX_ADVERT_CHARS + 5000))
        body = llm.advert_text(job).split("Advert text:\n", 1)[1]
        self.assertEqual(len(body), config.LLM_MAX_ADVERT_CHARS)

    def test_missing_description_is_stated_not_faked(self):
        self.assertIn("published no description", llm.advert_text(make_job(description="")))


class EligibilityTests(CacheIsolatedTest):

    def _run(self, verdict, job=None):
        job = job or make_job()
        stats = RunStats()
        llm.ask = lambda *a, **k: verdict
        promoted = llm.adjudicate_eligibility([job], stats, llm.Budget(1.0))
        return job, stats, promoted

    def test_remote_and_eligible_is_promoted_to_a_lead(self):
        job, stats, promoted = self._run({
            "remote": "yes", "eligible": "yes",
            "reason": "Hires worldwide through an employer of record",
            "evidence": "hiring through Deel",
        })
        self.assertEqual(promoted, [job])
        self.assertFalse(job.is_prospect)
        self.assertFalse(job.rejected)
        self.assertTrue(job.llm_promoted)
        self.assertEqual(job.rejection_reason, "")
        self.assertIn("AI-confirmed", job.eligibility)
        self.assertIn("employer of record", job.match_reasons[0])
        self.assertEqual(job.concerns, [])

    def test_a_promoted_lead_cannot_outrank_a_rules_confirmed_one(self):
        job, _, _ = self._run(
            {"remote": "yes", "eligible": "yes", "reason": "UK included", "evidence": "UK"},
            make_job(match_score=97),
        )
        self.assertEqual(job.match_score, config.LLM_PROMOTED_SCORE_CAP)

    def test_ineligible_is_marked_do_not_apply_and_stays_off_the_leads(self):
        job, stats, promoted = self._run({
            "remote": "yes", "eligible": "no",
            "reason": "Requires US work authorisation",
            "evidence": "must be authorized to work in the US",
        })
        self.assertEqual(promoted, [])
        self.assertTrue(job.is_prospect)
        self.assertEqual(job.application_status, "Do not apply")
        self.assertEqual(stats.llm_confirmed_ineligible, 1)
        self.assertIn("US work authorisation", job.rejection_reason)

    def test_unclear_leaves_it_a_prospect_but_records_the_reading(self):
        job, _, promoted = self._run({
            "remote": "yes", "eligible": "unclear",
            "reason": "The advert never states a region", "evidence": "",
        })
        self.assertEqual(promoted, [])
        self.assertTrue(job.is_prospect)
        self.assertIn("never states a region", job.concerns[0])

    def test_remote_unconfirmed_is_not_promoted_even_when_eligible(self):
        _, _, promoted = self._run({
            "remote": "unclear", "eligible": "yes",
            "reason": "UK named in the location", "evidence": "London, UK",
        })
        self.assertEqual(promoted, [])

    def test_an_unavailable_model_changes_nothing(self):
        job, _, promoted = self._run(None)
        self.assertEqual(promoted, [])
        self.assertTrue(job.is_prospect)
        self.assertEqual(job.rejection_reason, "Region not stated")
        self.assertEqual(job.llm_eligibility, "")

    def test_a_verdict_is_read_from_cache_on_the_second_run(self):
        verdict = {"remote": "yes", "eligible": "yes", "reason": "Worldwide", "evidence": "anywhere"}
        calls = []

        def counting_ask(*args, **kwargs):
            calls.append(1)
            return verdict

        llm.ask = counting_ask
        first, second = make_job(fingerprint="same"), make_job(fingerprint="same")
        stats = RunStats()
        llm.adjudicate_eligibility([first], stats, llm.Budget(1.0))
        llm.adjudicate_eligibility([second], stats, llm.Budget(1.0))

        self.assertEqual(len(calls), 1, "the second run should not pay for the same advert")
        self.assertEqual(stats.llm_cache_hits, 1)
        self.assertTrue(second.llm_promoted)


class FitTests(CacheIsolatedTest):

    def test_chance_is_blended_into_the_match_score(self):
        job = make_job(match_score=80, is_prospect=False, rejected=False, concerns=[])
        llm.ask = lambda *a, **k: {
            "fit": 90, "chance": 30, "verdict": "long shot",
            "strengths": ["Regulated fintech — they ask for PSD2"],
            "gaps": ["Over 200 applicants"],
        }
        stats = RunStats()
        llm.assess_fit([job], stats, llm.Budget(1.0))

        weight = config.LLM_FIT_WEIGHT
        self.assertEqual(job.match_score, round(80 * (1 - weight) + 30 * weight))
        self.assertEqual(job.llm_fit, 90)
        self.assertEqual(job.llm_chance, 30)
        self.assertEqual(job.llm_verdict, "long shot")
        self.assertIn("Verdict: long shot", job.match_reasons[0])
        self.assertIn("Assessed: Regulated fintech — they ask for PSD2", job.match_reasons)
        self.assertIn("Assessed: Over 200 applicants", job.concerns)

    def test_out_of_range_numbers_are_clamped_not_trusted(self):
        job = make_job(match_score=50, is_prospect=False)
        llm.ask = lambda *a, **k: {
            "fit": 250, "chance": -40, "verdict": "strong", "strengths": [], "gaps": [],
        }
        llm.assess_fit([job], RunStats(), llm.Budget(1.0))
        self.assertEqual(job.llm_fit, 100)
        self.assertEqual(job.llm_chance, 0)

    def test_no_opinion_leaves_the_rules_score_alone(self):
        job = make_job(match_score=73, is_prospect=False)
        llm.ask = lambda *a, **k: None
        llm.assess_fit([job], RunStats(), llm.Budget(1.0))
        self.assertEqual(job.match_score, 73)
        self.assertEqual(job.llm_verdict, "")

    def test_the_best_leads_are_assessed_first(self):
        seen = []
        llm.ask = lambda system, schema, user, effort, budget: (
            seen.append(user.split("\n", 1)[0]) or
            {"fit": 50, "chance": 50, "verdict": "strong", "strengths": [], "gaps": []}
        )
        jobs = [
            make_job(fingerprint="low", title="Low", match_score=10, is_prospect=False),
            make_job(fingerprint="high", title="High", match_score=95, is_prospect=False),
        ]
        real_limit = config.LLM_MAX_FIT_CALLS
        config.LLM_MAX_FIT_CALLS = 1
        try:
            llm.assess_fit(jobs, RunStats(), llm.Budget(1.0))
        finally:
            config.LLM_MAX_FIT_CALLS = real_limit
        self.assertEqual(seen, ["Title: High"])


class ChanceFloorTests(CacheIsolatedTest):
    """A CV changes the question from "what matches?" to "what could I get?"."""

    def setUp(self):
        super().setUp()
        self._real_profile = profile.active()

    def tearDown(self):
        profile.reset()
        super().tearDown()

    def _assess(self, *, has_cv: bool, chances: list[int], min_kept: int = 0):
        """Assess one batch of jobs, one per chance value.

        The whole list goes in at once, as the pipeline does it: how many
        leads a run has is exactly what the floor now depends on.
        """
        profile.set_active(replace(profile.active(), has_cv=has_cv))
        jobs = [
            make_job(fingerprint=f"fp{index}", company=f"Employer {index}.",
                     match_score=80 - index, is_prospect=False, rejected=False, concerns=[])
            for index in range(len(chances))
        ]
        wanted = {f"Employer {index}.": chance for index, chance in enumerate(chances)}

        def ask(_system, _schema, advert, *args, **kwargs):
            chance = next(c for name, c in wanted.items() if name in advert)
            return {"fit": 80, "chance": chance, "verdict": "long shot",
                    "strengths": [], "gaps": []}

        llm.ask = ask
        real = config.LLM_CHANCE_FLOOR_MIN_KEPT
        config.LLM_CHANCE_FLOOR_MIN_KEPT = min_kept
        try:
            long_shots = llm.assess_fit(jobs, RunStats(), llm.Budget(1.0))
        finally:
            config.LLM_CHANCE_FLOOR_MIN_KEPT = real
        return jobs, long_shots

    def test_with_a_cv_low_chance_leads_are_set_aside(self):
        jobs, long_shots = self._assess(has_cv=True, chances=[85, 20, 60, 5])
        self.assertEqual(sorted(j.llm_chance for j in long_shots), [5, 20])
        for job in long_shots:
            self.assertTrue(job.rejected)
            self.assertEqual(job.rejection_category, "low_chance")
            self.assertIn("below the", job.rejection_reason)
            self.assertEqual(job.application_status, "Only if you have time")

    def test_without_a_cv_nothing_is_filtered(self):
        _, long_shots = self._assess(has_cv=False, chances=[85, 20, 60, 5])
        self.assertEqual(long_shots, [])

    def test_the_floor_is_configurable_and_can_be_switched_off(self):
        real = config.LLM_MIN_CHANCE_WITH_CV
        config.LLM_MIN_CHANCE_WITH_CV = 0
        try:
            _, long_shots = self._assess(has_cv=True, chances=[5, 10])
        finally:
            config.LLM_MIN_CHANCE_WITH_CV = real
        self.assertEqual(long_shots, [])

    def test_a_thin_run_keeps_everything_it_qualified(self):
        # The case this guard exists for: a real run rated 9 of 14 leads below
        # the floor and the report lost them all. They were still the best
        # available.
        chances = [70, 65, 55] + [35, 30, 30, 25, 20, 20, 15, 10, 10, 5, 5]
        jobs, long_shots = self._assess(has_cv=True, chances=chances, min_kept=10)
        self.assertEqual(len(jobs) - len(long_shots), 10)
        self.assertEqual(len(long_shots), 4)

    def test_it_sets_aside_the_weakest_first(self):
        _, long_shots = self._assess(has_cv=True, chances=[80, 35, 5, 30, 20],
                                     min_kept=3)
        self.assertEqual(sorted(j.llm_chance for j in long_shots), [5, 20])

    def test_a_run_no_bigger_than_the_floor_allows_loses_nothing(self):
        _, long_shots = self._assess(has_cv=True, chances=[5, 5, 5], min_kept=10)
        self.assertEqual(long_shots, [])

    def test_a_plentiful_run_still_applies_the_floor_in_full(self):
        chances = [90, 85, 80, 75, 70, 65, 60, 55, 50, 45, 25, 10]
        _, long_shots = self._assess(has_cv=True, chances=chances, min_kept=10)
        self.assertEqual(sorted(j.llm_chance for j in long_shots), [10, 25])

    def test_a_lead_exactly_on_the_floor_is_kept(self):
        """The floor is the lowest chance still worth reading, not the first cut."""
        floor = config.LLM_MIN_CHANCE_WITH_CV
        chances = [90, 85, 80, 75, 70, 65, 60, 55, 50, 45, floor, floor - 1]
        _, long_shots = self._assess(has_cv=True, chances=chances, min_kept=10)
        self.assertEqual([j.llm_chance for j in long_shots], [floor - 1])

    def test_a_lead_the_model_never_reached_keeps_its_place(self):
        job = make_job(fingerprint="unassessed", match_score=80,
                       is_prospect=False, rejected=False, concerns=[])
        profile.set_active(replace(profile.active(), has_cv=True))
        llm.ask = lambda *a, **k: None
        long_shots = llm.assess_fit([job], RunStats(), llm.Budget(1.0))
        self.assertEqual(long_shots, [])
        self.assertFalse(job.rejected)


class BudgetTests(unittest.TestCase):

    class FakeUsage:
        input_tokens = 1_000_000
        output_tokens = 0
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0

    def test_spend_is_priced_from_the_model_table(self):
        budget = llm.Budget(10.0)
        real = config.LLM_MODEL
        config.LLM_MODEL = "claude-opus-5"
        try:
            budget.record(self.FakeUsage())
        finally:
            config.LLM_MODEL = real
        self.assertAlmostEqual(budget.spent, 5.0, places=4)

    def test_the_ceiling_stops_further_calls(self):
        budget = llm.Budget(1.0)
        self.assertFalse(budget.exhausted())
        real = config.LLM_MODEL
        config.LLM_MODEL = "claude-opus-5"
        try:
            budget.record(self.FakeUsage())
        finally:
            config.LLM_MODEL = real
        self.assertTrue(budget.exhausted())


class PromptBuildingTests(unittest.TestCase):
    """The prompts must build under every profile, including the empty one.

    A profile with no CV and no local candidate file is the state a bare
    checkout starts in, so a prompt that only renders for a described
    candidate is broken for the first run of every new user.
    """

    def setUp(self):
        profile.reset()

    def tearDown(self):
        profile.reset()

    def test_both_prompts_build_when_nobody_is_described(self):
        for name, build in (("eligibility", llm.system_eligibility), ("fit", llm.system_fit)):
            with self.subTest(prompt=name):
                self.assertTrue(build().strip())

    def test_an_undescribed_candidate_is_not_given_an_invented_background(self):
        self.assertIn(profile.DEFAULT_CANDIDATE_BRIEF.split(".")[0], llm.system_fit())

    def test_an_unknown_home_country_never_reaches_the_prompt_as_a_blank(self):
        prompt = llm.system_eligibility()
        self.assertNotIn("living in ?", prompt)
        self.assertNotIn("working from: .", prompt)

    def test_a_described_candidate_is_stated_with_their_country(self):
        with profile.using(flutter_uk_profile()):
            prompt = llm.system_eligibility()
        self.assertIn("United Kingdom", prompt)
        self.assertNotIn(profile.DEFAULT_CANDIDATE_BRIEF, prompt)


if __name__ == "__main__":
    unittest.main()
