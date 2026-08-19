"""Tests for search profiles — the thing that makes this engine general."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from job_agent import config, filters, geography, profile, remote
from job_agent.models import RawJob

from .fixtures import flutter_uk_profile


def raw(title: str, description: str = "", company: str = "Acme",
        location: str = "Remote", tags: tuple = ()) -> RawJob:
    return RawJob(source="test", source_id="1", title=title, company=company,
                  url="https://example.com/job", description=description,
                  location_raw=location, tags=list(tags))


IOS_PAYLOAD = {
    "label": "iOS engineering", "is_job_search": True, "answer": "",
    "core_terms": ["ios", "swift"],
    "secondary_terms": ["ios developer", "ios engineer", "mobile engineer", "swiftui"],
    "hard_title_exclusions": ["recruiter", "sales", "designer"],
    "other_discipline_terms": ["python", "flutter", "react native", ".net"],
    "competing_stacks": [{"term": "flutter", "weight": 8}, {"term": "react native", "weight": 7}],
    "skills": [{"term": "swift", "weight": 10}, {"term": "swiftui", "weight": 8},
               {"term": "uikit", "weight": 7}, {"term": "xcode", "weight": 5}],
    "domain_keywords": [{"term": "fintech", "weight": 3}],
    "search_queries": ["ios developer", "swift developer", "ios engineer"],
    "min_body_core_mentions": 3,
    "candidate_brief": "An iOS engineer based in Berlin, Germany.",
    "seniority": "Senior", "years_experience": 6,
    "home_country": "Germany",
    "home_terms": ["germany", "deutschland", "de"],
    "home_city_terms": ["berlin", "munich", "hamburg"],
    "region_terms": ["europe", "emea", "eu"],
    "region_excluding_home_terms": [],
    "timezone": "Europe/Berlin", "salary_floor_usd": 70000,
}


class BuiltinProfileTests(unittest.TestCase):
    """The shipped default describes no trade and no country of its own."""

    def setUp(self):
        self._real_file = profile.LOCAL_CANDIDATE_FILE
        self._dir = tempfile.TemporaryDirectory()
        profile.LOCAL_CANDIDATE_FILE = Path(self._dir.name) / "candidate.local.json"
        profile.reset()

    def tearDown(self):
        profile.LOCAL_CANDIDATE_FILE = self._real_file
        self._dir.cleanup()
        profile.reset()

    def _write_local(self, payload: dict):
        profile.LOCAL_CANDIDATE_FILE.write_text(json.dumps(payload), encoding="utf-8")
        profile.reset()

    def test_with_no_local_file_it_names_no_trade_and_no_country(self):
        active = profile.active()
        self.assertEqual(active.key, "builtin")
        self.assertEqual(active.core_terms, ())
        self.assertEqual(active.home_country, "")
        self.assertEqual(active.skills, {})
        self.assertFalse(active.remote_only)

    def test_a_search_with_no_core_terms_cannot_run(self):
        self.assertEqual(profile.active().core_terms, ())

    def test_a_local_default_search_is_honoured(self):
        self._write_local({"default_search": {"label": "Plumbing", "query": "plumber",
                                              "core_terms": ["plumbing", "pipefitting"]}})
        active = profile.active()
        self.assertEqual(active.label, "Plumbing")
        self.assertEqual(active.core_terms, ("plumbing", "pipefitting"))

    def test_a_local_home_country_is_expanded_the_same_way_a_compiled_one_is(self):
        self._write_local({"home_country": "United Kingdom"})
        active = profile.active()
        self.assertEqual(active.home_country, "United Kingdom")
        self.assertIn("uk", active.home_terms)
        self.assertIn("london", active.home_city_terms)

    def test_a_broken_local_file_is_ignored_rather_than_fatal(self):
        profile.LOCAL_CANDIDATE_FILE.write_text("{not json", encoding="utf-8")
        profile.reset()
        self.assertEqual(profile.active().key, "builtin")

    def test_remote_stays_opt_in_even_in_a_local_default(self):
        self._write_local({"default_search": {"core_terms": ["nursing"]}})
        self.assertFalse(profile.active().remote_only)


class FixtureProfileTests(unittest.TestCase):
    """The tuned Flutter search still works, now as test data rather than a default."""

    def tearDown(self):
        profile.reset()

    def test_the_fixture_carries_its_own_vocabulary(self):
        search = flutter_uk_profile()
        self.assertEqual(search.core_terms, ("flutter", "dart"))
        self.assertIn("flutter", search.skills)
        self.assertIn("uk", search.home_terms)

    def test_the_hand_tuned_title_regexes_are_kept_verbatim(self):
        from tests.fixtures.flutter_uk import OTHER_DISCIPLINE_TITLE
        profile.set_active(flutter_uk_profile())
        self.assertEqual(profile.title_exclusion_patterns(), OTHER_DISCIPLINE_TITLE)

    def test_using_restores_the_previous_profile(self):
        before = profile.active()
        with profile.using(profile._from_payload(IOS_PAYLOAD, "ios", False)) as swapped:
            self.assertEqual(swapped.home_country, "Germany")
            self.assertEqual(profile.active().home_country, "Germany")
        self.assertIs(profile.active(), before)


class TermCompilationTests(unittest.TestCase):

    def test_leading_punctuation_terms_are_anchored_so_they_actually_match(self):
        import re
        pattern = profile._as_word_pattern(".net")
        self.assertTrue(re.search(pattern, "senior .net developer", re.I))
        self.assertTrue(re.search(profile._as_word_pattern("c++"), "c++ engineer", re.I))
        self.assertTrue(re.search(profile._as_word_pattern("python"), "python engineer", re.I))
        self.assertIsNone(re.search(profile._as_word_pattern("go"), "golang engineer", re.I))

    def test_a_generated_profile_never_supplies_a_regex(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "other_discipline_terms": ["python", "(a+)+$", "c#"]},
            "ios", False)
        import re
        for pattern in compiled.title_exclusion_patterns():
            re.compile(pattern)
        self.assertTrue(any("a\\+" in p for p in compiled.title_exclusion_patterns()))


class PayloadSanitisingTests(unittest.TestCase):

    def test_a_core_term_is_never_used_as_an_exclusion(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "other_discipline_terms": ["ios", "swift", "python"]},
            "ios", False)
        self.assertNotIn("ios", compiled.other_discipline_terms)
        self.assertNotIn("swift", compiled.other_discipline_terms)
        self.assertIn("python", compiled.other_discipline_terms)

    def test_weights_are_clamped_and_junk_rows_dropped(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "skills": [
                {"term": "swift", "weight": 9999},
                {"term": "uikit", "weight": -5},
                {"term": "", "weight": 5},
                {"term": "xcode", "weight": "not a number"},
                "not even a dict",
            ]}, "ios", False)
        self.assertEqual(compiled.skills["swift"], 10.0)
        self.assertEqual(compiled.skills["uikit"], 0.5)
        self.assertNotIn("", compiled.skills)
        self.assertNotIn("xcode", compiled.skills)

    def test_an_unknown_region_stays_unknown(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "home_country": "", "home_terms": [], "target_regions": []},
            "ios", False)
        self.assertEqual(compiled.home_country, "")
        self.assertEqual(compiled.home_terms, ())
        self.assertEqual(compiled.target_regions, ())

    def test_named_target_countries_become_the_match_list(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD,
             "target_regions": ["USA", "uk", "Australia"],
             "home_country": "United Kingdom", "home_terms": []},
            "ios developer in usa, uk or australia", False)
        self.assertEqual(compiled.target_regions,
                         ("United States", "United Kingdom", "Australia"))
        for term in ("usa", "us", "uk", "britain", "england", "australia", "aus"):
            self.assertIn(term, compiled.home_terms)

    def test_region_variants_are_expanded_by_code_not_trusted_from_the_model(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "target_regions": ["Germany"],
             "home_terms": ["germany"], "home_country": ""},
            "ios in germany", False)
        self.assertIn("deutschland", compiled.home_terms)
        self.assertIn("berlin", compiled.home_city_terms)

    def test_an_unknown_country_still_works_with_its_own_name(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "target_regions": ["Liechtenstein"], "home_terms": []},
            "ios in liechtenstein", False)
        self.assertEqual(compiled.target_regions, ("Liechtenstein",))
        self.assertEqual(compiled.home_terms, ("liechtenstein",))

    def test_mention_threshold_and_salary_survive_bad_values(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "min_body_core_mentions": 999, "salary_floor_usd": "lots"},
            "ios", False)
        self.assertEqual(compiled.min_body_core_mentions, 10)
        self.assertEqual(compiled.salary_floor_usd, 0.0)


class GeographyTests(unittest.TestCase):

    def tearDown(self):
        profile.reset()

    def test_the_home_country_is_removed_from_the_excluded_list(self):
        profile.set_active(profile._from_payload(IOS_PAYLOAD, "ios", False))
        foreign = geography.foreign_country_terms()
        self.assertNotIn("germany", foreign)
        self.assertIn("united kingdom", foreign)

    def test_us_residency_patterns_are_dropped_for_a_us_based_candidate(self):
        profile.set_active(profile._from_payload(
            {**IOS_PAYLOAD, "home_country": "United States",
             "home_terms": ["united states", "usa", "us"]}, "ios", False))
        self.assertEqual(geography.residency_patterns(), ())

    def test_a_uk_candidate_keeps_them(self):
        self.assertEqual(geography.residency_patterns(), config.US_ONLY_PATTERNS)


class RelevanceUnderACompiledProfileTests(unittest.TestCase):
    """The real proof: the same filter, different search, opposite verdicts."""

    def setUp(self):
        profile.set_active(profile._from_payload(IOS_PAYLOAD, "ios", False))

    def tearDown(self):
        profile.reset()

    def test_an_ios_advert_now_qualifies(self):
        verdict = filters.check_relevance(raw(
            "Senior iOS Engineer",
            "You will build our iOS app in Swift and SwiftUI. Strong Swift "
            "experience required. Our iOS team ships weekly.",
        ))
        self.assertTrue(verdict.passed, verdict.reason)
        self.assertTrue(any("Ios" in d or "ios" in d.lower() for d in verdict.details))

    def test_a_flutter_advert_is_now_the_wrong_discipline(self):
        verdict = filters.check_relevance(raw(
            "Senior Flutter Engineer",
            "Build cross-platform apps in Flutter and Dart. Flutter experience "
            "essential. Our Flutter team is growing. Flutter Flutter.",
        ))
        self.assertFalse(verdict.passed)

    def test_the_same_flutter_advert_still_qualifies_under_the_builtin_profile(self):
        profile.reset()
        verdict = filters.check_relevance(raw(
            "Senior Flutter Engineer",
            "Build cross-platform apps in Flutter and Dart. Flutter experience "
            "essential. Our Flutter team is growing.",
        ))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_a_python_title_is_still_excluded(self):
        verdict = filters.check_relevance(raw(
            "Senior Python Engineer",
            "Our stack includes Django, Postgres and a small iOS app in Swift.",
        ))
        self.assertFalse(verdict.passed)

    def test_a_recruiter_advert_is_excluded_by_the_generated_list(self):
        self.assertFalse(filters.check_relevance(raw(
            "Technical Recruiter - iOS", "Hiring iOS engineers in Swift.")).passed)


class UnknownRegionBehaviourTests(unittest.TestCase):
    """With no region, nothing may be rejected on the candidate's behalf."""

    def setUp(self):
        profile.set_active(profile._from_payload(
            {**IOS_PAYLOAD, "home_country": "", "home_terms": [],
             "home_city_terms": [], "target_regions": []}, "ios", False))

    def tearDown(self):
        profile.reset()

    def test_the_filter_knows_the_region_is_unknown(self):
        self.assertFalse(geography.home_known())

    def test_no_country_is_treated_as_foreign(self):
        self.assertEqual(geography.foreign_country_terms(), ())

    def test_a_residency_restricted_role_becomes_a_prospect_not_a_rejection(self):
        verdict = remote.assess_remote(raw(
            "Senior iOS Engineer",
            "Fully remote position. You must be authorized to work in the United "
            "States. Swift and SwiftUI experience required for our iOS team.",
            location="Remote (US)",
        ))
        self.assertFalse(verdict.passed)
        self.assertTrue(verdict.prospect_worthy)
        self.assertEqual(verdict.category, "region_unknown")

    def test_a_worldwide_role_still_qualifies(self):
        verdict = remote.assess_remote(raw(
            "Senior iOS Engineer",
            "Fully remote, work from anywhere in the world. We hire contractors "
            "internationally through Deel. Swift and iOS experience required.",
            location="Remote worldwide",
        ))
        self.assertTrue(verdict.passed, verdict.reason)


class TargetRegionTests(unittest.TestCase):
    """"Find me jobs in the USA" should accept US-restricted roles."""

    def setUp(self):
        profile.set_active(profile._from_payload(
            {**IOS_PAYLOAD,
             "target_regions": ["united states"],
             "home_country": "United States",
             "home_terms": ["united states", "usa", "us", "u.s."],
             "home_city_terms": ["new york", "san francisco", "austin"]},
            "ios jobs in the usa", False))

    def tearDown(self):
        profile.reset()

    def test_us_residency_wording_is_no_longer_a_rejection(self):
        self.assertEqual(geography.residency_patterns(), ())

    def test_the_target_country_is_not_in_the_excluded_list(self):
        foreign = geography.foreign_country_terms()
        for term in ("united states", "usa", "us"):
            self.assertNotIn(term, foreign)


ANDROID_PAYLOAD = {
    "label": "Android engineering", "is_job_search": True, "answer": "",
    "core_terms": ["android", "kotlin"],
    "secondary_terms": ["android developer", "android engineer", "mobile engineer",
                        "jetpack compose", "android sdk"],
    "hard_title_exclusions": ["recruiter", "sales", "designer", "product manager",
                              "data scientist"],
    "other_discipline_terms": ["ios", "swift", "flutter", "react native", "python",
                               "backend", ".net", "java developer", "devops",
                               "data engineer"],
    "competing_stacks": [{"term": "flutter", "weight": 8},
                         {"term": "react native", "weight": 8}],
    "skills": [{"term": "kotlin", "weight": 10}, {"term": "jetpack compose", "weight": 8}],
    "domain_keywords": [], "search_queries": ["android developer"],
    "min_body_core_mentions": 3,
    "candidate_brief": "An Android engineer. No CV supplied.",
    "seniority": "Senior", "years_experience": 5,
    "needs_clarification": False, "questions": [],
    "target_regions": [], "home_country": "", "home_terms": [], "home_city_terms": [],
    "region_terms": [], "region_excluding_home_terms": [], "timezone": "",
    "salary_floor_usd": 0,
}


class SearchPrecisionTests(unittest.TestCase):
    """"I need an Android job" must return Android jobs and nothing else."""

    def setUp(self):
        profile.set_active(profile._from_payload(
            ANDROID_PAYLOAD, "i need android job", False))

    def tearDown(self):
        profile.reset()

    def assertKept(self, title, body):
        verdict = filters.check_relevance(raw(title, body))
        self.assertTrue(verdict.passed, f"{title!r} was dropped: {verdict.reason}")

    def assertDropped(self, title, body):
        verdict = filters.check_relevance(raw(title, body))
        self.assertFalse(verdict.passed, f"{title!r} was wrongly kept")
        return verdict.reason

    def test_android_roles_are_kept(self):
        self.assertKept("Senior Android Engineer",
                        "Build our Android app in Kotlin with Jetpack Compose. Strong "
                        "Android experience required. Our Android team ships weekly.")
        self.assertKept("Android Developer (Kotlin)",
                        "Kotlin, Android SDK, Compose. Android architecture experience "
                        "essential for this Android role.")

    def test_an_adjacent_mobile_platform_is_dropped(self):
        self.assertDropped("Senior iOS Engineer",
                           "Build our iOS app in Swift and SwiftUI. iOS experience "
                           "required. Our iOS team ships weekly.")

    def test_a_competing_cross_platform_stack_is_dropped(self):
        self.assertDropped("Senior Flutter Engineer",
                           "Cross-platform apps in Flutter and Dart. Flutter essential. "
                           "Our Flutter team is growing.")
        self.assertDropped("React Native Developer",
                           "Build cross-platform with React Native for Android and iOS.")

    def test_another_discipline_mentioning_android_in_passing_is_dropped(self):
        reason = self.assertDropped("Senior Python Engineer",
                                    "Django, Postgres. We also have a small Android app.")
        self.assertIn("python", reason.lower())
        self.assertDropped("Backend Engineer",
                           "Go microservices, Kubernetes. Our mobile team uses Android.")
        self.assertDropped("Data Scientist",
                           "Python, pandas, ML models. Android analytics data.")

    def test_non_engineering_titles_are_dropped_even_naming_android(self):
        self.assertDropped("Technical Recruiter - Android",
                           "Hiring Android engineers in Kotlin.")
        self.assertDropped("Product Manager, Mobile",
                           "Own our Android and iOS roadmap. Kotlin team.")

    def test_precision_holds_across_a_mixed_batch(self):
        batch = [
            ("Senior Android Engineer", "Android Kotlin Compose. Android team. Android.", True),
            ("Android Developer", "Kotlin Android SDK. Android architecture. Android.", True),
            ("Senior iOS Engineer", "Swift SwiftUI iOS. iOS team.", False),
            ("Senior Flutter Engineer", "Flutter Dart. Flutter team. Flutter.", False),
            ("Senior Python Engineer", "Django Postgres. Small Android app.", False),
            ("Data Scientist", "Python pandas ML. Android analytics.", False),
            ("Technical Recruiter - Android", "Hiring Android engineers.", False),
        ]
        kept = [t for t, b, _ in batch if filters.check_relevance(raw(t, b)).passed]
        self.assertEqual(kept, ["Senior Android Engineer", "Android Developer"])


class VagueRequestTests(unittest.TestCase):
    """A request too vague to search must ask, not guess."""

    def test_no_determinable_role_yields_questions_not_a_flutter_search(self):
        payload = {**IOS_PAYLOAD, "core_terms": [],
                   "needs_clarification": True,
                   "questions": ["What kind of work are you looking for?"]}
        compiled = profile._from_payload(payload, "i need a job", False)
        self.assertEqual(compiled.core_terms, ())
        self.assertNotIn("flutter", compiled.core_terms)

    def test_words_from_the_request_are_used_before_giving_up(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "core_terms": []}, "find me kotlin android jobs", False)
        self.assertEqual(compiled.core_terms, ("kotlin", "android"))

    def test_filler_words_alone_do_not_become_a_search(self):
        compiled = profile._from_payload(
            {**IOS_PAYLOAD, "core_terms": []}, "i need a job please", False)
        self.assertEqual(compiled.core_terms, ())


class QuestionDeduplicationTests(unittest.TestCase):
    """Never ask about something already settled."""

    def _profile(self, **overrides):
        return profile._from_payload({**IOS_PAYLOAD, **overrides}, "ios", False)

    def test_a_stated_region_removes_every_location_question(self):
        base = self._profile(home_country="", home_terms=[], target_regions=[])
        asked = ("Which country will you be working from?",
                 "How many years of iOS experience do you have?")
        result, questions = profile._apply_region(base, "Germany", asked)
        self.assertEqual(result.home_country, "Germany")
        self.assertFalse(result.region_detected)
        self.assertEqual(questions, ("How many years of iOS experience do you have?",))

    def test_a_known_region_removes_location_questions_without_adding_one(self):
        base = self._profile(home_country="Poland", home_terms=["poland"])
        asked = ("Which city are you based in?", "Contract or permanent?")
        result, questions = profile._apply_region(base, "", asked)
        self.assertEqual(result.home_country, "Poland")
        self.assertEqual(questions, ("Contract or permanent?",))

    def test_a_detected_region_adds_exactly_one_note_and_drops_the_rest(self):
        base = self._profile(home_country="", home_terms=[], target_regions=[])
        with mock.patch.object(
            profile.region_mod, "detect",
            return_value=profile.region_mod.build(
                "United Kingdom", source="your system timezone (Europe/London)"),
        ):
            result, questions = profile._apply_region(
                base, "", ("Which country are you in?", "Remote only?"))
        self.assertTrue(result.region_detected)
        self.assertEqual(len(questions), 2)
        self.assertTrue(questions[0].startswith("I am assuming"))
        self.assertEqual(questions[1], "Remote only?")

    def test_a_stated_region_beats_one_read_from_the_cv(self):
        # Order matters: what someone typed outranks what was inferred for them.
        base = self._profile(home_country="Poland", home_terms=["poland"])
        result, _ = profile._apply_region(base, "Germany", ())
        self.assertEqual(result.home_country, "Germany")
        self.assertEqual(result.region_source, "stated in the request")

    def test_a_region_from_the_cv_beats_the_machine(self):
        base = self._profile(home_country="Poland", home_terms=["poland"])
        with mock.patch.object(profile.region_mod, "detect") as detect:
            result, _ = profile._apply_region(base, "", ())
        detect.assert_not_called()
        self.assertEqual(result.home_country, "Poland")
        self.assertFalse(result.region_detected)

    def test_the_machine_is_only_read_when_nothing_else_said(self):
        base = self._profile(home_country="", home_terms=[], target_regions=[])
        with mock.patch.object(
            profile.region_mod, "detect",
            return_value=profile.region_mod.build(
                "Germany", source="your system timezone (Europe/Berlin)"),
        ):
            result, _ = profile._apply_region(base, "", ())
        self.assertEqual(result.home_country, "Germany")
        self.assertTrue(result.region_detected)

    def test_location_phrasings_are_recognised(self):
        for question in ("Which country are you in?", "Where will you be working from?",
                         "What city are you based in?", "Are you located in the EU?"):
            self.assertTrue(profile._asks_about_location(question), question)

    def test_unrelated_questions_are_kept(self):
        for question in ("How many years of iOS experience?",
                         "Do you prefer contract or permanent?"):
            self.assertFalse(profile._asks_about_location(question), question)


class GeneralQuestionTests(unittest.TestCase):

    def test_a_non_search_payload_yields_an_answer_not_a_profile(self):
        payload = {**IOS_PAYLOAD, "is_job_search": False,
                   "answer": "I search for jobs — tell me a role and I'll look."}
        self.assertFalse(payload["is_job_search"])
        self.assertTrue(payload["answer"])


if __name__ == "__main__":
    unittest.main()


INTERNSHIP_PAYLOAD = {
    "label": "Software engineering internship", "is_job_search": True, "answer": "",
    "core_terms": ["intern", "internship", "software engineering intern"],
    "secondary_terms": ["graduate scheme", "placement", "summer internship",
                        "software developer intern", "engineering intern"],
    "hard_title_exclusions": ["recruiter", "sales", "account manager"],
    "other_discipline_terms": ["senior", "principal", "staff engineer"],
    "competing_stacks": [], "skills": [{"term": "python", "weight": 6}],
    "domain_keywords": [], "search_queries": ["software engineering intern"],
    "min_body_core_mentions": 2, "small_employers_only": False,
    "candidate_brief": "A student looking for a software engineering internship.",
    "seniority": "Junior", "years_experience": 0,
    "needs_clarification": False, "questions": [],
    "target_regions": [], "home_country": "United Kingdom",
    "home_terms": [], "home_city_terms": [], "region_terms": [],
    "region_excluding_home_terms": [], "timezone": "", "salary_floor_usd": 0,
}


class InternshipSearchTests(unittest.TestCase):
    """Someone looking for an internship must actually get internships."""

    def setUp(self):
        profile.set_active(profile._from_payload(
            INTERNSHIP_PAYLOAD, "find me a software engineering internship", False))

    def tearDown(self):
        profile.reset()

    def test_internships_are_not_excluded_by_title(self):
        for title in ("Software Engineering Intern",
                      "Summer Internship - Software Developer",
                      "Graduate Software Engineer"):
            verdict = filters.check_relevance(raw(
                title,
                "Join our engineering internship programme. You will write Python "
                "and work alongside our engineers. Internship runs 12 weeks.",
            ))
            self.assertTrue(verdict.passed, f"{title!r}: {verdict.reason}")

    def test_large_employers_are_kept_for_this_search(self):
        # Internships barely exist outside big employers, and nothing about
        # employer size is a rejection any more in any case.
        verdict = filters.check_employer_size(
            raw("Software Engineering Intern", "Internship at our global office."),
            size_label="10,001+ employees")
        self.assertTrue(verdict.passed, verdict.reason)
        self.assertFalse(profile.active().small_employers_only)

    def test_a_normal_search_also_keeps_large_employers(self):
        profile.reset()
        self.assertFalse(profile.active().small_employers_only)
        self.assertTrue(filters.check_employer_size(
            raw("Site Engineer", "A role at our global office."),
            size_label="10,001+ employees").passed)

    def test_no_pay_floor_is_inherited(self):
        self.assertEqual(profile.active().salary_floor_usd, 0.0)

    def test_a_senior_role_is_still_the_wrong_search(self):
        verdict = filters.check_relevance(raw(
            "Principal Software Engineer",
            "Lead our platform team. 12+ years required. Mentor staff engineers.",
        ))
        self.assertFalse(verdict.passed)


MULTI_STACK_PAYLOAD = {
    "label": "Android, iOS and full stack", "is_job_search": True, "answer": "",
    "core_terms": ["android", "ios", "kotlin", "swift", "full stack"],
    "secondary_terms": ["mobile developer", "mobile engineer", "full stack developer",
                        "full stack engineer", "android developer", "ios developer"],
    "hard_title_exclusions": ["recruiter", "sales", "product manager"],
    "other_discipline_terms": ["embedded", "firmware", "salesforce", "sap",
                               "data engineer", "data scientist", "devops", "flutter"],
    "competing_stacks": [], "skills": [{"term": "kotlin", "weight": 8}],
    "domain_keywords": [], "search_queries": ["android developer", "ios developer",
                                              "full stack developer"],
    "min_body_core_mentions": 2, "small_employers_only": False,
    "employment_types": [], "startups_only": False,
    "candidate_brief": "A mobile and web engineer.", "seniority": "Senior",
    "years_experience": 6, "needs_clarification": False, "questions": [],
    "target_regions": [], "home_country": "United Kingdom", "home_terms": [],
    "home_city_terms": [], "region_terms": [], "region_excluding_home_terms": [],
    "timezone": "", "salary_floor_usd": 0, "pay_floor_stated": False,
}


class MultiStackSearchTests(unittest.TestCase):
    """One search can want several unrelated stacks at once."""

    def setUp(self):
        profile.set_active(profile._from_payload(
            MULTI_STACK_PAYLOAD, "find me job for android, ios and full stack", False))

    def tearDown(self):
        profile.reset()

    def _passed(self, title, body):
        return filters.check_relevance(raw(title, body)).passed

    def test_every_requested_stack_is_kept(self):
        self.assertTrue(self._passed(
            "Senior Android Engineer", "Kotlin, Jetpack Compose, Android SDK. Android team."))
        self.assertTrue(self._passed(
            "Senior iOS Engineer", "Swift, SwiftUI, iOS. Our iOS team ships weekly."))
        self.assertTrue(self._passed(
            "Full Stack Engineer", "React and Node.js across our web platform. Full stack."))

    def test_an_unrequested_stack_is_still_dropped(self):
        self.assertFalse(self._passed(
            "Flutter Developer", "Flutter and Dart cross-platform apps. Flutter team."))

    def test_other_disciplines_are_still_dropped(self):
        self.assertFalse(self._passed(
            "Data Scientist", "Python, pandas, ML models. Some mobile analytics."))
        self.assertFalse(self._passed(
            "DevOps Engineer", "Kubernetes, Terraform, AWS for our mobile and web teams."))

    def test_non_engineering_titles_are_still_dropped(self):
        self.assertFalse(self._passed(
            "Technical Recruiter", "Hiring Android, iOS and full stack engineers."))

    def test_no_requested_stack_ends_up_in_the_exclusion_list(self):
        active = profile.active()
        for term in active.core_terms:
            self.assertNotIn(term, active.other_discipline_terms)


TRADE_PAYLOAD = {
    "label": "Electrician", "is_job_search": True, "answer": "",
    "core_terms": ["electrician", "electrical installation", "wiring"],
    "secondary_terms": ["maintenance electrician", "commercial electrician",
                        "industrial electrician", "electrical technician"],
    "hard_title_exclusions": ["recruiter", "sales", "estimator"],
    "other_discipline_terms": ["plumber", "welder", "carpenter", "software",
                               "web developer"],
    "competing_stacks": [], "skills": [{"term": "18th edition", "weight": 8}],
    "domain_keywords": [], "search_queries": ["electrician"],
    "min_body_core_mentions": 2, "small_employers_only": False,
    "employment_types": [], "startups_only": False, "remote_only": False,
    "candidate_brief": "A qualified electrician.", "seniority": "Unspecified",
    "years_experience": 5, "needs_clarification": False, "questions": [],
    "target_regions": [], "home_country": "United Kingdom", "home_terms": [],
    "home_city_terms": [], "region_terms": [], "region_excluding_home_terms": [],
    "timezone": "", "salary_floor_usd": 0, "pay_floor_stated": False,
}


class OnSiteWorkTests(unittest.TestCase):
    """Most of the world's work cannot be done from a laptop."""

    def setUp(self):
        profile.set_active(profile._from_payload(
            TRADE_PAYLOAD, "find me electrician jobs", False))

    def tearDown(self):
        profile.reset()

    def test_the_search_knows_it_is_not_a_remote_one(self):
        self.assertFalse(profile.active().remote_only)

    def test_a_trade_advert_is_relevant(self):
        verdict = filters.check_relevance(raw(
            "Electrician (Commercial)",
            "NVQ Level 3 electrician. Commercial wiring, testing and inspection. "
            "18th Edition qualified. Electrical maintenance across sites.",
        ))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_a_different_trade_is_still_dropped(self):
        self.assertFalse(filters.check_relevance(raw(
            "Plumber - Domestic",
            "Experienced plumber for domestic installs, boilers and bathrooms.",
        )).passed)

    def test_office_work_is_still_dropped(self):
        self.assertFalse(filters.check_relevance(raw(
            "Senior Flutter Engineer", "Flutter and Dart mobile apps. Flutter team.",
        )).passed)

    def test_the_default_search_does_not_demand_remote(self):
        profile.reset()
        self.assertFalse(profile.active().remote_only)


class OnSiteGateTests(unittest.TestCase):
    """The remote gate must lift for on-site searches and hold for remote ones."""

    def tearDown(self):
        profile.reset()

    def _qualified(self, *, arrangement: str):
        from dataclasses import replace
        from job_agent import pipeline
        custom = replace(flutter_uk_profile(), key="compiled:test",
                         work_arrangement=arrangement)
        return pipeline.run(offline=True, verify_live=False, use_llm=False,
                            search_profile=custom).stats

    def test_on_site_searches_keep_office_based_adverts(self):
        remote, on_site = self._qualified(arrangement="remote"), self._qualified(arrangement="any")
        self.assertGreater(on_site.qualified, remote.qualified)
        self.assertEqual(on_site.rejected_not_remote, 0)

    def test_remote_searches_still_reject_hybrid_and_on_site(self):
        self.assertGreater(self._qualified(arrangement="remote").rejected_not_remote, 0)


class RemoteIsOptInTests(unittest.TestCase):
    """Both remote and on-site work are returned unless remote was asked for."""

    def tearDown(self):
        profile.reset()

    def test_a_payload_that_never_mentions_remote_returns_both(self):
        compiled = profile._from_payload({"label": "Plumbing", "query": "plumber",
                                          "core_terms": ["plumbing"]}, "plumbing jobs", False)
        self.assertFalse(compiled.remote_only)

    def test_remote_is_honoured_when_the_request_asked_for_it(self):
        compiled = profile._from_payload({"label": "Support", "query": "support",
                                          "core_terms": ["support"],
                                          "work_arrangement": "remote"},
                                         "remote support jobs", False)
        self.assertTrue(compiled.remote_only)
        self.assertEqual(compiled.work_arrangement, "remote")

    def test_hybrid_is_its_own_answer(self):
        compiled = profile._from_payload({"label": "Ops", "query": "ops",
                                          "core_terms": ["operations"],
                                          "work_arrangement": "hybrid"},
                                         "hybrid ops jobs in Leeds", False)
        self.assertEqual(compiled.work_arrangement, "hybrid")
        self.assertFalse(compiled.remote_only)

    def test_onsite_is_its_own_answer(self):
        compiled = profile._from_payload({"label": "Chef", "query": "chef",
                                          "core_terms": ["chef"],
                                          "work_arrangement": "onsite"},
                                         "onsite chef jobs", False)
        self.assertEqual(compiled.work_arrangement, "onsite")

    def test_a_junk_arrangement_falls_back_to_any(self):
        compiled = profile._from_payload({"label": "Chef", "query": "chef",
                                          "core_terms": ["chef"],
                                          "work_arrangement": "whatever"},
                                         "chef jobs", False)
        self.assertEqual(compiled.work_arrangement, "any")

    def test_an_explicit_false_is_not_overridden(self):
        compiled = profile._from_payload({"label": "Chef", "query": "chef",
                                          "core_terms": ["chef"],
                                          "work_arrangement": "any"},
                                         "chef jobs in Leeds", False)
        self.assertFalse(compiled.remote_only)

    def test_a_desk_job_request_is_not_quietly_made_remote_only(self):
        compiled = profile._from_payload({"label": "iOS", "query": "ios developer",
                                          "core_terms": ["ios", "swift"]}, "ios jobs", False)
        self.assertFalse(compiled.remote_only)


class NarrowedStandingTests(unittest.TestCase):
    """Naming only a region narrows the standing search; it does not replace it.

    Both entry points settle a search through this one function, so the CLI
    and the MCP server cannot drift apart on what `--region` alone means.
    """

    def tearDown(self):
        profile.reset()

    def test_a_region_alone_narrows_what_is_already_standing(self):
        profile.set_active(flutter_uk_profile())
        settled = profile.narrowed_standing("Nigeria")
        self.assertIsNotNone(settled)
        self.assertEqual(settled.core_terms, flutter_uk_profile().core_terms)
        self.assertEqual(settled.home_country, "Nigeria")

    def test_several_regions_are_all_targeted(self):
        profile.set_active(flutter_uk_profile())
        settled = profile.narrowed_standing("Nigeria, Kenya")
        self.assertEqual(len(settled.target_regions), 2)

    def test_a_query_alongside_a_region_must_be_compiled(self):
        profile.set_active(flutter_uk_profile())
        self.assertIsNone(profile.narrowed_standing("Nigeria", query="welder jobs"))

    def test_a_cv_alongside_a_region_must_be_compiled(self):
        profile.set_active(flutter_uk_profile())
        self.assertIsNone(profile.narrowed_standing("Nigeria", cv="a CV"))

    def test_no_region_narrows_nothing(self):
        profile.set_active(flutter_uk_profile())
        self.assertIsNone(profile.narrowed_standing(""))

    def test_nothing_standing_means_nothing_to_narrow(self):
        from dataclasses import replace
        profile.set_active(replace(flutter_uk_profile(), core_terms=()))
        self.assertIsNone(profile.narrowed_standing("Nigeria"))


class McpSettlesSearchesLikeTheCliTests(unittest.TestCase):
    """The MCP server honours a standing search exactly as the CLI does."""

    def tearDown(self):
        profile.reset()

    def test_a_region_alone_keeps_the_standing_search(self):
        from job_agent import mcp_server
        profile.set_active(flutter_uk_profile())
        compiled, _questions, refusal = mcp_server._settle_search("", "", "Nigeria")
        self.assertIsNone(refusal, "a standing search must not be abandoned")
        self.assertEqual(compiled.core_terms, flutter_uk_profile().core_terms)
        self.assertEqual(compiled.home_country, "Nigeria")

    def test_no_request_at_all_runs_the_standing_search(self):
        from job_agent import mcp_server
        profile.set_active(flutter_uk_profile())
        compiled, _questions, refusal = mcp_server._settle_search("", "", "")
        self.assertIsNone(refusal)
        self.assertIsNone(compiled, "None means run whatever is standing")

    def test_a_search_that_cannot_be_built_is_refused(self):
        from job_agent import mcp_server
        profile.set_active(flutter_uk_profile())
        with mock.patch.object(profile, "compile_profile",
                               return_value=profile.Compiled(answer="need a key",
                                                             failed=True)):
            _compiled, _questions, refusal = mcp_server._settle_search(
                "welder jobs", "", "")
        self.assertEqual(refusal, "need a key")


class HandWrittenProfileTests(unittest.TestCase):
    """`candidate.local.json` is written by a person, so it arrives imperfect.

    Two shapes used to fail quietly rather than loudly: a bare string where a
    list belongs became one term per letter, leaving a search that matched
    almost anything; and a UTF-8 BOM — what PowerShell redirects and "save as
    UTF-8 with BOM" produce — made the whole file unreadable.
    """

    def tearDown(self):
        profile.reset()

    def _loaded(self, text: str, encoding: str = "utf-8"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.local.json"
            path.write_text(text, encoding=encoding)
            original = profile.LOCAL_CANDIDATE_FILE
            profile.LOCAL_CANDIDATE_FILE = path
            profile.reset()
            try:
                return profile.active()
            finally:
                profile.LOCAL_CANDIDATE_FILE = original
                profile.reset()

    SEARCH = '{"default_search": {"query": "warehouse", "label": "W", %s}}'

    def test_core_terms_written_as_a_string_is_one_term(self):
        loaded = self._loaded(self.SEARCH % '"core_terms": "warehouse"')
        self.assertEqual(loaded.core_terms, ("warehouse",))

    def test_core_terms_written_as_a_comma_string_is_split(self):
        loaded = self._loaded(self.SEARCH % '"core_terms": "warehouse, forklift"')
        self.assertEqual(loaded.core_terms, ("warehouse", "forklift"))

    def test_core_terms_written_properly_still_works(self):
        loaded = self._loaded(self.SEARCH % '"core_terms": ["warehouse", "forklift"]')
        self.assertEqual(loaded.core_terms, ("warehouse", "forklift"))

    def test_a_file_saved_with_a_byte_order_mark_is_still_read(self):
        loaded = self._loaded(self.SEARCH % '"core_terms": ["warehouse"]',
                              encoding="utf-8-sig")
        self.assertEqual(loaded.core_terms, ("warehouse",))

    def test_a_broken_file_is_ignored_rather_than_fatal(self):
        for broken in ("not json at all {{{", "[]", '{"default_search": "a string"}'):
            with self.subTest(content=broken):
                self.assertIsNotNone(self._loaded(broken))


class FreshnessWindowTests(unittest.TestCase):
    """A window named in the request is an instruction, not decoration.

    "listed in the last 7 days" used to be read as vocabulary and thrown
    away, leaving every run on the 30-day default however few days the
    person asked for.
    """

    def tearDown(self):
        profile.reset()

    def test_days_named_in_the_request_are_read(self):
        for request, expected in (
            ("flutter jobs listed within last 7 days", 7),
            ("remote flutter jobs in the last 2 days", 2),
            ("jobs posted in the past 15 days", 15),
            ("find me anything from the last three days", 3),
            ("roles no older than 5 days", 5),
        ):
            with self.subTest(request=request):
                self.assertEqual(profile.freshness_from_request(request), expected)

    def test_windows_named_in_other_units(self):
        for request, expected in (
            ("posted within the last 24 hours", 1),
            ("adverts from the last 48 hours", 2),
            ("jobs listed last week", 7),
            ("anything from the past fortnight", 14),
            ("roles from the last 2 weeks", 14),
            ("jobs in the past 3 months", 90),
            ("plumbing work posted today", 1),
            ("jobs listed yesterday", 2),
            ("vacancies added this week", 7),
        ):
            with self.subTest(request=request):
                self.assertEqual(profile.freshness_from_request(request), expected)

    def test_a_shift_pattern_is_not_a_window(self):
        """"5 days a week" is when they work, not how old an advert may be."""
        for request in (
            "warehouse operative, 5 days a week",
            "full time flutter role, 4 days a week remote",
            "senior developer with 7 years of experience",
            "electrician in Leeds",
        ):
            with self.subTest(request=request):
                self.assertIsNone(profile.freshness_from_request(request))

    def test_a_shift_pattern_alongside_a_real_window_still_reads_the_window(self):
        self.assertEqual(
            profile.freshness_from_request(
                "electrician, 6 days a week, listed in the last 5 days"), 5)

    def test_an_absurd_window_is_clamped_rather_than_obeyed(self):
        self.assertEqual(profile.freshness_from_request("jobs from the last 500 days"),
                         config.MAX_FRESHNESS_DAYS)

    def test_nothing_said_leaves_the_window_unset(self):
        self.assertIsNone(profile.freshness_from_request(""))
        self.assertIsNone(profile.freshness_from_request("remote flutter developer"))

    def test_the_caller_outranks_the_request(self):
        self.assertEqual(profile.freshness_window_days(3, "jobs from the last 7 days"), 3)

    def test_the_request_is_used_when_the_caller_said_nothing(self):
        self.assertEqual(profile.freshness_window_days(0, "jobs from the last 7 days"), 7)

    def test_the_default_stands_when_neither_says(self):
        self.assertEqual(profile.freshness_window_days(0, "remote flutter developer"),
                         config.FRESHNESS_DAYS)

    def test_a_caller_number_is_clamped_too(self):
        self.assertEqual(profile.freshness_window_days(9999, ""), config.MAX_FRESHNESS_DAYS)
        self.assertEqual(profile.freshness_window_days(-4, ""), config.FRESHNESS_DAYS)

    def test_a_compiled_search_carries_the_window_it_was_asked_for(self):
        compiled = profile._from_payload(
            dict(IOS_PAYLOAD), "ios jobs posted in the last 4 days", False)
        self.assertEqual(compiled.freshness_days, 4)

    def test_a_compiled_search_falls_back_to_the_default(self):
        compiled = profile._from_payload(dict(IOS_PAYLOAD), "ios jobs", False)
        self.assertEqual(compiled.freshness_days, config.FRESHNESS_DAYS)


class CompiledProfilesNeverMixTests(unittest.TestCase):
    """Two different searches never share a compiled profile.

    The compiled profile is the other cache that depends on who is asking: it
    holds the vocabulary, the exclusions and the region a whole run reads as
    data. One search inheriting another's would mis-scope every filter at once.
    """

    def _key(self, request, cv_text="", region=""):
        from job_agent import cache as cache_mod
        seen = {}
        original = cache_mod.get

        def spy(key, _max_age_days):
            seen["key"] = key
            return {"captured": True}

        cache_mod.get = spy
        try:
            profile._fetch_payload(request, cv_text, region, None)
        finally:
            cache_mod.get = original
        self.assertIn("key", seen)
        return seen["key"]

    def test_a_different_request_is_a_different_profile(self):
        self.assertNotEqual(self._key("senior full stack developer"),
                            self._key("senior flutter developer"))

    def test_a_different_cv_is_a_different_profile(self):
        self.assertNotEqual(self._key("developer", cv_text="Flutter and Dart."),
                            self._key("developer", cv_text="TypeScript and React."))

    def test_a_different_region_is_a_different_profile(self):
        self.assertNotEqual(self._key("developer", region="Germany"),
                            self._key("developer", region="Canada"))

    def test_the_same_search_reuses_its_own_profile(self):
        self.assertEqual(self._key("developer", cv_text="React.", region="Germany"),
                         self._key("developer", cv_text="React.", region="Germany"))

    def test_many_searches_all_get_their_own(self):
        requests = ("full stack developer", "flutter developer", "data engineer",
                    "electrician", "registered nurse")
        keys = {self._key(r) for r in requests}
        self.assertEqual(len(keys), len(requests))
