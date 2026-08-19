"""Tests for employer contact discovery.

Ported from deep_run, with the mailbox exclusions kept: an address that exists
to receive legal or privacy correspondence is not a route to a job.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from job_agent import contacts


class EmailFilteringTests(unittest.TestCase):
    def test_a_careers_mailbox_outranks_a_general_one(self):
        found = contacts.emails_in("hello@acme.com careers@acme.com sales@acme.com")
        self.assertEqual(found[0], "careers@acme.com")
        self.assertEqual(found[-1], "sales@acme.com")

    def test_legal_and_privacy_mailboxes_are_excluded(self):
        for address in ("dpo@acme.com", "privacy@acme.com", "legal@acme.com",
                        "gdpr@acme.com", "noreply@acme.com", "postmaster@acme.com"):
            with self.subTest(address=address):
                self.assertFalse(contacts.usable_email(address), address)

    def test_asset_filenames_are_not_addresses(self):
        for junk in ("logo@2x.png", "sprite@3x.svg", "a@b.css"):
            with self.subTest(junk=junk):
                self.assertFalse(contacts.usable_email(junk))

    def test_library_and_schema_addresses_are_excluded(self):
        self.assertFalse(contacts.usable_email("someone@sentry.io"))
        self.assertFalse(contacts.usable_email("info@example.com"))

    def test_an_unknown_tld_is_rejected(self):
        self.assertFalse(contacts.usable_email("careers@acme.invalidtld"))

    def test_country_tlds_are_accepted(self):
        for address in ("careers@acme.ng", "jobs@acme.co.uk", "hr@acme.in"):
            with self.subTest(address=address):
                self.assertTrue(contacts.usable_email(address), address)

    def test_duplicates_collapse(self):
        self.assertEqual(contacts.emails_in("a@acme.com A@ACME.COM"), ["a@acme.com"])


class PeopleTests(unittest.TestCase):
    def test_name_then_role_is_read(self):
        self.assertIn("Amara Okafor — Founder",
                      contacts.people_in("<p>Amara Okafor — Founder</p>"))

    def test_role_then_name_is_read(self):
        self.assertIn("Tom Blake — Hiring Manager",
                      contacts.people_in("<p>Hiring Manager: Tom Blake</p>"))

    def test_markup_and_entities_do_not_confuse_it(self):
        found = contacts.people_in("<div><b>Sara&nbsp;Hill</b>, <i>CTO</i></div>")
        self.assertTrue(any("Sara Hill" in person for person in found), found)

    def test_ordinary_prose_yields_nobody(self):
        self.assertEqual(contacts.people_in("<p>We are a friendly team.</p>"), [])


class SiteFetchTests(unittest.TestCase):
    def test_a_non_https_site_is_not_fetched(self):
        with patch.object(contacts, "http_get") as get:
            self.assertEqual(contacts.for_site("http://acme.com"),
                             {"emails": [], "people": []})
        get.assert_not_called()

    def test_an_empty_website_is_not_fetched(self):
        with patch.object(contacts, "http_get") as get:
            contacts.for_site("")
        get.assert_not_called()

    def test_an_off_site_careers_link_is_not_followed(self):
        class Response:
            text = "<p>careers@acme.com</p>"

        with patch.object(contacts, "http_get", return_value=Response()) as get:
            contacts.for_site("https://acme.com", careers="https://elsewhere.com/jobs")
        for call in get.call_args_list:
            self.assertIn("acme.com", call[0][0])

    def test_the_number_of_pages_is_capped(self):
        class Response:
            text = ""

        with patch.object(contacts, "http_get", return_value=Response()) as get:
            contacts.for_site("https://acme.com")
        self.assertLessEqual(get.call_count, contacts.MAX_PAGES)

    def test_an_unreachable_site_returns_empty_not_an_error(self):
        with patch.object(contacts, "http_get", return_value=None):
            self.assertEqual(contacts.for_site("https://acme.com"),
                             {"emails": [], "people": []})


class EnrichTests(unittest.TestCase):
    """These need the stage switched on — the tier globals are process-wide."""

    def setUp(self):
        from job_agent import config
        self._saved = config.FETCH_EMPLOYER_CONTACTS
        config.FETCH_EMPLOYER_CONTACTS = True

    def tearDown(self):
        from job_agent import config
        config.FETCH_EMPLOYER_CONTACTS = self._saved

    def job(self, **kwargs):
        from job_agent.models import Job
        base = {"title": "Plumber", "company": "Acme",
                    "company_website": "https://acme.com"}
        base.update(kwargs)
        return Job(**base)

    def test_details_are_filled_in_when_missing(self):
        found = {"emails": ["careers@acme.com"], "people": ["Amara Okafor — Founder"]}
        target = self.job()
        with patch.object(contacts, "for_site", return_value=found):
            contacts.enrich(target)
        self.assertEqual(target.public_email, "careers@acme.com")
        self.assertEqual(target.best_contact_name, "Amara Okafor")
        self.assertEqual(target.contact_role, "Founder")

    def test_details_already_found_in_the_advert_are_not_overwritten(self):
        target = self.job(public_email="hiring@acme.com", best_contact_name="Existing")
        with patch.object(contacts, "for_site") as site:
            contacts.enrich(target)
        site.assert_not_called()
        self.assertEqual(target.public_email, "hiring@acme.com")

    def test_it_can_be_switched_off(self):
        from job_agent import config
        original = config.FETCH_EMPLOYER_CONTACTS
        try:
            config.FETCH_EMPLOYER_CONTACTS = False
            with patch.object(contacts, "for_site") as site:
                contacts.enrich(self.job())
            site.assert_not_called()
        finally:
            config.FETCH_EMPLOYER_CONTACTS = original


if __name__ == "__main__":
    unittest.main()
