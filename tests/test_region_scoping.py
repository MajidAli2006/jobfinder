"""A search scoped to one country returns that country's work.

These are the cross-country regression tests for the leak that sent a Berlin
search back with roles in Buenos Aires, Singapore and Melbourne. Three separate
faults produced it, and each is cheap to reintroduce, so every one is pinned
here against a spread of countries rather than against Germany alone:

* a two-letter ISO code read as a bare word ("de" is Germany, Delaware, and the
  commonest word in Spanish, Portuguese and French);
* office-based work passed without its location ever being looked at;
* the candidate's own country reported back to them as foreign.

Run with:  python -m unittest tests.test_region_scoping -v
"""

from __future__ import annotations

import unittest

from job_agent import geography, pipeline, profile, region, remote
from job_agent.models import RawJob
from job_agent.profile import SearchProfile
from job_agent.utils import normalize, now_local


#: A spread wide enough that a fix tuned to one country shows up as a failure
#: somewhere else: ISO codes that collide with US states (DE, CA, IN, IL, AT),
#: codes that do not (SG, JP, NO), and countries whose language supplies the
#: false positives (Spain, France, Brazil).
COUNTRIES = (
    "Germany", "Austria", "Australia", "Canada", "Saudi Arabia", "India",
    "Italy", "Norway", "Israel", "United States", "United Kingdom",
    "Netherlands", "Poland", "Brazil", "Singapore", "Japan",
    "United Arab Emirates", "Spain", "France", "Ireland",
)

ADVERTS = {
    "remote": "This is a fully remote role. You can work from home.",
    "hybrid": "Hybrid role — two days a week in the office, three from home.",
    "onsite": "On site, five days a week in our office.",
}


def advert(location: str, description: str) -> RawJob:
    return RawJob(source="test", source_id="1",
                  title="Senior Full Stack Developer", company="Acme",
                  url="https://example.com/job", description=description,
                  location_raw=location, posted_at=now_local())


class RegionScopedTestCase(unittest.TestCase):
    """Shared setup: run the search from a named country."""

    def tearDown(self):
        profile.reset()

    def searching_from(self, country: str, arrangement: str = "any") -> region.Region:
        built = region.build(country)
        profile.set_active(SearchProfile(
            key="test", label="test", query="full stack",
            work_arrangement=arrangement,
            home_country=built.country, home_terms=built.terms,
            home_city_terms=built.cities))
        return built

    def home_location(self, built: region.Region) -> str:
        return f"{built.cities[0].title()}, {built.country}"


class ArrangementIsTakenFromTheRequestTests(RegionScopedTestCase):
    """What the person asked for decides which arrangements qualify."""

    def test_the_three_adverts_classify_as_written(self):
        # The rest of this class is meaningless if the fixtures do not read as
        # the arrangements they are named for.
        for kind, description in ADVERTS.items():
            with self.subTest(kind=kind):
                self.assertEqual(
                    remote.classify_arrangement(normalize(description)), kind)

    def test_a_stated_arrangement_admits_only_that_arrangement(self):
        for country in ("Germany", "Australia", "Canada", "India",
                        "United States", "Japan"):
            for wanted in ("remote", "hybrid", "onsite"):
                built = self.searching_from(country, wanted)
                location = self.home_location(built)
                for kind, description in ADVERTS.items():
                    with self.subTest(country=country, wanted=wanted, advert=kind):
                        verdict = pipeline.judge_arrangement(
                            advert(location, description), wanted)
                        self.assertEqual(verdict.passed, kind == wanted,
                                         verdict.reason or verdict.remote_status)

    def test_an_unstated_arrangement_returns_all_three(self):
        for country in COUNTRIES:
            built = self.searching_from(country, "any")
            location = self.home_location(built)
            for kind, description in ADVERTS.items():
                with self.subTest(country=country, advert=kind):
                    verdict = pipeline.judge_arrangement(
                        advert(location, description), "any")
                    self.assertTrue(verdict.passed, verdict.reason)


class OfficeWorkIsScopedToTheCountryTests(RegionScopedTestCase):
    """Work that requires attendance has to be in the country searched."""

    def test_office_work_abroad_is_rejected_from_every_country(self):
        for index, country in enumerate(COUNTRIES):
            elsewhere = region.build(COUNTRIES[(index + 1) % len(COUNTRIES)])
            self.searching_from(country)
            location = f"{elsewhere.cities[0].title()}, {elsewhere.country}"
            for kind in ("hybrid", "onsite"):
                with self.subTest(home=country, job=location, advert=kind):
                    verdict = pipeline.judge_arrangement(
                        advert(location, ADVERTS[kind]), "any")
                    self.assertFalse(verdict.passed, verdict.remote_status)
                    self.assertEqual(verdict.category, "ineligible")

    def test_office_work_at_home_is_kept_from_every_country(self):
        for country in COUNTRIES:
            built = self.searching_from(country)
            location = self.home_location(built)
            for kind in ("hybrid", "onsite"):
                with self.subTest(home=country, advert=kind):
                    verdict = pipeline.judge_arrangement(
                        advert(location, ADVERTS[kind]), "any")
                    self.assertTrue(verdict.passed, verdict.reason)

    def test_a_location_naming_nowhere_is_left_to_the_other_gates(self):
        self.searching_from("Germany")
        verdict = pipeline.judge_arrangement(
            advert("Somewhere Nice", ADVERTS["onsite"]), "any")
        self.assertTrue(verdict.passed, verdict.reason)


class LocalLanguageProseNeverNamesACountryTests(RegionScopedTestCase):
    """An advert written in Spanish is not a German advert.

    Every one of these adverts contains the home country's ISO code as a bare
    word. Read as a country, each turned a Latin American or European posting
    into a confirmed local one.
    """

    PROSE = {
        "Germany": "buscamos un desarrollador de software y trabajo de forma remota",
        "Austria": "we are at the forefront of payments and hiring at pace",
        "India": "the team works in small squads and ships in short cycles",
        "Italy": "it is a small team and it ships every week",
        "Norway": "there is no on-call rota and no weekend work",
        "Netherlands": "nl is not a word here but the team is small",
        "Spain": "el equipo es pequeno y el proceso es rapido",
        "Poland": "pl teams ship fast; the stack is typescript and go",
        "Brazil": "br is unused; a vaga e para pessoa desenvolvedora senior",
        "France": "fr unused; nous recherchons un developpeur full stack",
    }

    def test_a_bare_code_in_prose_confirms_nothing(self):
        for country, prose in self.PROSE.items():
            with self.subTest(country=country):
                self.searching_from(country)
                self.assertFalse(geography.home_mentioned(prose))

    def test_a_foreign_remote_advert_is_not_claimed_as_local(self):
        # The Brazilian and Argentine postings that started this: Portuguese and
        # Spanish prose, a foreign location, and a "Remote — Germany" label.
        self.searching_from("Germany")
        posting = RawJob(
            source="test", source_id="1",
            title="Engenheiro de Software Fullstack Senior", company="Acme",
            url="https://example.com/job", location_raw="Brazil",
            description="Fully remote role. Vaga remota para pessoa "
                        "desenvolvedora de software. Nosso time de engenharia "
                        "esta localizado no Brasil.",
            posted_at=now_local())
        verdict = remote.assess_remote(posting)
        self.assertFalse(verdict.passed)
        self.assertNotIn("Germany", verdict.remote_status)


class TheHomeCountryIsNeverForeignTests(RegionScopedTestCase):
    """`location_country` names somewhere else, or nothing at all."""

    def test_a_home_location_resolves_to_nothing(self):
        for country in COUNTRIES:
            built = self.searching_from(country)
            for location in (self.home_location(built), built.country,
                             built.cities[0].title()):
                with self.subTest(country=country, location=location):
                    self.assertEqual(geography.location_country(location), "")

    def test_a_home_city_the_profile_never_listed_is_still_not_foreign(self):
        """The city table knows more cities than any one profile lists.

        A German profile names five cities; the table knows Stuttgart too. Read
        back as "based in Germany, not eligible", that rejected exactly the
        local roles the search exists to find.
        """
        cases = {
            "Germany": "Stuttgart, Baden-Wurttemberg",
            "United Kingdom": "Sheffield",
            "United States": "Seattle",
            "Australia": "Perth",
            "India": "Chennai",
            "Poland": "Gdansk",
            "Italy": "Bologna",
            "Spain": "Malaga",
        }
        for country, location in cases.items():
            with self.subTest(country=country, location=location):
                self.searching_from(country)
                self.assertEqual(geography.location_country(location), "")

    def test_a_foreign_location_still_resolves(self):
        for index, country in enumerate(COUNTRIES):
            elsewhere = region.build(COUNTRIES[(index + 1) % len(COUNTRIES)])
            self.searching_from(country)
            location = f"{elsewhere.cities[0].title()}, {elsewhere.country}"
            with self.subTest(home=country, location=location):
                self.assertNotEqual(geography.location_country(location), "")


class CodesSharedWithUsStatesTests(RegionScopedTestCase):
    """Two-letter codes belong to a country and a US state at once."""

    def test_an_unaided_shared_code_settles_on_neither(self):
        for location in ("santa barbara, ca", "indianapolis, in",
                         "wilmington, de", "peoria, il"):
            with self.subTest(location=location):
                self.assertEqual(geography.resolve_location(location), "")

    def test_a_known_city_settles_it(self):
        for location, expected in (("chicago, il", "united states"),
                                   ("berlin, de", "germany"),
                                   ("toronto, ca", "canada"),
                                   ("mumbai, in", "india")):
            with self.subTest(location=location):
                self.assertEqual(geography.resolve_location(location), expected)

    def test_an_unshared_code_needs_no_help(self):
        for location, expected in (("graz, at", "austria"),
                                   ("bergen, no", "norway"),
                                   ("nagoya, jp", "japan"),
                                   ("cebu, ph", "philippines")):
            with self.subTest(location=location):
                self.assertEqual(geography.resolve_location(location), expected)

    def test_an_address_ending_in_a_country_code_is_read_as_one(self):
        self.assertEqual(
            geography.resolve_location("rostock, mecklenburg-vorpommern, de"),
            "germany")


class ArrangementPhrasingTests(RegionScopedTestCase):
    """"remote only" has to mean remote only.

    `_arrangement` matched four exact strings and sent everything else to
    "any". So "remote only", "fully remote", "wfh" and "office based" all
    returned office work to someone who had asked for one arrangement — the one
    way this field must never fail, because it fails open and says nothing.
    """

    def test_the_plain_words_are_read(self):
        for text, expected in (("remote", "remote"), ("hybrid", "hybrid"),
                               ("onsite", "onsite"), ("on-site", "onsite"),
                               ("on site", "onsite"), ("any", "any"),
                               ("all", "any"), ("", "any")):
            with self.subTest(text=text):
                self.assertEqual(profile._arrangement(text), expected)

    def test_only_is_not_a_different_arrangement(self):
        for text, expected in (("remote only", "remote"), ("Remote Only", "remote"),
                               ("remote-only", "remote"), ("hybrid only", "hybrid"),
                               ("onsite only", "onsite"), ("on-site only", "onsite")):
            with self.subTest(text=text):
                self.assertEqual(profile._arrangement(text), expected)

    def test_the_everyday_synonyms_are_read(self):
        for text, expected in (("fully remote", "remote"), ("work from home", "remote"),
                               ("wfh", "remote"), ("telecommute", "remote"),
                               ("fully distributed", "remote"),
                               ("hybrid working", "hybrid"), ("part remote", "hybrid"),
                               ("partially remote", "hybrid"), ("split week", "hybrid"),
                               ("office based", "onsite"), ("in the office", "onsite"),
                               ("in person", "onsite")):
            with self.subTest(text=text):
                self.assertEqual(profile._arrangement(text), expected)

    def test_naming_two_arrangements_asks_for_all_of_them(self):
        for text in ("remote or hybrid", "onsite/hybrid", "hybrid, onsite", "either"):
            with self.subTest(text=text):
                self.assertEqual(profile._arrangement(text), "any")

    def test_an_unreadable_value_asks_for_all_of_them(self):
        for text in ("flexible", "no preference", None, "whatever suits"):
            with self.subTest(text=text):
                self.assertEqual(profile._arrangement(text), "any")

    def test_a_phrasing_filters_the_way_the_plain_word_does(self):
        """End to end: the phrasing decides which adverts survive the gate."""
        cases = {
            "remote only": "remote",
            "fully remote": "remote",
            "wfh": "remote",
            "hybrid only": "hybrid",
            "part remote": "hybrid",
            "onsite only": "onsite",
            "office based": "onsite",
        }
        for country in ("Germany", "Canada", "India"):
            for phrasing, kept in cases.items():
                wanted = profile._arrangement(phrasing)
                built = self.searching_from(country, wanted)
                location = self.home_location(built)
                for kind, description in ADVERTS.items():
                    with self.subTest(country=country, said=phrasing, advert=kind):
                        verdict = pipeline.judge_arrangement(
                            advert(location, description), wanted)
                        self.assertEqual(verdict.passed, kind == kept,
                                         f"{phrasing!r} -> {wanted!r}")

    def test_saying_all_returns_every_arrangement(self):
        for country in ("Germany", "Canada", "India"):
            wanted = profile._arrangement("all")
            built = self.searching_from(country, wanted)
            location = self.home_location(built)
            for kind, description in ADVERTS.items():
                with self.subTest(country=country, advert=kind):
                    verdict = pipeline.judge_arrangement(
                        advert(location, description), wanted)
                    self.assertTrue(verdict.passed, verdict.reason)
