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

    def searching_from(self, country: str, arrangement: str = "any",
                       onsite_cities: tuple[str, ...] = ()) -> region.Region:
        built = region.build(country)
        profile.set_active(SearchProfile(
            key="test", label="test", query="full stack",
            work_arrangement=arrangement,
            home_country=built.country, home_terms=built.terms,
            home_city_terms=built.cities, onsite_cities=onsite_cities))
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


class OnsiteWorkIsScopedToNamedCitiesTests(RegionScopedTestCase):
    """"Jobs in Lahore" means Lahore, not the next city over.

    On-site scoping is opt-in: a search that names the cities its office work
    must be in rejects office roles in the same country's other cities, while
    leaving remote work open to anywhere. A request that names only a country
    sets no cities and keeps the old country-level behaviour.
    """

    #: Countries whose city list is long enough to name one and test another.
    CITY_COUNTRIES = ("Pakistan", "United Kingdom", "United States", "Germany",
                      "India", "Australia", "Canada")

    def test_onsite_in_the_named_city_qualifies(self):
        for country in self.CITY_COUNTRIES:
            built = region.build(country)
            wanted, elsewhere = built.cities[0], built.cities[1]
            self.searching_from(country, onsite_cities=(wanted,))
            location = f"{wanted.title()}, {built.country}"
            with self.subTest(country=country, city=wanted):
                verdict = pipeline.judge_arrangement(
                    advert(location, ADVERTS["onsite"]), "any")
                self.assertTrue(verdict.passed, verdict.reason)
            self.assertTrue(elsewhere)  # the next test is meaningless without it

    def test_onsite_in_another_home_city_is_rejected(self):
        for country in self.CITY_COUNTRIES:
            built = region.build(country)
            wanted, elsewhere = built.cities[0], built.cities[1]
            self.searching_from(country, onsite_cities=(wanted,))
            location = f"{elsewhere.title()}, {built.country}"
            for kind in ("onsite", "hybrid"):
                with self.subTest(country=country, city=elsewhere, advert=kind):
                    verdict = pipeline.judge_arrangement(
                        advert(location, ADVERTS[kind]), "any")
                    self.assertFalse(verdict.passed, verdict.remote_status)
                    self.assertEqual(verdict.category, "ineligible")
                    self.assertIn(wanted.title(), verdict.reason)

    def test_remote_work_is_never_scoped_by_onsite_cities(self):
        self.searching_from("Pakistan", onsite_cities=("lahore",))
        verdict = pipeline.judge_arrangement(
            advert("Remote", "Fully remote role. Work from anywhere in the world."),
            "any")
        self.assertTrue(verdict.passed, verdict.reason)
        self.assertIn("Worldwide", verdict.remote_status)

    def test_an_onsite_role_naming_no_city_is_kept_as_a_prospect(self):
        built = self.searching_from("Pakistan", onsite_cities=("lahore",))
        verdict = pipeline.judge_arrangement(
            advert(built.country, ADVERTS["onsite"]), "any")
        self.assertFalse(verdict.passed, verdict.remote_status)
        self.assertTrue(verdict.prospect_worthy)

    def test_the_named_city_of_a_multiword_name_is_read(self):
        self.searching_from("United States", onsite_cities=("san francisco",))
        verdict = pipeline.judge_arrangement(
            advert("San Francisco, CA", ADVERTS["onsite"]), "any")
        self.assertTrue(verdict.passed, verdict.reason)

    def test_without_named_cities_onsite_stays_country_level(self):
        # The regression guard: an empty onsite_cities must not start rejecting
        # the country's other cities.
        for country in self.CITY_COUNTRIES:
            built = self.searching_from(country)
            location = f"{built.cities[1].title()}, {built.country}"
            with self.subTest(country=country, city=built.cities[1]):
                verdict = pipeline.judge_arrangement(
                    advert(location, ADVERTS["onsite"]), "any")
                self.assertTrue(verdict.passed, verdict.reason)

    def test_every_city_in_the_table_follows_the_four_rules(self):
        """Sweep all ~130 cities: scoped city passes, others reject, remote free.

        Broad on purpose. A fix tuned to one city's spelling — a multiword name,
        a name that is also its country, one whose ISO code collides with a US
        state — shows up as a failure on another country here.
        """
        countries = list(region.COUNTRIES)
        for index, country in enumerate(countries):
            cities = region.COUNTRIES[country][1]
            foreign = region.COUNTRIES[countries[(index + 1) % len(countries)]][1][0]
            for city in cities:
                built = self.searching_from(country, onsite_cities=(city,))
                here = f"{city.title()}, {built.country}"
                with self.subTest(country=country, city=city):
                    # 1. On-site in the scoped city qualifies.
                    self.assertTrue(pipeline.judge_arrangement(
                        advert(here, ADVERTS["onsite"]), "any").passed, here)
                    # 2. Remote is never scoped by a city.
                    self.assertTrue(pipeline.judge_arrangement(
                        advert("Remote", "Fully remote. Work from anywhere in "
                               "the world."), "any").passed)
                    # 3. On-site in another city of the same country is rejected.
                    others = [c for c in cities if c != city]
                    if others:
                        v = pipeline.judge_arrangement(
                            advert(f"{others[0].title()}, {built.country}",
                                   ADVERTS["onsite"]), "any")
                        self.assertFalse(v.passed, f"{others[0]} vs {city}")
                    # 4. On-site abroad is rejected.
                    self.assertFalse(pipeline.judge_arrangement(
                        advert(f"{foreign.title()}, "
                               f"{countries[(index + 1) % len(countries)]}",
                               ADVERTS["onsite"]), "any").passed)

    def test_the_awkward_location_spellings(self):
        """Bare cities, ISO codes, metro areas and hybrids all read the same."""
        self.searching_from("Pakistan", onsite_cities=("lahore",))
        passes = ("Lahore", "Lahore, PK", "Greater Lahore Area", "Lahore, Punjab")
        rejects = ("Karachi", "Karachi, PK", "Greater Karachi Area", "Islamabad")
        for location in passes:
            with self.subTest(passes=location):
                self.assertTrue(pipeline.judge_arrangement(
                    advert(location, ADVERTS["onsite"]), "any").passed, location)
        for location in rejects:
            for kind in ("onsite", "hybrid"):
                with self.subTest(rejects=location, advert=kind):
                    self.assertFalse(pipeline.judge_arrangement(
                        advert(location, ADVERTS[kind]), "any").passed, location)

    def test_remote_city_phrasing_is_not_treated_as_on_site(self):
        # "Remote - Karachi" is a remote role, and remote work is never scoped
        # to a city — so it survives a Lahore-only on-site scope.
        self.searching_from("Pakistan", onsite_cities=("lahore",))
        verdict = pipeline.judge_arrangement(
            advert("Remote - Karachi",
                   "Fully remote role. Work from anywhere."), "any")
        self.assertTrue(verdict.passed, verdict.reason)


class WorldwideBoilerplateNeverOverridesAForeignLocationTests(RegionScopedTestCase):
    """"Global" in the body describes the employer, not the job's location.

    The leak that sent an Operations search scoped to Pakistan back with
    cleanroom and warehouse roles in Poland, New Zealand and Michigan: each
    advert named a foreign city as its location, mentioned "remote" once in the
    body, and called the company a "global leader" — enough to be stamped
    "Remote — Worldwide" and qualified, its location never looked at.
    """

    GLOBAL_BODY = ("A global leader operating globally. We work remotely with "
                   "colleagues around the world across our global business.")

    def test_a_foreign_office_role_is_not_made_worldwide_by_boilerplate(self):
        for index, country in enumerate(COUNTRIES):
            elsewhere = region.build(COUNTRIES[(index + 1) % len(COUNTRIES)])
            self.searching_from(country)
            location = f"{elsewhere.cities[0].title()}, {elsewhere.country}"
            with self.subTest(home=country, job=location):
                verdict = remote.assess_remote(advert(location, self.GLOBAL_BODY))
                self.assertFalse(verdict.passed, verdict.remote_status)
                self.assertNotIn("Worldwide", verdict.remote_status)

    def test_a_genuinely_worldwide_location_field_still_qualifies(self):
        for location in ("Anywhere in the World", "Remote — Worldwide",
                         "Worldwide"):
            self.searching_from("Pakistan")
            with self.subTest(location=location):
                verdict = remote.assess_remote(
                    advert(location, "Fully remote role, work from anywhere."))
                self.assertTrue(verdict.passed, verdict.reason)
                self.assertIn("Worldwide", verdict.remote_status)

    def test_a_home_country_office_role_is_untouched(self):
        built = self.searching_from("Pakistan")
        verdict = pipeline.judge_arrangement(
            advert(self.home_location(built), self.GLOBAL_BODY), "any")
        self.assertTrue(verdict.passed, verdict.reason)


class OnsiteCitiesAreReadFromTheCompiledPayloadTests(unittest.TestCase):
    """The sanitiser carries `onsite_cities` from the model's answer, lowercased."""

    def tearDown(self):
        profile.reset()

    def test_named_cities_populate_onsite_cities(self):
        compiled = profile._from_payload(
            {"onsite_cities": ["Lahore", "lahore"], "home_country": "Pakistan"},
            "operations jobs on-site in Lahore or remote anywhere", False)
        self.assertEqual(compiled.onsite_cities, ("lahore",))

    def test_a_country_only_request_names_no_cities(self):
        compiled = profile._from_payload(
            {"home_country": "Pakistan"}, "operations jobs in Pakistan", False)
        self.assertEqual(compiled.onsite_cities, ())


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
