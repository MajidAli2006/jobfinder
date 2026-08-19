"""Tests for the strict remote-only / UK-eligibility gate."""

from __future__ import annotations

import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

from job_agent import cache, config, filters, geography, profile, remote, scoring
from job_agent.models import Job, RawJob
from job_agent.utils import freshness_window, now_local

from .fixtures import flutter_uk_profile


def setUpModule():
    """Every test here judges adverts against the tuned Flutter/UK profile."""
    profile.set_active(flutter_uk_profile())


def tearDownModule():
    profile.reset()


def posting(title="Senior Flutter Developer", location="", description="",
            company="Acme", tags=None, days_ago=0, **extra) -> RawJob:
    return RawJob(
        source="test",
        source_id="test-1",
        title=title,
        company=company,
        url="https://example.com/job",
        description=description,
        location_raw=location,
        posted_at=now_local() - timedelta(days=days_ago, hours=1),
        tags=tags or [],
        extra=extra,
    )


class RemoteOnlyTests(unittest.TestCase):
    """Nothing hybrid or office-based may ever pass."""

    def assert_rejected(self, job: RawJob, category="not_remote"):
        verdict = remote.assess_remote(job)
        self.assertFalse(verdict.passed, f"should have been rejected: {job.description[:80]}")
        self.assertEqual(verdict.category, category)
        return verdict

    def test_hybrid_keyword_rejected(self):
        self.assert_rejected(posting(location="London, UK (Hybrid)",
                                     description="Hybrid role, fully remote otherwise."))

    def test_days_per_week_in_office_rejected(self):
        for phrase in (
            "3 days a week in the office, 2 days remote",
            "two days per week onsite in our London office",
            "Remote role with 1 day a week in the office",
        ):
            with self.subTest(phrase=phrase):
                self.assert_rejected(posting(location="Remote UK", description=phrase))

    def test_weekly_and_monthly_office_attendance_rejected(self):
        for phrase in (
            "Remote, but weekly office attendance is expected in Manchester.",
            "Fully remote with monthly onsite attendance required.",
            "Remote working with once a month in the office.",
        ):
            with self.subTest(phrase=phrase):
                self.assert_rejected(posting(location="Remote UK", description=phrase))

    def test_onsite_and_relocation_rejected(self):
        self.assert_rejected(posting(location="Dubai, UAE",
                                     description="On-site role. Relocation is required."))
        self.assert_rejected(posting(location="Remote",
                                     description="Must be able to commute to our Leeds office."))

    def test_negated_hybrid_is_not_a_rejection(self):
        """'no hybrid nonsense' must not be read as a hybrid requirement."""
        verdict = remote.assess_remote(posting(
            location="Remote, United Kingdom",
            description="100% remote. No hybrid, no commute, no office. UK based team.",
        ))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_quantifier_negated_office_mention_is_not_a_rejection(self):
        """Real Canonical wording: "very few office-based roles" describes a remote
        company, and must not be read as an office requirement.
        """
        verdict = remote.assess_remote(posting(
            location="Home based - Worldwide",
            description="We are a pioneer of globally distributed collaboration, with "
                        "1100+ colleagues in 75+ countries and very few office-based "
                        "roles. Teams meet two to four times yearly in person.",
        ))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_optional_office_is_not_a_rejection(self):
        verdict = remote.assess_remote(posting(
            location="Remote, United Kingdom",
            description="Fully remote. Office access is optional if you prefer a desk."))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_remote_must_be_stated(self):
        verdict = remote.assess_remote(posting(
            location="United Kingdom",
            description="Great Flutter role with a strong team."))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "not_remote")

    def test_occasional_offsite_allowed_but_flagged(self):
        verdict = remote.assess_remote(posting(
            location="Remote UK",
            description="Fully remote. We meet quarterly for a company retreat."))
        self.assertTrue(verdict.passed, verdict.reason)
        self.assertTrue(any("travel" in c.lower() for c in verdict.concerns))


class HomeEligibilityTests(unittest.TestCase):
    """'Remote' is never assumed to mean 'remote from where the candidate is'."""

    def test_remote_uk_accepted(self):
        verdict = remote.assess_remote(posting(location="Remote (UK)",
                                                description="Fully remote within the UK."))
        self.assertTrue(verdict.passed, verdict.reason)
        self.assertIn(geography.home_label(), verdict.remote_status)

    def test_worldwide_accepted(self):
        for location in ("Remote - Worldwide", "Work from anywhere", "Anywhere in the world"):
            with self.subTest(location=location):
                verdict = remote.assess_remote(
                    posting(location=location, description="Fully remote, work from anywhere."))
                self.assertTrue(verdict.passed, verdict.reason)

    def test_us_only_rejected(self):
        for phrase in (
            "Must be authorized to work in the United States.",
            "US-based candidates only.",
            "You must reside in the USA.",
            "Remote (US) — green card holder required.",
        ):
            with self.subTest(phrase=phrase):
                verdict = remote.assess_remote(posting(location="Remote", description=phrase))
                self.assertFalse(verdict.passed, phrase)
                self.assertEqual(verdict.category, "ineligible")

    def test_eu_only_rejected(self):
        verdict = remote.assess_remote(posting(
            location="Remote - EU only",
            description="Fully remote but you must hold an EU passport."))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "ineligible")

    def test_europe_without_uk_is_rejected_as_prospect(self):
        verdict = remote.assess_remote(posting(
            location="Remote - Europe",
            description="Fully remote within European time zones."))
        self.assertFalse(verdict.passed, "Europe alone must not be assumed UK-eligible")
        self.assertEqual(verdict.category, "ineligible")
        self.assertTrue(verdict.prospect_worthy)

    def test_europe_with_explicit_uk_accepted(self):
        verdict = remote.assess_remote(posting(
            location="Remote - EMEA",
            description="Remote across EMEA including the UK; we employ via our UK entity."))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_single_country_restriction_rejected(self):
        verdict = remote.assess_remote(posting(
            location="Remote",
            description="Fully remote. Candidates must reside in Germany for payroll reasons."))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "ineligible")

    def test_unstated_region_is_not_assumed_uk(self):
        verdict = remote.assess_remote(posting(
            location="Remote",
            description="Fully remote Flutter role. Competitive salary."))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "ineligible")
        self.assertTrue(verdict.prospect_worthy)

    def test_international_contractor_accepted(self):
        verdict = remote.assess_remote(posting(
            location="Remote",
            description="US company hiring international contractors through Deel, "
                        "our employer of record. We work with contractors worldwide."))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_ukraine_is_not_read_as_uk(self):
        verdict = remote.assess_remote(posting(
            location="Remote (Ukraine)",
            description="Fully remote. Candidates must be located in Ukraine."))
        self.assertFalse(verdict.passed)

    def test_anywhere_in_england_is_home_not_worldwide(self):
        verdict = remote.assess_remote(posting(
            location="Work from home - anywhere in England, Scotland or Wales",
            description="Remote-first company, no office."))
        self.assertTrue(verdict.passed, verdict.reason)
        self.assertEqual(verdict.remote_status, f"Remote — {geography.home_label()}")

    def test_uk_office_mention_does_not_unlock_a_us_only_role(self):
        verdict = remote.assess_remote(posting(
            location="Remote - US",
            description="We have a UK office in London. This role is remote within the "
                        "United States; you must be authorized to work in the US."))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "ineligible")


class RelevanceTests(unittest.TestCase):

    def test_flutter_role_relevant(self):
        self.assertTrue(filters.check_relevance(
            posting(title="Senior Flutter Developer", description="Flutter and Dart.")).passed)

    def test_international_does_not_match_intern(self):
        verdict = filters.check_relevance(posting(
            title="Flutter Engineer (International Contractor)", description="Flutter, Dart."))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_business_role_rejected(self):
        verdict = filters.check_relevance(posting(
            title="Senior Publisher Partnerships, Mobile App",
            description="Own partner relationships for our mobile app portfolio."))
        self.assertFalse(verdict.passed)

    def test_recruiter_rejected(self):
        self.assertFalse(filters.check_relevance(posting(
            title="Technical Recruiter - Mobile", description="Hiring Flutter engineers.")).passed)

    def test_react_native_role_rejected(self):
        self.assertFalse(filters.check_relevance(posting(
            title="Senior React Native Developer",
            description="React Native and TypeScript.")).passed)

    def test_boilerplate_flutter_mention_does_not_qualify_other_disciplines(self):
        """Regression: Canonical lists Flutter in every advert's stack blurb, which made
        "Python Engineer" look like a Flutter job and flooded the report.
        """
        for title in ("Python Engineer", "Ubuntu Software Engineer",
                      "Senior Golang Engineer", "Data Engineer"):
            with self.subTest(title=title):
                verdict = filters.check_relevance(posting(
                    title=title,
                    description="We are a global software company. Our stack includes "
                                "Python, Go, Rust, React and Flutter. You will work on "
                                "backend services and packaging.",
                ))
                self.assertFalse(verdict.passed, title)

    def test_search_keywords_in_the_url_are_not_evidence(self):
        """Regression: aggregator links carry the search that found them."""
        job = posting(title="WordPress Developer",
                      description="Build and maintain WordPress themes for clients.")
        job.url = ("https://jooble.org/away/-4411855510134666605"
                   "?p=1&rgn=55127&ckey=dart+developer+remote")
        verdict = filters.check_relevance(job)
        self.assertFalse(verdict.passed, verdict.reason)

    def test_ats_slug_in_the_url_path_is_still_read(self):
        job = posting(title="Senior Software Engineer", description="Join our team.")
        job.url = "https://boards.greenhouse.io/acme/jobs/senior-flutter-engineer-dart-flutter-flutter"
        self.assertTrue(filters.check_relevance(job).passed)

    def test_one_dart_mention_is_boilerplate_like_one_flutter_mention(self):
        verdict = filters.check_relevance(posting(
            title="Senior Blockchain Developer",
            description="Solidity, Rust and Go. Our wider stack also touches Dart.",
        ))
        self.assertFalse(verdict.passed, verdict.reason)

    def test_dotnet_title_rejected(self):
        for title in ("Junior Software Developer (.NET / Power Platform)",
                      "Senior .NET Developer", "ASP.NET Engineer"):
            with self.subTest(title=title):
                self.assertFalse(filters.check_relevance(posting(
                    title=title,
                    description="C# and .NET work. We also use Flutter somewhere.",
                )).passed, title)

    def test_ios_engineer_is_a_mobile_role(self):
        verdict = filters.check_relevance(posting(
            title="iOS Engineer",
            description="Build and ship our iOS app in Swift.",
        ))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_generic_title_with_substantive_flutter_body_passes(self):
        verdict = filters.check_relevance(posting(
            title="Senior Software Engineer",
            description="You will own our Flutter app. Deep Flutter experience required, "
                        "Flutter testing, Flutter CI, and Dart across iOS and Android. "
                        "We ship Flutter to three platforms.",
        ))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_flutter_in_title_always_passes(self):
        verdict = filters.check_relevance(posting(
            title="Web Frontend Engineer - JS, CSS, React, Flutter",
            description="Front end work using React and Flutter."))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_partnership_opportunity_with_flutter_in_title_passes(self):
        verdict = filters.check_relevance(posting(
            title="Flutter Development Partner / White Label Agency Wanted",
            description="Looking for a Flutter software house on retainer."))
        self.assertTrue(verdict.passed, verdict.reason)


class ExperienceLevelTests(unittest.TestCase):
    """Beginner / Medium / Senior bucketing used to tailor the CV."""

    def level(self, title, description=""):
        from job_agent import classify
        return classify.classify_all(posting(title=title, description=description))[
            "experience_level"]

    def test_title_seniority_wins(self):
        self.assertEqual(self.level("Senior Flutter Developer"), config.LEVEL_SENIOR)
        self.assertEqual(self.level("Junior Flutter Developer"), config.LEVEL_BEGINNER)
        self.assertEqual(self.level("Mid-Level Flutter Developer"), config.LEVEL_MEDIUM)
        self.assertEqual(self.level("Lead Flutter Engineer"), config.LEVEL_SENIOR)
        self.assertEqual(self.level("Principal Flutter Engineer"), config.LEVEL_SENIOR)

    def test_years_used_when_title_is_neutral(self):
        self.assertEqual(
            self.level("Flutter Developer", "You have 1-2 years of experience with Flutter."),
            config.LEVEL_BEGINNER)
        self.assertEqual(
            self.level("Flutter Developer", "We need 4 years experience building Flutter apps."),
            config.LEVEL_MEDIUM)
        self.assertEqual(
            self.level("Flutter Developer", "8+ years of experience in mobile development."),
            config.LEVEL_SENIOR)

    def test_entry_level_phrasing(self):
        self.assertEqual(
            self.level("Flutter Developer", "This is an entry-level role; Flutter basics only."),
            config.LEVEL_BEGINNER)
        self.assertEqual(
            self.level("Flutter Developer", "Graduate developer scheme. Flutter and Dart taught."),
            config.LEVEL_BEGINNER)

    def test_senior_phrasing_without_a_senior_title(self):
        self.assertEqual(
            self.level("Flutter Developer",
                       "You will be mentoring the team and owning the architecture. Flutter."),
            config.LEVEL_SENIOR)

    def test_unstated_level_is_not_guessed(self):
        self.assertEqual(
            self.level("Flutter Developer", "Build our Flutter app. Great team, great product."),
            config.LEVEL_UNSPECIFIED)

    def test_title_beats_a_conflicting_years_requirement(self):
        self.assertEqual(
            self.level("Senior Flutter Developer", "2 years of experience required."),
            config.LEVEL_SENIOR)


class PayFloorTests(unittest.TestCase):
    """Full-time roles must clear the USD pay floor, when a floor was set."""

    FLOOR = 50_000

    def job(self, **kwargs):
        from job_agent.models import Job
        kwargs.setdefault("employment_type", "Full Time")
        return Job(**kwargs)

    def check(self, job, **kwargs):
        kwargs.setdefault("floor_usd", self.FLOOR)
        return filters.check_pay_floor(job, **kwargs)

    def test_no_floor_means_no_rejection(self):
        verdict = filters.check_pay_floor(
            self.job(salary_min=1_000, salary_max=2_000, salary_currency="USD"))
        self.assertTrue(verdict.passed, "a search with no stated floor rejects no pay")

    def test_below_floor_rejected(self):
        verdict = self.check(
            self.job(salary_min=20_000, salary_max=30_000, salary_currency="USD"))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "low_pay")

    def test_above_floor_accepted(self):
        verdict = self.check(
            self.job(salary_min=70_000, salary_max=95_000, salary_currency="USD"))
        self.assertTrue(verdict.passed)

    def test_top_of_band_counts(self):
        """A band straddling the floor is kept — the top is negotiable."""
        verdict = self.check(
            self.job(salary_min=40_000, salary_max=65_000, salary_currency="USD"))
        self.assertTrue(verdict.passed)

    def test_low_currency_salary_rejected(self):
        verdict = self.check(
            self.job(salary_min=1_200_000, salary_max=1_800_000, salary_currency="INR"))
        self.assertFalse(verdict.passed, "1.8M INR is roughly $21k")

    def test_day_rate_annualised(self):
        good = self.check(
            self.job(employment_type="Contract", day_rate_max=500, salary_currency="GBP"))
        self.assertTrue(good.passed, "£500/day is well above the floor")
        poor = self.check(
            self.job(employment_type="Contract", day_rate_max=100, salary_currency="USD"))
        self.assertFalse(poor.passed, "$100/day annualises to ~$22k")

    def test_unpublished_pay_kept_but_flagged(self):
        verdict = filters.check_pay_floor(self.job())
        self.assertTrue(verdict.passed)
        self.assertTrue(any("not published" in c for c in verdict.concerns))

    def test_require_salary_mode(self):
        verdict = filters.check_pay_floor(self.job(), require_salary=True)
        self.assertFalse(verdict.passed)


class LowRateMarketTests(unittest.TestCase):

    def test_location_scoped_to_low_rate_market_rejected(self):
        for location in ("Remote - India", "Remote (Pakistan)", "Bangladesh - Remote"):
            with self.subTest(location=location):
                verdict = filters.check_market(posting(location=location,
                                                       description="Fully remote Flutter role."))
                self.assertFalse(verdict.passed, location)
                self.assertEqual(verdict.category, "low_rate_market")

    def test_body_restriction_rejected(self):
        verdict = filters.check_market(posting(
            location="Remote",
            description="Fully remote. We are only hiring candidates from India."))
        self.assertFalse(verdict.passed)

    def test_worldwide_role_is_not_rejected(self):
        verdict = filters.check_market(posting(
            location="Remote - Worldwide",
            description="Work from anywhere. Our team spans India, the UK and Brazil."))
        self.assertTrue(verdict.passed,
                        "a worldwide role must not be rejected for having staff there")

    def test_uk_role_unaffected(self):
        self.assertTrue(filters.check_market(posting(
            location="Remote, United Kingdom",
            description="Fully remote UK role.")).passed)


class FreshnessTests(unittest.TestCase):

    def setUp(self):
        self.days = config.FRESHNESS_DAYS
        self.cutoff, *_ = freshness_window(self.days)

    def test_today_accepted(self):
        self.assertTrue(filters.check_freshness(posting(days_ago=0), self.cutoff, self.days).passed)

    def test_inside_window_accepted(self):
        inside = max(0, config.FRESHNESS_DAYS - 2)
        self.assertTrue(filters.check_freshness(posting(days_ago=inside), self.cutoff, self.days).passed)

    def test_outside_window_rejected(self):
        verdict = filters.check_freshness(posting(days_ago=config.FRESHNESS_DAYS + 1),
                                          self.cutoff, self.days)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "stale")

    def test_the_reason_names_the_window_this_run_asked_for(self):
        """A run told to read 7 days must not explain itself in terms of 30."""
        cutoff, *_ = freshness_window(7)
        verdict = filters.check_freshness(posting(days_ago=9), cutoff, 7)
        self.assertFalse(verdict.passed)
        self.assertIn("7-day window", verdict.reason)

    def test_missing_date_is_kept_and_flagged(self):
        job = posting()
        job.posted_at = None
        verdict = filters.check_freshness(job, self.cutoff, self.days)
        self.assertTrue(verdict.passed)
        self.assertTrue(any("date not published" in c.lower() for c in verdict.concerns))

    def test_missing_date_rejected_when_the_policy_is_turned_off(self):
        job = posting()
        job.posted_at = None
        original = config.KEEP_UNDATED
        config.KEEP_UNDATED = False
        try:
            verdict = filters.check_freshness(job, self.cutoff, self.days)
        finally:
            config.KEEP_UNDATED = original
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "stale")


class EnrichmentLinkTests(unittest.TestCase):
    """The Website and Careers Page columns must never point at a job board."""

    def test_redirect_tracker_is_not_the_employers_website(self):
        from job_agent import enrich
        job = posting()
        job.url = job.apply_url = "https://jobviewtrack.com/v2/qgAmHIZdGtHQh9Aw"
        self.assertEqual(enrich.company_website(job), "")
        self.assertEqual(enrich.careers_page(job, "")[0], "")

    def test_host_prefix_stripping_does_not_eat_real_characters(self):
        from job_agent import enrich
        self.assertEqual(enrich._host("https://workday.com/x"), "workday.com")
        self.assertEqual(enrich._host("https://www.workday.com/x"), "workday.com")
        self.assertEqual(enrich._host("https://wise.com/x"), "wise.com")

    def test_a_real_employer_site_is_still_used(self):
        from job_agent import enrich
        job = posting()
        job.url = job.apply_url = "https://kestrelpayments.com/careers/flutter-engineer"
        self.assertEqual(enrich.company_website(job), "https://kestrelpayments.com")
        url, guessed = enrich.careers_page(job, "https://kestrelpayments.com")
        self.assertEqual(url, "https://kestrelpayments.com/careers")
        self.assertFalse(guessed)


class ProspectFloorTests(unittest.TestCase):
    """An unverifiable region gets a lower bar than other prospects, not none."""

    UNVERIFIED = "Remote status not stated in the truncated description"

    def prospect(self, title, score, reason=None):
        from job_agent.models import Job
        return Job(title=title, company="Acme", match_score=score,
                   rejection_reason=reason if reason is not None else self.UNVERIFIED)

    def test_unverified_floor_is_below_the_ordinary_one(self):
        self.assertLess(config.PROSPECT_UNVERIFIED_MIN_SCORE, config.PROSPECT_MIN_SCORE)

    def test_generic_mobile_post_with_a_poor_score_is_dropped(self):
        from job_agent.pipeline import keep_prospect
        self.assertFalse(keep_prospect(self.prospect("Mobile Developer - Advisor I", 7)))
        self.assertFalse(keep_prospect(self.prospect("Software Engineer II (Mobile Engineer)", 8)))

    def test_flutter_title_survives_any_score(self):
        from job_agent.pipeline import keep_prospect
        self.assertTrue(keep_prospect(self.prospect("Flutter Developer", 4)))
        self.assertTrue(keep_prospect(self.prospect("Senior Dart Engineer", 4)))

    def test_strong_generic_mobile_post_still_surfaces(self):
        from job_agent.pipeline import keep_prospect
        self.assertTrue(keep_prospect(self.prospect("Senior Mobile Developer", 48)))

    def test_a_barred_region_uses_the_ordinary_higher_bar(self):
        from job_agent.pipeline import keep_prospect
        reason = 'US-only role — matched "must be based in the US"'
        self.assertFalse(keep_prospect(self.prospect("Flutter Developer", 40, reason)))
        self.assertTrue(keep_prospect(self.prospect("Flutter Developer", 60, reason)))

    def test_europe_without_uk_counts_as_undetermined(self):
        from job_agent.pipeline import keep_prospect
        reason = 'Europe/EMEA role ("europe") but UK eligibility is not confirmed'
        self.assertTrue(keep_prospect(self.prospect("Flutter Developer", 40, reason)))


class EmployerSizeTests(unittest.TestCase):
    """Size is a ranking signal. It only rejects when small firms were asked for."""

    def tearDown(self):
        profile.set_active(flutter_uk_profile())

    def small_only(self):
        from dataclasses import replace
        profile.set_active(replace(profile.active(), small_employers_only=True))

    def test_a_large_employer_is_kept_by_default(self):
        self.assertTrue(filters.check_employer_size(posting(company="Thomson Reuters")).passed)
        self.assertTrue(filters.check_employer_size(posting(company="Someone Big"), "1000+").passed)

    def test_a_large_employer_is_rejected_when_small_firms_were_asked_for(self):
        self.small_only()
        verdict = filters.check_employer_size(posting(company="Thomson Reuters"))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "large_employer")

    def test_a_large_size_label_is_rejected_when_small_firms_were_asked_for(self):
        self.small_only()
        self.assertFalse(filters.check_employer_size(posting(company="Someone Big"), "1000+").passed)

    def test_size_is_still_detected_even_though_it_no_longer_rejects(self):
        self.assertTrue(filters.employer_is_large(posting(company="Thomson Reuters")))
        self.assertTrue(filters.employer_is_large(posting(company="Someone Big"), "1000+"))
        self.assertFalse(filters.employer_is_large(posting(company="Hypervolt"), "11-50"))

    def test_startup_kept(self):
        self.assertTrue(filters.check_employer_size(posting(company="Hypervolt"), "11-50").passed)

    def test_unknown_size_is_kept(self):
        self.assertTrue(filters.check_employer_size(posting(company="Quiet Startup Ltd")).passed)

    def test_substring_does_not_falsely_match(self):
        self.small_only()
        self.assertTrue(filters.check_employer_size(posting(company="Metadata Systems")).passed)
        self.assertTrue(filters.check_employer_size(posting(company="Wiseman Labs")).passed)


class ApplicantCountTests(unittest.TestCase):

    def test_parses_linkedin_captions(self):
        cases = {
            "Be among the first 25 applicants": 10,
            "Over 200 applicants": 250,
            "48 applicants": 48,
            "1,024 applicants": 1024,
            "": None,
        }
        for caption, expected in cases.items():
            with self.subTest(caption=caption):
                self.assertEqual(scoring.applicant_count(caption), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class DescriptionCacheTests(unittest.TestCase):
    """The cache is what lets a daily run read every description without re-fetching the
    whole market every morning.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._real_cache_dir = config.CACHE_DIR
        config.CACHE_DIR = Path(self._tmp.name)

    def tearDown(self):
        config.CACHE_DIR = self._real_cache_dir
        self._tmp.cleanup()

    def _age(self, key: str, days: float) -> None:
        import sqlite3
        from contextlib import closing
        path = config.CACHE_DIR / "fetch_cache.sqlite3"
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute("UPDATE fetch_cache SET fetched_at = ? WHERE key = ?",
                         (time.time() - days * 86400, key))

    def test_roundtrip(self):
        cache.put("k", {"description": "body", "tags": ["Senior"]})
        self.assertEqual(cache.get("k", 21)["description"], "body")

    def test_expires_past_ttl(self):
        cache.put("k", {"description": "body"})
        self._age("k", 5)
        self.assertIsNotNone(cache.get("k", 21), "5 days old should survive a 21-day TTL")
        self.assertIsNone(cache.get("k", 2), "5 days old must not survive a 2-day TTL")

    def test_applicant_counts_expire_before_descriptions(self):
        self.assertLess(config.LINKEDIN_APPLICANTS_CACHE_DAYS,
                        config.LINKEDIN_CACHE_DAYS)

    def test_missing_key_is_a_miss(self):
        self.assertIsNone(cache.get("never-written", 21))

    def test_prune_removes_old_entries(self):
        cache.put("old", {"description": "x"})
        self._age("old", 40)
        self.assertEqual(cache.prune(30), 1)
        self.assertIsNone(cache.get("old", 90))


class MetroLocationTests(unittest.TestCase):
    """Boards report "Greater Mumbai Area" with no country in the string. Reading those as
    "location unstated" sent concretely-foreign roles to Prospects.
    """

    def test_metro_areas_resolve_to_a_country(self):
        cases = {
            "Greater Mumbai Area": "india",
            "Mumbai Metropolitan Region": "india",
            "Greater Bengaluru Area": "india",
            "Lisbon Metropolitan Area": "portugal",
            "Greater Tokyo Area": "japan",
            "Atlanta Metropolitan Area": "united states",
            "Zamosc Metropolitan Area": "poland",
            "Singapore, Singapore": "singapore",
            "New York, NY": "united states",
        }
        for location, country in cases.items():
            with self.subTest(location=location):
                self.assertEqual(geography.location_country(location), country)

    def test_uk_locations_never_resolve_to_a_foreign_country(self):
        for location in ("Greater London Area", "London, England, United Kingdom",
                         "United Kingdom", "Manchester Metropolitan Area",
                         "Edinburgh, Scotland", "Remote (UK)"):
            with self.subTest(location=location):
                self.assertEqual(geography.location_country(location), "")

    def test_unstated_locations_stay_unresolved(self):
        for location in ("", "Remote", "Worldwide", "Home based - Worldwide", "Anywhere"):
            with self.subTest(location=location):
                self.assertEqual(geography.location_country(location), "")

    def test_foreign_metro_is_rejected_not_offered_as_a_prospect(self):
        verdict = remote.assess_remote(posting(
            location="Greater Tokyo Area",
            description="Fully remote Flutter role. Great team, modern stack."))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "ineligible")
        self.assertFalse(verdict.prospect_worthy,
                         "a job advertised for Tokyo is not a UK near-miss")

    def test_genuinely_unstated_location_is_still_a_prospect(self):
        verdict = remote.assess_remote(posting(
            location="Remote",
            description="Fully remote Flutter role. Competitive salary."))
        self.assertFalse(verdict.passed)
        self.assertTrue(verdict.prospect_worthy,
                        "an unstated region is worth asking about")

    def test_worldwide_still_beats_a_foreign_office_location(self):
        verdict = remote.assess_remote(posting(
            location="Berlin, Germany",
            description="Fully remote. We hire from anywhere in the world."))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_low_rate_metro_is_rejected_as_low_rate_market(self):
        verdict = filters.check_market(posting(location="Greater Bengaluru Area"))
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "low_rate_market")


class HeadcountTests(unittest.TestCase):
    """Name-matching only knows the household names someone thought to list; the company
    page states the band outright.
    """

    def test_parses_size_bands(self):
        from job_agent.filters import _headcount_ceiling
        cases = {
            "1,001-5,000 employees": 5000,
            "11-50 employees": 50,
            "10,001+ employees": 20002,
            "2-10 employees": 10,
            "": None,
            "no numbers here": None,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(_headcount_ceiling(text), expected)

    def test_non_linkedin_url_is_ignored(self):
        self.assertIsNone(filters.linkedin_headcount("https://example.com/about"))
        self.assertIsNone(filters.linkedin_headcount(""))


class StatedPayFloorTests(unittest.TestCase):
    """A minimum the person named behaves differently from one we estimated."""

    def tearDown(self):
        profile.set_active(flutter_uk_profile())

    def _job(self, **kwargs):
        return Job(title="Senior Flutter Engineer", company="Acme", **kwargs)

    def _profile(self, *, stated: bool, floor: float = 50_000):
        from dataclasses import replace
        profile.set_active(replace(profile.active(), key="compiled:test",
                                   salary_floor_usd=floor, pay_floor_stated=stated))

    def test_an_unpublished_salary_becomes_a_prospect_when_a_floor_was_stated(self):
        self._profile(stated=True)
        verdict = filters.check_pay_floor(self._job(), floor_usd=50_000)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "pay_unstated")
        self.assertIn("cannot be shown to meet", verdict.reason)

    def test_an_unpublished_salary_is_kept_when_the_floor_was_only_estimated(self):
        self._profile(stated=False)
        verdict = filters.check_pay_floor(self._job(), floor_usd=50_000)
        self.assertTrue(verdict.passed)
        self.assertTrue(any("not published" in c for c in verdict.concerns))

    def test_a_published_salary_below_a_stated_floor_is_still_a_rejection(self):
        self._profile(stated=True)
        verdict = filters.check_pay_floor(
            self._job(salary_min=20_000, salary_max=25_000, salary_currency="USD"),
            floor_usd=50_000)
        self.assertFalse(verdict.passed)
        self.assertEqual(verdict.category, "low_pay")

    def test_a_published_salary_above_the_floor_passes(self):
        self._profile(stated=True)
        verdict = filters.check_pay_floor(
            self._job(salary_min=90_000, salary_max=110_000, salary_currency="USD"),
            floor_usd=50_000)
        self.assertTrue(verdict.passed)

    def test_no_floor_at_all_means_no_pay_filtering(self):
        self._profile(stated=True, floor=0)
        self.assertTrue(filters.check_pay_floor(self._job(), floor_usd=0).passed)
