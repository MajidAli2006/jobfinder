"""Tests for working out where someone works from."""

from __future__ import annotations

import unittest
from unittest import mock

from job_agent import geography, profile, region, remote
from job_agent.models import RawJob


def raw(title: str, description: str = "", location: str = "Remote") -> RawJob:
    return RawJob(source="test", source_id="1", title=title, company="Acme",
                  url="https://example.com/job", description=description,
                  location_raw=location)


class CountryResolutionTests(unittest.TestCase):

    def test_common_spellings_resolve_to_one_country(self):
        for text in ("UK", "uk", "United Kingdom", "great britain", "GB", "england"):
            self.assertEqual(region.build(text).country, "United Kingdom", text)
        for text in ("USA", "us", "U.S.", "united states", "America"):
            self.assertEqual(region.build(text).country, "United States", text)

    def test_variants_and_cities_come_from_the_table(self):
        germany = region.build("Germany")
        self.assertIn("deutschland", germany.terms)
        self.assertIn("berlin", germany.cities)

    def test_an_unknown_country_is_usable_rather_than_an_error(self):
        liech = region.build("Liechtenstein")
        self.assertEqual(liech.country, "Liechtenstein")
        self.assertEqual(liech.terms, ("liechtenstein",))

    def test_empty_input_is_falsey_not_a_fake_country(self):
        self.assertFalse(region.build(""))
        self.assertFalse(region.build("   "))

    def test_a_list_is_parsed_in_the_order_given(self):
        parsed = region.parse_list("USA, UK and Australia")
        self.assertEqual([r.country for r in parsed],
                         ["United States", "United Kingdom", "Australia"])

    def test_separators_and_duplicates_are_handled(self):
        parsed = region.parse_list("uk / united kingdom & Germany, DE")
        self.assertEqual([r.country for r in parsed], ["United Kingdom", "Germany"])


class SystemDetectionTests(unittest.TestCase):
    """The timezone answers "where"; the locale answers "in which language"."""

    def test_the_timezone_decides(self):
        with mock.patch.object(region, "_system_timezone", return_value="Europe/Berlin"):
            detected = region.detect()
        self.assertEqual(detected.country, "Germany")
        self.assertIn("timezone", detected.source)

    def test_a_language_preference_never_overrides_the_timezone(self):
        with mock.patch.object(region, "_system_timezone", return_value="Europe/London"), \
             mock.patch.object(region, "_locale_candidates",
                               return_value=[("en_PK", "locale")]):
            detected = region.detect()
        self.assertEqual(detected.country, "United Kingdom")

    def test_the_locale_is_used_only_when_the_timezone_says_nothing(self):
        with mock.patch.object(region, "_system_timezone", return_value=""), \
             mock.patch.object(region, "_locale_candidates",
                               return_value=[("en_CA.UTF-8", "LANG setting")]):
            detected = region.detect()
        self.assertEqual(detected.country, "Canada")
        self.assertIn("LANG", detected.source)

    def test_an_undetectable_system_returns_nothing_rather_than_guessing(self):
        with mock.patch.object(region, "_system_timezone", return_value="Antarctica/Troll"), \
             mock.patch.object(region, "_locale_candidates", return_value=[]):
            self.assertFalse(region.detect())


class StrictCountryListTests(unittest.TestCase):
    """"Jobs in the USA, UK and Australia" means those countries and no others."""

    def setUp(self):
        payload = {
            "label": "Android", "is_job_search": True, "answer": "",
            "core_terms": ["android", "kotlin"],
            "secondary_terms": ["android developer"],
            "hard_title_exclusions": ["recruiter"],
            "other_discipline_terms": ["ios", "flutter"],
            "competing_stacks": [], "skills": [], "domain_keywords": [],
            "search_queries": ["android developer"], "min_body_core_mentions": 3,
            "candidate_brief": "An Android engineer.", "seniority": "Senior",
            "years_experience": 5, "needs_clarification": False, "questions": [],
            "target_regions": ["United States", "United Kingdom", "Australia"],
            "home_country": "United Kingdom", "home_terms": [],
            "home_city_terms": [], "region_terms": [],
            "region_excluding_home_terms": [], "timezone": "",
            "salary_floor_usd": 0,
        }
        profile.set_active(profile._from_payload(payload, "android in usa uk au", False))

    def tearDown(self):
        profile.reset()

    def test_every_named_country_is_matchable(self):
        terms = profile.active().home_terms
        for expected in ("usa", "united states", "uk", "england", "australia"):
            self.assertIn(expected, terms)

    def test_a_country_outside_the_list_is_treated_as_foreign(self):
        foreign = geography.foreign_country_terms()
        self.assertIn("germany", foreign)
        self.assertIn("india", foreign)

    def test_none_of_the_named_countries_is_treated_as_foreign(self):
        foreign = geography.foreign_country_terms()
        for named in ("united states", "usa", "united kingdom", "australia"):
            self.assertNotIn(named, foreign)

    def test_a_role_in_a_named_country_is_accepted(self):
        verdict = remote.assess_remote(raw(
            "Senior Android Engineer",
            "Fully remote role, open to candidates based in the United Kingdom. "
            "Kotlin and Android experience required.",
            location="Remote, United Kingdom",
        ))
        self.assertTrue(verdict.passed, verdict.reason)

    def test_a_role_restricted_to_an_unnamed_country_is_not_accepted(self):
        verdict = remote.assess_remote(raw(
            "Senior Android Engineer",
            "Fully remote. Candidates must be located in Germany. Kotlin and "
            "Android experience required.",
            location="Remote, Germany",
        ))
        self.assertFalse(verdict.passed)

    def test_a_worldwide_role_is_still_accepted(self):
        verdict = remote.assess_remote(raw(
            "Senior Android Engineer",
            "Fully remote, work from anywhere in the world. We hire "
            "internationally through Deel. Kotlin and Android required.",
            location="Remote worldwide",
        ))
        self.assertTrue(verdict.passed, verdict.reason)


if __name__ == "__main__":
    unittest.main()


class LinkedInGeoSelectionTests(unittest.TestCase):
    """LinkedIn is searched per region, and regions dominate a run's wall time."""

    def setUp(self):
        from job_agent.sources.linkedin import LinkedIn
        self.LinkedIn = LinkedIn
        self.base = profile.active()

    def tearDown(self):
        profile.reset()

    def _geos(self, **overrides):
        from dataclasses import replace
        profile.set_active(replace(self.base, key="compiled:test", **overrides))
        return [name for name, _ in self.LinkedIn().geos()]

    def test_named_countries_narrow_the_search(self):
        names = self._geos(target_regions=("United Kingdom", "Germany"),
                           home_country="United Kingdom")
        self.assertIn("United Kingdom", names)
        self.assertIn("Germany", names)
        self.assertNotIn("Qatar", names)
        self.assertNotIn("New Zealand", names)

    def test_worldwide_is_always_searched(self):
        self.assertIn("Worldwide", self._geos(target_regions=("United States",),
                                              home_country="United States"))

    def test_a_european_country_also_searches_europe(self):
        self.assertIn("Europe", self._geos(target_regions=("Germany",),
                                           home_country="Germany"))

    def test_a_non_european_country_does_not(self):
        self.assertNotIn("Europe", self._geos(target_regions=("Australia",),
                                              home_country="Australia"))

    def test_an_unknown_region_keeps_the_full_net(self):
        names = self._geos(target_regions=(), home_country="", home_terms=())
        self.assertEqual(len(names), len(self.LinkedIn.GEOS))

    def test_a_country_linkedin_cannot_target_keeps_the_full_net(self):
        names = self._geos(target_regions=("Liechtenstein",),
                           home_country="Liechtenstein")
        self.assertEqual(len(names), len(self.LinkedIn.GEOS))
