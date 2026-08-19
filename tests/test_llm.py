"""Tests for the Claude judgement layer."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dataclasses import replace

from job_agent import cache, config, llm, profile
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


class MalformedVerdictTests(CacheIsolatedTest):
    """The model's answer is untrusted input, like any other feed.

    A verdict object that is well-formed enough to reach the schema but wrong
    in its parts — a chance of 5000, a null verdict, a gaps field holding a
    string — must be read defensively rather than trusted.
    """

    def setUp(self):
        super().setUp()
        profile.set_active(replace(profile.active(), has_cv=True))

    def tearDown(self):
        profile.reset()
        super().tearDown()

    def _assessed(self, payload, index=0):
        job = make_job(fingerprint=f"m{index}", company=f"Employer {index}.",
                       match_score=70, is_prospect=False, rejected=False, concerns=[])
        llm.ask = lambda *a, **k: payload
        llm.assess_fit([job], RunStats(), llm.Budget(1.0))
        return job

    def test_a_chance_above_the_scale_is_clamped(self):
        self.assertEqual(self._assessed({"fit": 80, "chance": 5000}).llm_chance, 100)

    def test_a_chance_below_the_scale_is_clamped(self):
        self.assertEqual(self._assessed({"fit": 80, "chance": -20}).llm_chance, 0)

    def test_a_chance_that_is_not_a_number_is_ignored(self):
        self.assertEqual(self._assessed({"fit": 80, "chance": "high"}).llm_chance, 0)

    def test_a_null_verdict_does_not_become_the_word_none(self):
        """`str(None)` is "None", which reached the workbook as a verdict."""
        for payload in ({"fit": None, "chance": None, "verdict": None},
                        {"fit": 80, "chance": 40}):
            with self.subTest(payload=payload):
                self.assertEqual(self._assessed(payload).llm_verdict, "")

    def test_a_missing_answer_leaves_the_lead_unjudged(self):
        job = self._assessed(None)
        self.assertEqual((job.llm_chance, job.llm_fit, job.llm_verdict), (0, 0, ""))

    def test_a_well_formed_answer_still_lands(self):
        job = self._assessed({"fit": 88, "chance": 46, "verdict": "worth applying"})
        self.assertEqual((job.llm_fit, job.llm_chance, job.llm_verdict),
                         (88, 46, "worth applying"))


class CachedPayloadShapeTests(CacheIsolatedTest):
    """The cache hands its rows straight to callers that read them with .get().

    `cache.get` is typed to return a dict, but returned whatever JSON the row
    held. A half-written entry, or one left by an older format, would end a run
    in an AttributeError a long way from the cache.
    """

    def _stored(self, raw: str):
        import sqlite3
        import time as time_mod
        from contextlib import closing
        path = config.CACHE_DIR / "fetch_cache.sqlite3"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS fetch_cache "
                         "(key TEXT PRIMARY KEY, payload TEXT, fetched_at REAL)")
            conn.execute("INSERT OR REPLACE INTO fetch_cache VALUES (?, ?, ?)",
                         ("k", raw, time_mod.time()))
            conn.commit()
        return cache.get("k", 21)

    def test_a_row_that_is_not_an_object_is_refused(self):
        for raw in ("[1, 2, 3]", '"a refusal"', "42", "null", "true"):
            with self.subTest(payload=raw):
                self.assertIsNone(self._stored(raw))

    def test_a_real_object_is_still_returned(self):
        self.assertEqual(self._stored('{"chance": 46}'), {"chance": 46})

    def test_unparseable_json_is_refused(self):
        self.assertIsNone(self._stored("{not json"))


class VerdictsBelongToOneCandidateTests(CacheIsolatedTest):
    """A cached verdict is about a job and a person, not a job alone.

    The key was the job's fingerprint, so a run for one person served its
    verdicts to the next. A full-stack search run with no CV at all came back
    with gaps reading "CV evidences Flutter/Dart mobile" — the previous run's
    candidate, described in a report that belonged to somebody else.
    """

    def _under(self, brief: str, home: str = "Germany"):
        profile.set_active(replace(flutter_uk_profile(), key="test",
                                   candidate_brief=brief, home_country=home,
                                   home_terms=(home.lower(),)))

    def test_two_candidates_do_not_share_a_verdict(self):
        self._under("A senior full stack engineer.")
        full_stack = llm._candidate_fingerprint()
        self._under("A senior Flutter and mobile engineer.")
        mobile = llm._candidate_fingerprint()
        self.assertNotEqual(full_stack, mobile)

    def test_the_same_candidate_still_reuses_a_verdict(self):
        self._under("A senior full stack engineer.")
        first = llm._candidate_fingerprint()
        self._under("A senior full stack engineer.")
        self.assertEqual(first, llm._candidate_fingerprint())

    def test_where_they_live_changes_the_verdict(self):
        self._under("A senior full stack engineer.", home="Germany")
        germany = llm._candidate_fingerprint()
        self._under("A senior full stack engineer.", home="Canada")
        self.assertNotEqual(germany, llm._candidate_fingerprint())

    def test_a_verdict_cached_for_one_candidate_is_not_served_to_another(self):
        job = make_job(title="Senior Full Stack Developer", company="Acme")
        stored = {"fit": 91, "chance": 80, "verdict": "strong",
                  "strengths": ["react"], "gaps": []}

        self._under("A senior full stack engineer.")
        llm._run_stage([job], "fit", lambda _job: stored,
                       llm.Budget(10.0), 10)

        self._under("A senior Flutter and mobile engineer.")
        verdicts, hits, calls = llm._run_stage(
            [job], "fit", lambda _job: None, llm.Budget(10.0), 10)
        self.assertEqual(hits, 0, "the other candidate's verdict was served")
        self.assertEqual(verdicts, {})
        self.assertEqual(calls, 1)

    def tearDown(self):
        profile.reset()
        super().tearDown()


class ManyProfilesNeverMixTests(CacheIsolatedTest):
    """However many searches are run, none inherits another's judgement.

    Only two caches depend on who is searching: the compiled profile and the
    per-advert verdict. Everything else the run stores — an advert's text, a
    company's headcount — is a fact about the job, shared on purpose.
    """

    BRIEFS = (
        "A senior full stack engineer, TypeScript and React.",
        "A senior Flutter and mobile engineer.",
        "A data engineer working in Python and Spark.",
        "An electrician with seventeen years on site.",
        "A registered nurse moving into clinical informatics.",
    )

    def tearDown(self):
        profile.reset()
        super().tearDown()

    def _under(self, brief: str, home: str = "Germany"):
        profile.set_active(replace(flutter_uk_profile(), key="test",
                                   candidate_brief=brief, home_country=home,
                                   home_terms=(home.lower(),)))

    def test_every_profile_gets_its_own_key(self):
        seen = {}
        for brief in self.BRIEFS:
            self._under(brief)
            seen[llm._candidate_fingerprint()] = brief
        self.assertEqual(len(seen), len(self.BRIEFS),
                         "two of these searches would share cached verdicts")

    def test_whether_a_cv_was_supplied_changes_the_key(self):
        """The prompt tells the model to weigh a CV-less background differently.

        That instruction is chosen from `has_cv`, not from the brief, so two
        searches sharing a brief still ask two different questions.
        """
        self._under(self.BRIEFS[0])
        with_cv = replace(profile.active(), has_cv=True)
        profile.set_active(with_cv)
        evidenced = llm._candidate_fingerprint()
        profile.set_active(replace(with_cv, has_cv=False))
        self.assertNotEqual(evidenced, llm._candidate_fingerprint())

    def test_every_field_the_prompt_reads_is_covered(self):
        """The key is the prompt, so nothing the prompt reads can escape it."""
        self._under(self.BRIEFS[0])
        base = profile.active()
        first = llm._candidate_fingerprint()
        for field, value in (("candidate_brief", "Something else entirely."),
                             ("home_country", "Japan"),
                             ("has_cv", not base.has_cv),
                             ("region_excluding_home_terms", ("eu only",))):
            with self.subTest(field=field):
                profile.set_active(replace(base, **{field: value}))
                self.assertNotEqual(first, llm._candidate_fingerprint(),
                                    f"{field} does not reach the cache key")

    def test_no_profile_is_served_another_profiles_verdict(self):
        job = make_job(title="Senior Full Stack Developer", company="Acme")
        stored = {"fit": 91, "chance": 80, "verdict": "strong",
                  "strengths": [], "gaps": []}

        self._under(self.BRIEFS[0])
        llm._run_stage([job], "fit", lambda _j: stored, llm.Budget(10.0), 10)

        for brief in self.BRIEFS[1:]:
            with self.subTest(brief=brief):
                self._under(brief)
                _verdicts, hits, _calls = llm._run_stage(
                    [job], "fit", lambda _j: None, llm.Budget(10.0), 10)
                self.assertEqual(hits, 0)

        self._under(self.BRIEFS[0])
        _verdicts, hits, _calls = llm._run_stage(
            [job], "fit", lambda _j: None, llm.Budget(10.0), 10)
        self.assertEqual(hits, 1, "the same search should still reuse its own")

    def test_reading_the_cache_can_be_turned_off_entirely(self):
        job = make_job(title="Senior Full Stack Developer", company="Acme")
        stored = {"fit": 91, "chance": 80, "verdict": "strong",
                  "strengths": [], "gaps": []}
        self._under(self.BRIEFS[0])
        llm._run_stage([job], "fit", lambda _j: stored, llm.Budget(10.0), 10)

        cache.ENABLED = False
        try:
            _verdicts, hits, _calls = llm._run_stage(
                [job], "fit", lambda _j: None, llm.Budget(10.0), 10)
        finally:
            cache.ENABLED = True
        self.assertEqual(hits, 0, "--no-cache still served a stored verdict")
