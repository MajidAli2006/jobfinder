"""The engine must work for any trade, in any country."""

from __future__ import annotations

import unittest

from job_agent import classify, filters, pipeline, profile, remote, scoring
from job_agent.models import Job, RawJob


def advert(title, description, location="", tags=None) -> RawJob:
    return RawJob(source="test", source_id="1", title=title, company="Acme",
                  url="https://example.com/jobs/1", description=description,
                  location_raw=location, tags=tags or [])


def search(**overrides) -> profile.SearchProfile:
    """A compiled-looking profile with nothing inherited from anywhere."""
    base = {
        "key": "compiled:test", "label": "Work", "query": "work",
        "core_terms": (), "secondary_terms": (), "hands_on_title_tokens": (),
        "hard_title_exclusions": (), "other_discipline_terms": (),
        "title_exclusion_regexes": (), "competing_stacks": {},
        "min_body_core_mentions": 2, "skills": {}, "domain_keywords": {},
        "seniority": "Unspecified", "years_experience": 0, "has_cv": False,
        "home_country": "", "home_terms": (), "home_city_terms": (),
        "target_regions": (), "region_terms": (), "region_excluding_home_terms": (),
        "search_queries": (), "work_arrangement": "any", "salary_floor_usd": 0.0,
        "timezone": "",
    }
    base.update(overrides)
    return profile.SearchProfile(**base)


TRADES = {
    "plumbing": {
        "label": "Plumbing", "core_terms": ("plumbing", "pipefitting"),
        "hands_on_title_tokens": ("plumber", "fitter", "installer", "engineer"),
        "title": "Domestic Plumber",
        "body": "Experienced plumber wanted for domestic plumbing and pipefitting "
             "work. Plumbing qualifications essential. Pipefitting a plus.",
    },
    "electrical": {
        "label": "Electrical", "core_terms": ("electrician", "electrical"),
        "hands_on_title_tokens": ("electrician", "engineer", "technician"),
        "title": "Qualified Electrician",
        "body": "Electrician needed for commercial electrical installs. "
             "Electrical testing and inspection. 18th edition electrician.",
    },
    "nursing": {
        "label": "Nursing", "core_terms": ("nursing", "nurse"),
        "hands_on_title_tokens": ("nurse", "practitioner", "sister"),
        "title": "Staff Nurse - Acute Medicine",
        "body": "Registered nurse for our acute ward. Nursing degree required. "
             "Nursing experience in an acute setting. NMC registered nurse.",
    },
    "driving": {
        "label": "HGV driving", "core_terms": ("hgv", "driving"),
        "hands_on_title_tokens": ("driver", "operator"),
        "title": "HGV Class 1 Driver",
        "body": "HGV driver needed for regional distribution. HGV licence and "
             "driving experience essential. Class 1 driving work.",
    },
    "teaching": {
        "label": "Teaching", "core_terms": ("teaching", "teacher"),
        "hands_on_title_tokens": ("teacher", "tutor", "lecturer"),
        "title": "Secondary Maths Teacher",
        "body": "Teacher of mathematics. Qualified teacher status required. "
             "Teaching across KS3 and KS4. Teaching experience preferred.",
    },
}

COUNTRIES = {
    "Nigeria": ("nigeria", "lagos", "abuja"),
    "United States": ("us", "usa", "united states"),
    "Australia": ("australia", "sydney", "melbourne"),
    "Germany": ("germany", "berlin", "munich"),
    "India": ("india", "mumbai", "bengaluru"),
}


class EveryTradeIsSearchable(unittest.TestCase):
    """A trade advert must survive the relevance gate on its own vocabulary."""

    def tearDown(self):
        profile.reset()

    def test_each_trade_keeps_its_own_adverts(self):
        for name, spec in TRADES.items():
            with self.subTest(trade=name):
                profile.set_active(search(
                    label=spec["label"], core_terms=spec["core_terms"],
                    hands_on_title_tokens=spec["hands_on_title_tokens"]))
                verdict = filters.check_relevance(advert(spec["title"], spec["body"]))
                self.assertTrue(verdict.passed, f"{name}: {verdict.reason}")

    def test_no_trade_leaks_into_another(self):
        for name, spec in TRADES.items():
            profile.set_active(search(
                label=spec["label"], core_terms=spec["core_terms"],
                hands_on_title_tokens=spec["hands_on_title_tokens"]))
            for other, other_spec in TRADES.items():
                if other == name:
                    continue
                with self.subTest(searching=name, advert=other):
                    verdict = filters.check_relevance(
                        advert(other_spec["title"], other_spec["body"]))
                    self.assertFalse(verdict.passed,
                                     f"a {name} search kept a {other} advert")

    def test_a_software_advert_does_not_ride_in_on_a_trade_search(self):
        spec = TRADES["plumbing"]
        profile.set_active(search(
            label=spec["label"], core_terms=spec["core_terms"],
            hands_on_title_tokens=spec["hands_on_title_tokens"]))
        verdict = filters.check_relevance(advert(
            "Senior Flutter Engineer", "Flutter and Dart mobile apps. Flutter team."))
        self.assertFalse(verdict.passed)


class NoInheritedVocabulary(unittest.TestCase):
    """With an empty profile nothing may supply a vocabulary of its own."""

    def tearDown(self):
        profile.reset()

    def test_the_classifier_reports_no_core_skill(self):
        profile.set_active(search())
        self.assertEqual(classify.core_skills_required(advert("Plumber", "plumbing"), ""),
                         ("No", "No"))

    def test_the_scorer_awards_no_core_points(self):
        profile.set_active(search())
        job = Job(title="Senior Flutter Developer", company="Acme")
        job.core_skill_required = "Yes"
        score, reasons, _ = scoring.score_match(job, "flutter dart mobile")
        self.assertNotIn("Flutter", " ".join(reasons))

    def test_scoring_never_mentions_a_trade_the_search_did_not_name(self):
        spec = TRADES["nursing"]
        profile.set_active(search(label=spec["label"], core_terms=spec["core_terms"],
                                  skills={"triage": 4.0}))
        job = Job(title="Staff Nurse", company="Acme")
        job.core_skill_required = "Yes"
        _, reasons, concerns = scoring.score_match(job, "nursing triage acute ward")
        text = " ".join(reasons + concerns).lower()
        for leaked in ("flutter", "dart", "fintech", "mobile experience"):
            self.assertNotIn(leaked, text)


class EligibilitySpeaksTheRightCountry(unittest.TestCase):
    def tearDown(self):
        profile.reset()

    def test_the_home_country_is_named_not_the_uk(self):
        for country, terms in COUNTRIES.items():
            with self.subTest(country=country):
                profile.set_active(search(home_country=country, home_terms=terms,
                                          work_arrangement="remote"))
                verdict = remote.assess_remote(advert(
                    "Support Engineer", f"Fully remote within {country}.",
                    location=f"Remote ({country})"))
                self.assertTrue(verdict.passed, verdict.reason)
                self.assertIn(country, verdict.remote_status)
                self.assertNotIn("UK", verdict.remote_status)

    def test_a_worldwide_role_is_covered_from_anywhere(self):
        for country, terms in COUNTRIES.items():
            with self.subTest(country=country):
                profile.set_active(search(home_country=country, home_terms=terms,
                                          work_arrangement="remote"))
                verdict = remote.assess_remote(advert(
                    "Support Engineer", "Fully remote, work from anywhere in the world.",
                    location="Remote - Worldwide"))
                self.assertTrue(verdict.passed, verdict.reason)


class OnSiteWorkIsIncludedByDefault(unittest.TestCase):
    """Someone who never said "remote" must still see on-site work."""

    def tearDown(self):
        profile.reset()

    def _screen(self, *, arrangement: str):
        spec = TRADES["electrical"]
        custom = search(label=spec["label"], core_terms=spec["core_terms"],
                        hands_on_title_tokens=spec["hands_on_title_tokens"],
                        home_country="Nigeria", home_terms=COUNTRIES["Nigeria"],
                        work_arrangement=arrangement)
        return pipeline.run(offline=True, verify_live=False, use_llm=False,
                            search_profile=custom).stats

    def test_an_on_site_search_rejects_nothing_for_being_on_site(self):
        self.assertEqual(self._screen(arrangement="any").rejected_not_remote, 0)

    def test_asking_for_remote_still_rejects_on_site_work(self):
        self.assertGreater(self._screen(arrangement="remote").rejected_not_remote, 0)


class YourOwnCountryIsNeverBeneathYou(unittest.TestCase):
    """The low-rate market filter is about arbitrage, not about a place."""

    def tearDown(self):
        profile.reset()

    LOCAL = advert("Qualified Electrician",
                   "Electrician for commercial electrical installs across the city. "
                   "Electrical testing and inspection. On-site role.",
                   location="Lagos, Nigeria")

    def test_a_nigerian_search_keeps_nigerian_work(self):
        profile.set_active(search(home_country="Nigeria",
                                  home_terms=("nigeria", "lagos", "abuja")))
        self.assertNotIn("nigeria", filters.low_rate_markets())
        self.assertTrue(filters.check_market(self.LOCAL).passed)

    def test_an_indian_search_keeps_indian_work(self):
        profile.set_active(search(home_country="India", home_terms=("india", "mumbai")))
        self.assertNotIn("india", filters.low_rate_markets())

    def test_a_named_target_country_is_also_kept(self):
        profile.set_active(search(home_country="United Kingdom",
                                  home_terms=("uk", "united kingdom"),
                                  target_regions=("United Kingdom", "Kenya")))
        self.assertNotIn("kenya", filters.low_rate_markets())

    def test_the_filter_still_works_for_somewhere_they_did_not_name(self):
        profile.set_active(search(home_country="United Kingdom",
                                  home_terms=("uk", "united kingdom")))
        self.assertIn("nigeria", filters.low_rate_markets())
        self.assertFalse(filters.check_market(self.LOCAL).passed)


class TheClockFollowsTheCandidate(unittest.TestCase):
    """Freshness is counted in the candidate's days, not in British ones."""

    def tearDown(self):
        profile.reset()

    def test_the_timezone_comes_from_the_profile(self):
        from job_agent.utils import local_timezone
        for zone in ("Australia/Sydney", "America/New_York", "Africa/Lagos", "Asia/Kolkata"):
            with self.subTest(zone=zone):
                profile.set_active(search(timezone=zone))
                self.assertEqual(local_timezone().key, zone)

    def test_an_unset_timezone_falls_back_rather_than_failing(self):
        from job_agent.utils import local_timezone
        profile.set_active(search(timezone=""))
        self.assertTrue(local_timezone().key)

    def test_a_nonsense_timezone_does_not_break_the_run(self):
        from job_agent.utils import local_timezone
        profile.set_active(search(timezone="Not/AZone"))
        self.assertTrue(local_timezone().key)

    SYDNEY = "Australia/Sydney"

    def _aged(self, now, posted):
        """`job_age` as at a fixed local moment, so the clock cannot decide it."""
        from unittest import mock
        from job_agent import utils
        with mock.patch.object(utils, "now_local", return_value=now):
            return utils.job_age(posted)

    def test_the_candidates_timezone_is_the_one_used(self):
        from zoneinfo import ZoneInfo
        from job_agent.utils import now_local
        profile.set_active(search(timezone=self.SYDNEY))
        self.assertEqual(now_local().tzinfo, ZoneInfo(self.SYDNEY))

    def test_a_posting_from_earlier_today_is_today(self):
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        profile.set_active(search(timezone=self.SYDNEY))
        midday = datetime(2026, 8, 19, 12, 0, tzinfo=ZoneInfo(self.SYDNEY))
        self.assertEqual(self._aged(midday, midday - timedelta(hours=2)), (0, "Today"))

    def test_a_posting_from_before_local_midnight_is_yesterday(self):
        """Two hours old is not always today — this is why the timezone matters.

        Measured in London the same instant is still today; measured in Sydney,
        just after midnight, it fell on the previous calendar day. Pinning the
        moment keeps this from depending on when the suite happens to run.
        """
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        profile.set_active(search(timezone=self.SYDNEY))
        just_past_midnight = datetime(2026, 8, 19, 1, 0, tzinfo=ZoneInfo(self.SYDNEY))
        self.assertEqual(self._aged(just_past_midnight,
                                    just_past_midnight - timedelta(hours=2)),
                         (1, "Yesterday"))


class WorkArrangementTests(unittest.TestCase):
    """Remote, hybrid and on-site are three answers, not one boolean."""

    def tearDown(self):
        profile.reset()

    CASES = (
        ("Fully remote, work from home.", "remote"),
        ("Hybrid - three days a week in the office.", "hybrid"),
        ("Remote/hybrid, split between home and the office.", "hybrid"),
        ("On-site role, office based in Leeds.", "onsite"),
        ("You must be able to commute to our office daily.", "onsite"),
        ("A great place to work with good benefits.", "unknown"),
        ("Fully remote. No hybrid working.", "remote"),
    )

    def test_each_arrangement_is_read_from_the_advert(self):
        profile.set_active(search())
        for text, expected in self.CASES:
            with self.subTest(text=text):
                self.assertEqual(remote.classify_arrangement(text.lower()), expected)

    def test_hybrid_wins_over_a_bare_remote_mention(self):
        profile.set_active(search())
        self.assertEqual(
            remote.classify_arrangement("remote/hybrid working available"), "hybrid")

    def test_any_accepts_everything(self):
        profile.set_active(search(work_arrangement="any"))
        for arrangement in ("remote", "hybrid", "onsite", "unknown"):
            self.assertTrue(remote.arrangement_wanted(arrangement), arrangement)

    def test_remote_accepts_only_remote(self):
        profile.set_active(search(work_arrangement="remote"))
        self.assertTrue(remote.arrangement_wanted("remote"))
        for arrangement in ("hybrid", "onsite", "unknown"):
            self.assertFalse(remote.arrangement_wanted(arrangement), arrangement)

    def test_hybrid_accepts_hybrid_and_the_unstated(self):
        profile.set_active(search(work_arrangement="hybrid"))
        self.assertTrue(remote.arrangement_wanted("hybrid"))
        self.assertTrue(remote.arrangement_wanted("unknown"))
        self.assertFalse(remote.arrangement_wanted("remote"))
        self.assertFalse(remote.arrangement_wanted("onsite"))

    def test_onsite_accepts_onsite_and_the_unstated(self):
        profile.set_active(search(work_arrangement="onsite"))
        self.assertTrue(remote.arrangement_wanted("onsite"))
        self.assertTrue(remote.arrangement_wanted("unknown"))
        self.assertFalse(remote.arrangement_wanted("remote"))
        self.assertFalse(remote.arrangement_wanted("hybrid"))

    def test_a_hybrid_search_rejects_on_site_work(self):
        spec = TRADES["plumbing"]
        custom = search(label=spec["label"], core_terms=spec["core_terms"],
                        hands_on_title_tokens=spec["hands_on_title_tokens"],
                        home_country="United Kingdom",
                        home_terms=("uk", "united kingdom", "england"),
                        work_arrangement="hybrid")
        stats = pipeline.run(offline=True, verify_live=False, use_llm=False,
                             search_profile=custom).stats
        self.assertEqual(stats.qualified, 0)
        self.assertGreater(stats.rejected_not_remote, 0)


if __name__ == "__main__":
    unittest.main()
