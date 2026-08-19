"""Tests for place resolution.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from dataclasses import replace

from job_agent import geography, profile, region

from .fixtures import flutter_uk_profile


class HomeVocabularyTests(unittest.TestCase):
    def tearDown(self):
        profile.reset()

    def based_in(self, country, terms=(), cities=()):
        profile.set_active(replace(profile.active(), key="compiled:test",
                                   home_country=country, home_terms=terms,
                                   home_city_terms=cities))

    def test_the_home_country_is_named_for_the_reader(self):
        self.based_in("Nigeria")
        self.assertEqual(geography.home_label(), "Nigeria")

    def test_an_unknown_country_still_reads_sensibly(self):
        self.based_in("")
        self.assertEqual(geography.home_label(), "your location")

    def test_home_known_reflects_whether_a_country_was_established(self):
        self.based_in("Pakistan", terms=("pakistan",))
        self.assertTrue(geography.home_known())
        self.based_in("")
        self.assertFalse(geography.home_known())


class ForeignCountryTests(unittest.TestCase):
    """A country is only "foreign" relative to where the candidate is."""

    def tearDown(self):
        profile.reset()

    def test_the_home_country_is_never_treated_as_foreign(self):
        profile.set_active(replace(flutter_uk_profile(), home_country="United Kingdom"))
        foreign = [c.lower() for c in geography.foreign_country_terms()]
        self.assertNotIn("united kingdom", foreign)

    def test_an_unknown_home_rules_nothing_out(self):
        profile.set_active(replace(flutter_uk_profile(), home_terms=()))
        self.assertEqual(geography.foreign_country_terms(), ())


class LocationCountryTests(unittest.TestCase):
    """Adverts name places in many ways; the country is what matters."""

    def test_a_named_country_is_recognised(self):
        self.assertEqual(geography.location_country("Berlin, Germany"), "germany")

    def test_a_metro_area_resolves_to_its_country(self):
        # "Greater Bengaluru Area" names no country, so the metro table is what
        # lets the market filter see it at all.
        self.assertEqual(geography.location_country("Greater Bengaluru Area"), "india")

    def test_a_gulf_location_resolves(self):
        self.assertEqual(geography.location_country("Dubai, United Arab Emirates"),
                         "united arab emirates")

    def test_an_unrecognised_place_returns_nothing(self):
        self.assertEqual(geography.location_country("Somewhere Nice"), "")

    def test_an_empty_location_returns_nothing(self):
        self.assertEqual(geography.location_country(""), "")


class WorldwideTests(unittest.TestCase):
    def test_the_common_phrasings_are_recognised(self):
        for phrase in ("work from anywhere", "remote worldwide", "anywhere in the world"):
            with self.subTest(phrase=phrase):
                self.assertTrue(geography.worldwide(phrase))

    def test_an_ordinary_advert_is_not_worldwide(self):
        self.assertFalse(geography.worldwide("remote within the united kingdom"))


class EligibilityPhrasingTests(unittest.TestCase):
    """The phrasings that confirm eligibility are built from the active profile.

    They were fixed to the United Kingdom, so an advert saying "Nigeria-based
    candidates welcome" gave a candidate in Lagos no confirmation at all, and
    a country restriction they were exempt from still rejected it.
    """

    def tearDown(self):
        profile.reset()

    def _under(self, country: str):
        built = region.build(country)
        profile.set_active(replace(profile.active(), home_country=built.country,
                                   home_terms=built.terms,
                                   home_city_terms=built.cities))

    def test_every_country_gets_its_own_confirmations(self):
        cases = {
            "Nigeria": "we hire nigeria-based candidates",
            "India": "open to india residents",
            "United Kingdom": "uk-based applicants welcome",
            "Germany": "work from home germany",
            "United States": "us payroll, remote united states",
            "United Arab Emirates": "anywhere in the uae",
        }
        for country, advert in cases.items():
            with self.subTest(country=country):
                self._under(country)
                self.assertTrue(geography.home_strongly_eligible(advert))

    def test_a_code_that_is_an_english_word_never_matches_loose_prose(self):
        # "in" is India's code and "us" the United States'. Built into a loose
        # phrasing they would confirm eligibility on almost any advert.
        prose = ("we are a remote in-house team based in berlin. work with us "
                 "and the wider group; anywhere in europe is fine")
        for country in ("India", "United States", "Italy", "Norway", "Austria"):
            with self.subTest(country=country):
                self._under(country)
                self.assertFalse(geography.home_strongly_eligible(prose))

    def test_an_employers_own_address_is_not_an_eligibility_statement(self):
        self._under("Nigeria")
        confirmations = geography.incidental_mentions()
        self.assertIn("our lagos", confirmations)
        self.assertIn("office", confirmations)


if __name__ == "__main__":
    unittest.main()
