"""Tests for source selection and market routing."""

from __future__ import annotations

import os
from contextlib import contextmanager
import unittest
from dataclasses import replace

from job_agent import profile
from job_agent.sources import build_sources, skipped_for_market
from job_agent.sources.market_boards import ADZUNA_MARKETS, Adzuna, Jooble, Reed


def search_in(*countries: str):
    """Point the active profile at one or more countries."""
    profile.set_active(replace(
        profile.active(), key="compiled:test",
        home_country=countries[0] if countries else "",
        target_regions=tuple(countries) if len(countries) > 1 else ()))


class MarketRoutingTests(unittest.TestCase):
    """A board tied to one country must not answer for another."""

    def tearDown(self):
        profile.reset()

    def test_reed_serves_a_uk_search(self):
        search_in("United Kingdom")
        self.assertTrue(Reed().serves_active_search())

    def test_reed_steps_aside_for_a_nigerian_search(self):
        search_in("Nigeria")
        self.assertFalse(Reed().serves_active_search())

    def test_reed_is_kept_when_the_country_is_unknown(self):
        search_in("")
        self.assertTrue(Reed().serves_active_search())

    def test_a_worldwide_board_serves_anywhere(self):
        search_in("Nigeria")
        self.assertTrue(Jooble().serves_active_search())

    def test_a_multi_country_search_keeps_a_board_serving_any_of_them(self):
        search_in("Nigeria", "United Kingdom")
        self.assertTrue(Reed().serves_active_search())


class AdzunaMarketTests(unittest.TestCase):
    """Adzuna runs one endpoint per country and must be pointed at the right one."""

    def tearDown(self):
        profile.reset()

    def test_the_endpoint_follows_the_search_country(self):
        search_in("Australia")
        self.assertEqual(Adzuna().target_markets(), ("au",))
        self.assertIn("/jobs/au/", Adzuna().endpoint("au"))

    def test_several_countries_produce_several_markets(self):
        search_in("United States", "Germany")
        self.assertEqual(set(Adzuna().target_markets()), {"us", "de"})

    def test_a_country_adzuna_does_not_serve_yields_no_market(self):
        search_in("Nigeria")
        self.assertEqual(Adzuna().target_markets(), ())

    def test_an_unserved_country_fetches_nothing_rather_than_the_uk(self):
        search_in("Nigeria")
        source = Adzuna()
        source.enabled = True
        self.assertEqual(source.fetch(), [])

    def test_every_market_has_a_currency(self):
        from job_agent.sources.market_boards import ADZUNA_CURRENCY
        for code in set(ADZUNA_MARKETS.values()):
            self.assertIn(code, ADZUNA_CURRENCY, code)


class JoobleLocationTests(unittest.TestCase):
    """Jooble asks about where the search is, not where this project was written."""

    def tearDown(self):
        profile.reset()

    def test_the_location_follows_the_search(self):
        search_in("Nigeria")
        self.assertEqual(Jooble().where(), "Nigeria")
        self.assertEqual(Jooble().payload("plumber")["location"], "Nigeria")

    def test_an_unknown_country_sends_no_location_at_all(self):
        search_in("")
        self.assertNotIn("location", Jooble().payload("plumber"))

    def _with_queries(self, *, arrangement: str):
        profile.set_active(replace(profile.active(), key="compiled:test",
                                   search_queries=("plumber", "pipefitter"),
                                   work_arrangement=arrangement))
        return Jooble().search_terms()

    def test_remote_is_not_appended_when_remote_was_not_asked_for(self):
        self.assertFalse(any(q.endswith(" remote")
                             for q in self._with_queries(arrangement="any")))

    def test_remote_is_appended_when_remote_was_asked_for(self):
        self.assertTrue(any(q.endswith(" remote")
                            for q in self._with_queries(arrangement="remote")))


class BuildSourcesTests(unittest.TestCase):
    def tearDown(self):
        profile.reset()

    def test_offline_runs_use_the_fixture_source_only(self):
        self.assertEqual([s.name for s in build_sources(offline=True)], ["fixtures"])

    def test_a_board_out_of_market_is_reported_not_silently_dropped(self):
        search_in("Nigeria")
        with _config_keys(REED_API_KEY="test-key"):
            self.assertIn("Reed.co.uk", [label for label, _ in skipped_for_market()])


if __name__ == "__main__":
    unittest.main()


class FeedParsingTests(unittest.TestCase):
    """A feed with HTML entities must not silently disable a whole board."""

    def test_a_clean_feed_parses(self):
        from job_agent.sources.boards import RssBoard
        xml = b"<rss><channel><item><title>Plumber</title></item></channel></rss>"
        root = RssBoard.parse(xml)
        self.assertIsNotNone(root)
        self.assertEqual(len(list(root.iter("item"))), 1)

    def test_an_undefined_html_entity_still_parses(self):
        # NoDesk emits &nbsp;, which is not valid XML and aborts a strict parse.
        from job_agent.sources.boards import RssBoard
        xml = b"<rss><channel><item><title>Plumber&nbsp;Wanted</title></item></channel></rss>"
        root = RssBoard.parse(xml)
        self.assertIsNotNone(root, "an undefined entity must not lose the whole feed")
        self.assertIn("Plumber", list(root.iter("item"))[0].findtext("title"))

    def test_genuinely_broken_xml_returns_nothing_rather_than_raising(self):
        from job_agent.sources.boards import RssBoard
        self.assertIsNone(RssBoard.parse(b"<rss><channel><item>"))


class NoDeskTests(unittest.TestCase):
    """NoDesk titles read "Role at Employer" with no separate company field."""

    def _fetched(self, titles):
        from unittest.mock import patch
        from job_agent.models import RawJob
        from job_agent.sources.boards import NoDesk, RssBoard
        raws = [RawJob(source="nodesk", source_id=str(i), title=t,
                       company="Undisclosed", url=f"https://example.com/{i}")
                for i, t in enumerate(titles)]
        with patch.object(RssBoard, "fetch", return_value=raws):
            return NoDesk().fetch()

    def test_the_employer_is_split_out_of_the_title(self):
        job = self._fetched(["Senior React Developer at Lemon.io"])[0]
        self.assertEqual(job.title, "Senior React Developer")
        self.assertEqual(job.company, "Lemon.io")

    def test_the_last_at_wins_so_role_names_survive(self):
        job = self._fetched(["Engineer at Scale at Ghost"])[0]
        self.assertEqual(job.title, "Engineer at Scale")
        self.assertEqual(job.company, "Ghost")

    def test_a_title_without_at_is_left_alone(self):
        job = self._fetched(["Warehouse Operative"])[0]
        self.assertEqual(job.title, "Warehouse Operative")
        self.assertEqual(job.company, "Undisclosed")


class PartnerApiTests(unittest.TestCase):
    """Indeed and ZipRecruiter block ordinary requests, so they are key-gated."""

    def tearDown(self):
        profile.reset()

    def test_they_are_disabled_without_a_key(self):
        from job_agent.sources.partner_apis import Indeed, ZipRecruiter
        with _config_keys(INDEED_PUBLISHER_ID="", ZIPRECRUITER_API_KEY=""):
            self.assertFalse(Indeed().enabled)
            self.assertFalse(ZipRecruiter().enabled)

    def test_a_disabled_connector_fetches_nothing_rather_than_failing(self):
        from job_agent.sources.partner_apis import Indeed, ZipRecruiter
        for source in (Indeed(), ZipRecruiter()):
            source.enabled = False
            self.assertEqual(source.fetch(), [])

    def test_the_probe_explains_why_a_key_is_needed(self):
        from job_agent.sources.partner_apis import Indeed, ZipRecruiter
        with _config_keys(INDEED_PUBLISHER_ID="", ZIPRECRUITER_API_KEY=""):
            self._probe_explains(Indeed(), ZipRecruiter())

    def _probe_explains(self, indeed, ziprecruiter):
        for source, host in ((indeed, "developer.indeed.com"),
                             (ziprecruiter, "ziprecruiter.com/partner")):
            ok, message = source.probe()
            self.assertFalse(ok)
            self.assertIn("not set", message)
            self.assertIn(host, message, "the probe should say where to apply")

    def test_they_follow_the_search_country(self):
        from job_agent.sources.partner_apis import Indeed
        search_in("Australia")
        self.assertEqual(Indeed().location(), "Australia")

    def test_ziprecruiter_steps_aside_outside_its_markets(self):
        from job_agent.sources.partner_apis import ZipRecruiter
        search_in("Nigeria")
        self.assertFalse(ZipRecruiter().serves_active_search())
        search_in("United States")
        self.assertTrue(ZipRecruiter().serves_active_search())

    def test_the_key_never_appears_in_the_probe_message(self):
        from job_agent import config
        from job_agent.sources.partner_apis import Indeed, ZipRecruiter
        for source, secret in ((Indeed(), config.INDEED_PUBLISHER_ID),
                               (ZipRecruiter(), config.ZIPRECRUITER_API_KEY)):
            if not secret:
                continue
            self.assertNotIn(secret, source.probe()[1])


@contextmanager
def _config_keys(**values):
    """Pin credential settings for a block, whatever this machine has set.

    These are read as `config` attributes rather than from the environment, so
    the environment cannot be patched to reach them.
    """
    from job_agent import config
    saved = {name: getattr(config, name) for name in values}
    for name, value in values.items():
        setattr(config, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(config, name, value)


@contextmanager
def _without_vendor_keys():
    """Run a block as though no search-API vendor key were configured.

    These tests used to skip on any machine that had one, so they only ever
    ran in CI — the state they describe is the one a new user starts in.
    """
    from unittest.mock import patch
    from job_agent.sources import partner_apis
    with patch.dict(os.environ, {}, clear=False):
        for provider in partner_apis.PROVIDERS:
            os.environ.pop(provider.env, None)
        yield


class GoogleJobsTests(unittest.TestCase):
    """The licensed route to the boards that refuse direct requests."""

    #: The shape SerpApi actually returns, trimmed.
    PAYLOAD = {
        "jobs_results": [
            {"title": "Electrician - Interiors", "company_name": "Exhibition and Interiors",
             "location": "Dubai - United Arab Emirates", "via": "via Indeed",
             "description": "<p>Wiring and maintenance</p>", "job_id": "abc123",
             "detected_extensions": {"posted_at": "2026-08-17", "schedule_type": "Full-time",
                                     "salary": "AED 3,000 a month"},
             "apply_options": [{"title": "Indeed", "link": "https://ae.indeed.com/viewjob?jk=9d99"}]},
            {"title": "No apply link", "company_name": "Nowhere", "job_id": "def456"},
        ]
    }

    def tearDown(self):
        profile.reset()

    def source(self):
        from job_agent.sources import partner_apis
        src = partner_apis.GoogleJobs()
        src.provider = partner_apis.PROVIDERS[0]
        src.enabled = True
        return src

    def searching(self, country: str):
        """A profile with something to search for, in a country."""
        search_in(country)
        profile.set_active(replace(profile.active(), search_queries=("electrician",)))

    def test_it_is_disabled_when_no_vendor_has_a_key(self):
        from job_agent.sources import partner_apis
        with _without_vendor_keys():
            self.assertIsNone(partner_apis.active_provider())
            self.assertFalse(partner_apis.GoogleJobs().enabled)

    def test_any_vendor_with_a_key_activates_it(self):
        from unittest.mock import patch
        from job_agent.sources import partner_apis
        for provider in partner_apis.PROVIDERS:
            with self.subTest(provider=provider.name):
                with patch.dict(os.environ, {provider.env: "test-key"}, clear=False):
                    chosen = partner_apis.active_provider()
                    self.assertIsNotNone(chosen)
                    self.assertTrue(partner_apis.GoogleJobs().enabled)

    def test_each_vendor_reads_the_results_key_it_actually_returns(self):
        """A vendor whose key is wrong fails soft: "works", zero jobs, no error.

        SearchApi.io returns `jobs` where SerpApi returns `jobs_results`, and
        the shared default silently discarded every result.
        """
        from job_agent.sources import partner_apis
        expected = {"serpapi": "jobs_results", "searchapi": "jobs"}
        for provider in partner_apis.PROVIDERS:
            with self.subTest(provider=provider.name):
                self.assertEqual(provider.results_key, expected[provider.name])

    def test_an_apply_link_is_found_however_the_vendor_spells_it(self):
        from job_agent.sources import partner_apis
        google = partner_apis.GoogleJobs()
        shapes = (
            {"apply_options": [{"title": "Apply", "link": "https://a.example/1"}]},
            {"apply_link": "https://b.example/2"},
            {"sharing_link": "https://c.example/3"},
            {"share_link": "https://d.example/4"},
        )
        for item in shapes:
            with self.subTest(shape=sorted(item)[0]):
                self.assertTrue(google._apply_url(item).startswith("https://"))
        self.assertEqual(google._apply_url({"title": "no link anywhere"}), "")

    def test_a_vendors_payload_is_parsed_whichever_shape_it_uses(self):
        from job_agent.sources import partner_apis
        google = partner_apis.GoogleJobs()
        shapes = {
            "serpapi": {"apply_options": [{"link": "https://e.example/1"}],
                        "detected_extensions": {"schedule_type": "Full-time"}},
            "searchapi": {"apply_link": "https://e.example/2",
                          "detected_extensions": {"schedule": "Full-time"}},
        }
        for provider in partner_apis.PROVIDERS:
            with self.subTest(provider=provider.name):
                item = {"title": "Engineer", "company_name": "Acme",
                        "location": "Karachi", "description": "<p>Build things</p>",
                        **shapes[provider.name]}
                postings = google._postings({provider.results_key: [item]}, provider)
                self.assertEqual(len(postings), 1)
                self.assertEqual(postings[0].title, "Engineer")
                self.assertEqual(postings[0].company, "Acme")
                self.assertEqual(postings[0].employment_type_raw, "Full-time")
                self.assertTrue(postings[0].apply_url.startswith("https://"))

    def test_no_results_is_not_reported_as_a_bad_key(self):
        """A vendor that answers but has nothing to say has a working key.

        Google Jobs returns nothing for some country and wording combinations,
        and reporting that as a rejected key sends people to re-issue a key
        that was never the problem.
        """
        from job_agent.sources import partner_apis
        empty = "Google Jobs didn't return any results."
        self.assertIsNone(partner_apis.REFUSAL_WORDS.search(empty))
        for refusal in ("Invalid API key.", "Unauthorized", "quota exceeded",
                        "rate limit reached", "your subscription has ended"):
            with self.subTest(refusal=refusal):
                self.assertIsNotNone(partner_apis.REFUSAL_WORDS.search(refusal))

    def test_the_key_goes_in_the_parameter_the_vendor_expects(self):
        from unittest.mock import patch
        from job_agent.sources import partner_apis
        provider = partner_apis.PROVIDERS[0]
        with patch.dict(os.environ, {provider.env: "test-key"}, clear=False):
            params = partner_apis.GoogleJobs()._params("welder")
        self.assertEqual(params[provider.key_param], "test-key")
        self.assertEqual(params["engine"], "google_jobs")

    def test_the_probe_names_every_vendor_and_what_they_unlock(self):
        from job_agent.sources import partner_apis
        with _without_vendor_keys():
            ok, message = partner_apis.GoogleJobs().probe()
        self.assertFalse(ok)
        self.assertIn("Indeed", message)
        for provider in partner_apis.PROVIDERS:
            self.assertIn(provider.env, message)

    def test_a_result_becomes_a_posting(self):
        from unittest.mock import patch
        self.searching("United Arab Emirates")
        with patch("job_agent.sources.partner_apis.get_json", return_value=self.PAYLOAD):
            jobs = self.source().fetch()
        self.assertEqual(len(jobs), 1, "the entry with no apply link must be skipped")
        job = jobs[0]
        self.assertEqual(job.title, "Electrician - Interiors")
        self.assertEqual(job.company, "Exhibition and Interiors")
        self.assertEqual(job.apply_url, "https://ae.indeed.com/viewjob?jk=9d99")
        self.assertEqual(job.employment_type_raw, "Full-time")
        self.assertIn("AED", job.salary_raw)
        self.assertIn("Indeed", job.tags)

    def test_the_originating_board_is_recorded(self):
        from unittest.mock import patch
        self.searching("United Arab Emirates")
        with patch("job_agent.sources.partner_apis.get_json", return_value=self.PAYLOAD):
            job = self.source().fetch()[0]
        self.assertEqual(job.extra["aggregated_by"], "serpapi",
                         "the vendor is recorded, so a bad parse is traceable")

    def test_an_error_response_yields_nothing(self):
        from unittest.mock import patch
        self.searching("Pakistan")
        with patch("job_agent.sources.partner_apis.get_json",
                   return_value={"error": "Invalid API key"}):
            self.assertEqual(self.source().fetch(), [])

    def test_the_location_follows_the_search(self):
        search_in("Saudi Arabia")
        self.assertEqual(self.source().location(), "Saudi Arabia")
        self.assertEqual(self.source()._params("welder")["location"], "Saudi Arabia")

    def test_no_country_sends_no_location(self):
        search_in("")
        self.assertNotIn("location", self.source()._params("welder"))


class GoogleJobsPagingTests(unittest.TestCase):
    """Each page is one billed search, so paging is bounded and tier-driven."""

    def tearDown(self):
        profile.reset()

    def page(self, titles, token=""):
        results = [{"title": t, "company_name": f"{t} Ltd", "job_id": t,
                    "apply_options": [{"title": "Indeed", "link": f"https://x/{t}"}]}
                   for t in titles]
        payload = {"jobs_results": results}
        if token:
            payload["serpapi_pagination"] = {"next_page_token": token}
        return payload

    def source(self):
        from job_agent.sources import partner_apis
        src = partner_apis.GoogleJobs()
        src.provider = partner_apis.PROVIDERS[0]
        src.enabled = True
        return src

    def searching(self):
        search_in("United Arab Emirates")
        profile.set_active(replace(profile.active(), search_queries=("electrician",)))

    def test_it_follows_the_next_page_token(self):
        from unittest.mock import patch
        from job_agent import config
        self.searching()
        pages = [self.page(["a", "b"], token="t1"), self.page(["c", "d"])]
        with patch("job_agent.sources.partner_apis.get_json", side_effect=pages), \
             patch.object(config, "GOOGLE_JOBS_PAGES", 3):
            jobs = self.source().fetch()
        self.assertEqual(sorted(j.title for j in jobs), ["a", "b", "c", "d"])

    def test_it_stops_when_there_is_no_further_page(self):
        from unittest.mock import patch
        from job_agent import config
        self.searching()
        calls = []

        def once(*args, **kwargs):
            calls.append(kwargs.get("params", {}))
            return self.page(["only"])

        with patch("job_agent.sources.partner_apis.get_json", side_effect=once), \
             patch.object(config, "GOOGLE_JOBS_PAGES", 5):
            self.source().fetch()
        self.assertEqual(len(calls), 1, "no token means no further request")

    def test_the_page_budget_is_respected(self):
        from unittest.mock import patch
        from job_agent import config
        self.searching()
        endless = self.page(["x"], token="always")
        with patch("job_agent.sources.partner_apis.get_json", return_value=endless) as get, \
             patch.object(config, "GOOGLE_JOBS_PAGES", 2):
            self.source().fetch()
        self.assertEqual(get.call_count, 2, "a board that always offers more must still stop")

    def test_the_quick_tier_reads_a_single_page(self):
        from job_agent import config
        with config.tier("quick"):
            self.assertEqual(self.source().pages(), 1)

    def test_the_deep_tier_reads_more(self):
        from job_agent import config
        with config.tier("deep"):
            self.assertGreater(self.source().pages(), 3)


class BoardParsingTests(unittest.TestCase):
    """The listing shapes these boards actually return, pinned field by field.

    Each parser reads a live API whose payload nobody here controls, so the
    fallbacks matter more than the happy path: a board that renames a field
    should fail a test rather than quietly report empty employers.
    """

    def test_remoteok_skips_the_legal_notice_the_feed_leads_with(self):
        from job_agent.sources.boards import RemoteOK
        self.assertIsNone(RemoteOK._to_raw({"legal": "notice"}))
        self.assertIsNone(RemoteOK._to_raw("not a dict"))

    def test_remoteok_reads_a_posting(self):
        from job_agent.sources.boards import RemoteOK
        job = RemoteOK._to_raw({
            "id": 7, "position": "Senior Python Dev", "company": "Acme",
            "url": "https://remoteok.com/l/7", "apply_url": "https://acme.io/apply",
            "location": "Worldwide", "tags": ["python", 42],
        })
        self.assertEqual(job.source_id, "remoteok-7")
        self.assertEqual(job.title, "Senior Python Dev")
        self.assertEqual(job.apply_url, "https://acme.io/apply")
        self.assertEqual(job.tags, ["python", "42"])
        self.assertEqual(job.salary_currency, "USD")
        self.assertTrue(job.extra["is_remote"])

    def test_remoteok_falls_back_to_the_listing_url_when_there_is_no_apply_link(self):
        from job_agent.sources.boards import RemoteOK
        job = RemoteOK._to_raw({"slug": "abc", "position": "Dev",
                                "url": "https://remoteok.com/l/abc"})
        self.assertEqual(job.source_id, "remoteok-abc")
        self.assertEqual(job.apply_url, "https://remoteok.com/l/abc")

    def test_himalayas_names_the_employer_from_the_slug_when_the_feed_sends_a_placeholder(self):
        from job_agent.sources.boards import Himalayas
        for placeholder in ("", "name", "null", "none", "NULL"):
            with self.subTest(placeholder=placeholder):
                job = Himalayas._to_raw({"companyName": placeholder,
                                         "companySlug": "big-corp-ltd"})
                self.assertEqual(job.company, "Big Corp Ltd")

    def test_himalayas_keeps_a_real_employer_name(self):
        from job_agent.sources.boards import Himalayas
        job = Himalayas._to_raw({"companyName": "Careflow", "companySlug": "cf"})
        self.assertEqual(job.company, "Careflow")

    def test_himalayas_reads_location_restrictions_in_either_shape(self):
        from job_agent.sources.boards import Himalayas
        self.assertEqual(Himalayas._to_raw({"locationRestrictions": ["USA", "Canada"]})
                         .location_raw, "USA, Canada")
        self.assertEqual(Himalayas._to_raw({"locationRestrictions": "Anywhere"})
                         .location_raw, "Anywhere")

    def test_himalayas_treats_an_unrestricted_posting_as_worldwide(self):
        from job_agent.sources.boards import Himalayas
        self.assertEqual(Himalayas._to_raw({}).location_raw, "Worldwide")

    def test_himalayas_takes_the_currency_from_either_field(self):
        from job_agent.sources.boards import Himalayas
        self.assertEqual(Himalayas._to_raw({"currency": "usd"}).salary_currency, "USD")
        self.assertEqual(Himalayas._to_raw({"salaryCurrency": "eur"}).salary_currency, "EUR")

    def test_adzuna_reads_a_posting(self):
        from job_agent.sources.market_boards import Adzuna
        job = Adzuna._to_raw({
            "id": 77, "title": "Electrician",
            "company": {"display_name": "Sparks Ltd"},
            "redirect_url": "https://adzuna/77",
            "location": {"display_name": "Leeds, West Yorkshire"},
            "contract_time": "full_time", "contract_type": "permanent",
            "category": {"label": "Trade & Construction"},
        }, "gb")
        self.assertEqual(job.source_id, "adzuna-77")
        self.assertEqual(job.company, "Sparks Ltd")
        self.assertEqual(job.location_raw, "Leeds, West Yorkshire")
        self.assertEqual(job.employment_type_raw, "full time permanent")
        self.assertEqual(job.tags, ["Trade & Construction"])
        self.assertEqual(job.apply_url, job.url)

    def test_adzuna_names_the_market_when_a_posting_carries_no_location(self):
        from job_agent.sources.market_boards import Adzuna
        job = Adzuna._to_raw({"id": 1, "location": None}, "de")
        self.assertEqual(job.location_raw, "DE")
        self.assertEqual(job.employment_type_raw, "")
        self.assertEqual(job.tags, [])


class LinkedInSweepTests(unittest.TestCase):
    """The search plan and the worker pool that runs it.

    LinkedIn is the one source that fans a single request out into hundreds of
    paged searches across threads, so the parts worth pinning are the shape of
    that plan and the pool's promise that one bad query cannot end the sweep.
    """

    def source(self, deep=False):
        from job_agent.sources.linkedin import LinkedIn
        source = LinkedIn(deep=deep)
        source.geos = lambda: tuple((f"Region {i}", str(i)) for i in range(10))
        source.primary_keywords = lambda: ("electrician",)
        source.secondary_keywords = lambda: ("electrical fitter",)
        source.queries = staticmethod(lambda limit=6: ("electrician",))
        return source

    def test_primary_keywords_sweep_every_region(self):
        from job_agent.sources.linkedin import LinkedIn
        plain = [t for t in self.source()._search_tasks() if t[2] is None]
        self.assertEqual(len(plain), 10 + LinkedIn.SECONDARY_GEOS,
                         "primary sweeps all ten regions, secondary is capped")
        self.assertEqual(len([t for t in plain if t[0] == "electrician"]), 10)

    def test_a_shallow_run_caps_the_secondary_keywords(self):
        from job_agent.sources.linkedin import LinkedIn
        shallow = self.source(deep=False)._search_tasks()
        secondary = [t for t in shallow if t[0] == "electrical fitter" and t[2] is None]
        self.assertEqual(len(secondary), LinkedIn.SECONDARY_GEOS)

    def test_a_deep_run_sweeps_every_region_with_secondary_keywords_too(self):
        deep = self.source(deep=True)._search_tasks()
        secondary = [t for t in deep if t[0] == "electrical fitter" and t[2] is None]
        self.assertEqual(len(secondary), 10)

    def test_a_shallow_run_caps_the_weaker_sweeps(self):
        from job_agent.sources.linkedin import LinkedIn
        shallow = self.source(deep=False)._search_tasks()
        job_type = [t for t in shallow if t[2] is not None]
        self.assertEqual(len(job_type), len(LinkedIn.JOB_TYPES) * LinkedIn.JOB_TYPE_GEOS)

    def test_a_deep_run_sweeps_every_region(self):
        from job_agent.sources.linkedin import LinkedIn
        deep = self.source(deep=True)._search_tasks()
        job_type = [t for t in deep if t[2] is not None]
        self.assertEqual(len(job_type), len(LinkedIn.JOB_TYPES) * 10)
        self.assertGreater(len(deep), len(self.source(deep=False)._search_tasks()))

    def test_job_type_tasks_carry_their_filter_and_label(self):
        from job_agent.sources.linkedin import LinkedIn
        tasks = [t for t in self.source()._search_tasks() if t[2] is not None]
        codes = {t[2]["f_JT"] for t in tasks}
        self.assertEqual(codes, {code for code, _label in LinkedIn.JOB_TYPES})
        self.assertTrue(all(t[3] for t in tasks), "every job-type task is labelled")

    def test_the_pool_runs_every_item(self):
        handled = []
        self.source()._run_pool(list(range(50)), handled.append, 4, "search")
        self.assertEqual(sorted(handled), list(range(50)))

    def test_one_failing_item_does_not_stop_the_sweep(self):
        handled = []

        def flaky(item):
            if item % 7 == 0:
                raise RuntimeError("linkedin said no")
            handled.append(item)

        self.source()._run_pool(list(range(50)), flaky, 4, "search")
        self.assertEqual(sorted(handled), [i for i in range(50) if i % 7])

    def test_an_empty_plan_is_not_an_error(self):
        self.source()._run_pool([], lambda item: None, 4, "search")


class LinkedInDetailBudgetTests(unittest.TestCase):
    """Which adverts earn one of the run's limited detail fetches."""

    def jobs(self, count):
        from job_agent.models import RawJob
        return {f"linkedin-{i}": RawJob(source="linkedin", source_id=f"linkedin-{i}",
                                        title="Electrician", company="Acme",
                                        url=f"https://linkedin.com/jobs/view/{i}")
                for i in range(count)}

    def source(self, budget, cached=()):
        from job_agent.sources.linkedin import LinkedIn
        source = LinkedIn(deep=False)
        source.MAX_DETAILS = budget
        source._restore_from_cache = lambda job: job.source_id in cached
        return source

    def test_the_budget_caps_how_many_adverts_are_fetched(self):
        source = self.source(budget=5)
        self.assertEqual(len(source._select_pending(self.jobs(20))), 5)

    def test_cached_adverts_do_not_spend_the_budget(self):
        cached = {f"linkedin-{i}" for i in range(15)}
        source = self.source(budget=5, cached=cached)
        pending = source._select_pending(self.jobs(20))
        self.assertEqual(len(pending), 5, "the five uncached adverts")
        self.assertEqual(source.cache_hits, 15)
        self.assertFalse({j.source_id for j in pending} & cached)

    def test_a_fully_cached_run_fetches_nothing(self):
        cached = {f"linkedin-{i}" for i in range(20)}
        source = self.source(budget=5, cached=cached)
        self.assertEqual(source._select_pending(self.jobs(20)), [])
        self.assertEqual(source.cache_hits, 20)

    def test_the_rate_limit_delay_is_paid_even_when_an_advert_fails(self):
        from unittest.mock import patch
        from job_agent.models import RawJob
        source = self.source(budget=1)
        source._attach_description = lambda job: (_ for _ in ()).throw(RuntimeError("429"))
        job = RawJob(source="linkedin", source_id="linkedin-1", title="", company="", url="")
        with patch("job_agent.sources.linkedin.time.sleep") as slept:
            with self.assertRaises(RuntimeError):
                source._fetch_detail(job)
        slept.assert_called_once_with(source.DETAIL_DELAY)


class SourceNameRegistryTests(unittest.TestCase):
    """`--sources` must accept every name the tool itself prints."""

    def test_every_advertised_source_is_selectable(self):
        from job_agent.sources import known_source_names, source_catalogue
        shown = {name for name, _label, _configured in source_catalogue()}
        self.assertEqual(shown - known_source_names(), set())

    def test_the_offline_corpus_is_selectable(self):
        from job_agent.sources import known_source_names
        from job_agent.sources.fixtures import FixtureSource
        self.assertIn(FixtureSource.name, known_source_names())

    def test_a_name_no_connector_answers_to_is_not_accepted(self):
        from job_agent.sources import known_source_names
        self.assertNotIn("nosuchsource", known_source_names())


class TagListTests(unittest.TestCase):
    """Boards disagree about how a list field is sent, so one reader settles it.

    A bare string used to be iterated character by character, so a board
    answering `"tags": "healthcare"` produced ten single-letter tags.
    """

    def test_a_real_list_is_kept(self):
        from job_agent.sources.boards import tag_list
        self.assertEqual(tag_list(["python", "django"]), ["python", "django"])

    def test_a_bare_string_is_one_tag_not_one_per_letter(self):
        from job_agent.sources.boards import tag_list
        self.assertEqual(tag_list("healthcare"), ["healthcare"])

    def test_a_comma_separated_string_is_split(self):
        from job_agent.sources.boards import tag_list
        self.assertEqual(tag_list("a, b ,c"), ["a", "b", "c"])

    def test_nulls_and_blanks_are_dropped_rather_than_stringified(self):
        from job_agent.sources.boards import tag_list
        self.assertEqual(tag_list(["python", None, "", 42]), ["python", "42"])

    def test_nothing_at_all_is_no_tags(self):
        from job_agent.sources.boards import tag_list
        for empty in (None, "", [], {}):
            with self.subTest(value=empty):
                self.assertEqual(tag_list(empty), [])

    def test_the_boards_use_it(self):
        from job_agent.sources.boards import Himalayas, RemoteOK
        self.assertEqual(RemoteOK._to_raw({"position": "X", "tags": "healthcare"}).tags,
                         ["healthcare"])
        self.assertEqual(Himalayas._to_raw({"title": "X", "categories": "health",
                                            "seniority": "mid"}).tags,
                         ["health", "mid"])
