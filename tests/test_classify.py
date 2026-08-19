"""Tests for advert classification, across trades as well as technology."""

from __future__ import annotations

import unittest

from job_agent import profile
from job_agent.classify import core_skills_required, required_years
from job_agent.models import RawJob


def posting(title="Plumber", description="", tags=None) -> RawJob:
    return RawJob(source="test", source_id="1", title=title, company="Acme",
                  url="https://example.com/jobs/1", description=description,
                  tags=tags or [])


class RequiredYearsTests(unittest.TestCase):
    """A stated experience bar must be read for any trade, not only software."""

    def assert_years(self, text: str, expected: int | None):
        self.assertEqual(required_years(text.lower()), expected, text)

    def test_a_trade_states_its_bar_without_the_word_experience(self):
        self.assert_years("We need 5+ years of plumbing on domestic systems.", 5)
        self.assert_years("3 years as a qualified electrician required.", 3)
        self.assert_years("2 years of HGV driving.", 2)
        self.assert_years("5+ years of welding and fabrication.", 5)

    def test_clinical_and_office_phrasing_still_reads(self):
        self.assert_years("4+ years in nursing within an acute setting.", 4)
        self.assert_years("Minimum 6 years of commercial software development.", 6)
        self.assert_years("7+ years experience building mobile apps.", 7)

    def test_company_tenure_is_not_an_entry_bar(self):
        self.assert_years("A family firm with 20 years of service to the county.", None)
        self.assert_years("Founded 12 years ago in Leeds.", None)

    def test_nothing_stated_returns_nothing(self):
        self.assert_years("A great place to work.", None)


class CoreSkillsWithoutAProfileTests(unittest.TestCase):
    """With no core terms compiled, the classifier must not invent a vocabulary."""

    def tearDown(self):
        profile.reset()

    def test_an_empty_profile_reports_no_core_skill(self):
        from dataclasses import replace
        profile.set_active(replace(profile.active(), key="compiled:test", core_terms=()))
        self.assertEqual(core_skills_required(posting(), ""), ("No", "No"))

    def test_a_trade_profile_reads_its_own_terms(self):
        from dataclasses import replace
        profile.set_active(replace(profile.active(), key="compiled:test",
                                   core_terms=("plumbing", "gas")))
        primary, secondary = core_skills_required(
            posting(title="Plumbing Engineer"),
            "must hold a gas safe registration. plumbing is essential.")
        self.assertEqual(primary, "Yes")
        self.assertEqual(secondary, "Yes")


if __name__ == "__main__":
    unittest.main()
