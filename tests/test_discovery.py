"""Tests for platform discovery, and above all for its URL validation.

A model naming a URL that this code then fetches is a server-side request
forgery surface. These tests are the security boundary: if they pass, a
hallucinated or poisoned answer cannot make the tool reach an internal
address.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from job_agent import discovery


def candidate(url: str, **kwargs) -> discovery.Candidate:
    base = {"name": "board", "label": "Board", "country": "Nigeria",
                "url_template": url, "kind": "jsonld"}
    base.update(kwargs)
    return discovery.Candidate(**base)


class SchemeAndShapeTests(unittest.TestCase):
    """Checks that need no DNS."""

    def reason(self, url: str) -> str:
        return discovery.rejection(candidate(url), resolve=False)

    def test_a_well_formed_https_url_passes(self):
        self.assertEqual(self.reason("https://www.jobberman.com/jobs?q={query}"), "")

    def test_a_missing_placeholder_is_rejected(self):
        self.assertIn("placeholder", self.reason("https://example.com/jobs"))

    def test_plain_http_is_rejected(self):
        self.assertIn("scheme", self.reason("http://example.com/jobs?q={query}"))

    def test_file_urls_are_rejected(self):
        self.assertIn("scheme", self.reason("file:///etc/passwd?q={query}"))

    def test_other_schemes_are_rejected(self):
        for url in ("ftp://example.com/{query}", "gopher://example.com/{query}",
                    "data:text/plain,{query}", "javascript:alert('{query}')"):
            with self.subTest(url=url):
                self.assertNotEqual(self.reason(url), "")

    def test_embedded_credentials_are_rejected(self):
        self.assertIn("credentials",
                      self.reason("https://user:pw@example.com/jobs?q={query}"))

    def test_a_non_standard_port_is_rejected(self):
        self.assertIn("port", self.reason("https://example.com:8080/jobs?q={query}"))

    def test_port_443_is_allowed(self):
        self.assertEqual(self.reason("https://example.com:443/jobs?q={query}"), "")

    def test_a_bare_hostname_is_rejected(self):
        self.assertIn("hostname", self.reason("https://localhost/jobs?q={query}"))


class AddressTests(unittest.TestCase):
    """The address a host resolves to is what actually matters."""

    def reason(self, url: str) -> str:
        return discovery.rejection(candidate(url), resolve=True)

    def test_loopback_is_rejected_even_behind_a_public_name(self):
        with patch.object(discovery.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
            self.assertIn("public address", self.reason("https://evil.example.com/{query}"))

    def test_the_cloud_metadata_address_is_rejected(self):
        with patch.object(discovery.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("169.254.169.254", 0))]):
            self.assertIn("public address", self.reason("https://metadata.example.com/{query}"))

    def test_private_ranges_are_rejected(self):
        for address in ("10.0.0.5", "192.168.1.1", "172.16.0.9"):
            with self.subTest(address=address):
                with patch.object(discovery.socket, "getaddrinfo",
                                  return_value=[(2, 1, 6, "", (address, 0))]):
                    self.assertNotEqual(self.reason("https://internal.example.com/{query}"), "")

    def test_ipv6_loopback_is_rejected(self):
        with patch.object(discovery.socket, "getaddrinfo",
                          return_value=[(10, 1, 6, "", ("::1", 0, 0, 0))]):
            self.assertNotEqual(self.reason("https://evil.example.com/{query}"), "")

    def test_one_bad_address_among_several_rejects_the_host(self):
        # A host that resolves to both a public and a private address is the
        # classic DNS rebinding shape.
        with patch.object(discovery.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("93.184.216.34", 0)),
                                        (2, 1, 6, "", ("127.0.0.1", 0))]):
            self.assertNotEqual(self.reason("https://mixed.example.com/{query}"), "")

    def test_a_public_address_passes(self):
        with patch.object(discovery.socket, "getaddrinfo",
                          return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            self.assertEqual(self.reason("https://www.example.com/jobs?q={query}"), "")

    def test_a_host_that_does_not_resolve_is_rejected(self):
        with patch.object(discovery.socket, "getaddrinfo",
                          side_effect=OSError("nope")):
            self.assertNotEqual(self.reason("https://nowhere.example.com/{query}"), "")


class FilteringTests(unittest.TestCase):
    def test_unsafe_candidates_are_dropped_not_raised_on(self):
        mixed = [candidate("https://good.example.com/jobs?q={query}"),
                 candidate("http://bad.example.com/jobs?q={query}"),
                 candidate("https://no-placeholder.example.com/jobs")]
        kept = discovery.safe_candidates(mixed, resolve=False)
        self.assertEqual([c.url_template for c in kept],
                         ["https://good.example.com/jobs?q={query}"])

    def test_the_number_of_platforms_is_capped(self):
        many = [candidate(f"https://board{i}.example.com/jobs?q={{query}}")
                for i in range(50)]
        self.assertLessEqual(len(discovery.safe_candidates(many, resolve=False)),
                             discovery.MAX_PLATFORMS)


class SearchUrlTests(unittest.TestCase):
    def test_the_query_is_url_encoded_into_the_placeholder(self):
        built = candidate("https://example.com/jobs?q={query}").search_url("gas engineer")
        self.assertEqual(built, "https://example.com/jobs?q=gas+engineer")

    def test_a_query_cannot_smuggle_url_structure(self):
        built = candidate("https://example.com/jobs?q={query}").search_url(
            "x&admin=1 https://evil.example.com")
        self.assertNotIn("&admin=1", built)
        self.assertNotIn("://evil", built)


class DiscoverTests(unittest.TestCase):
    def test_no_country_means_no_discovery(self):
        self.assertEqual(discovery.discover("", "plumbing"), [])

    def test_a_cached_payload_is_validated_not_trusted(self):
        poisoned = {"platforms": [
            {"name": "evil", "label": "Evil", "country": "Nigeria",
             "url_template": "http://169.254.169.254/{query}", "kind": "jsonld",
             "needs_key": False},
            {"name": "good", "label": "Good", "country": "Nigeria",
             "url_template": "https://www.jobberman.com/jobs?q={query}",
             "kind": "jsonld", "needs_key": False},
        ]}
        with patch.object(discovery.cache, "get", return_value=poisoned):
            kept = discovery.discover("Nigeria", "plumbing", resolve=False)
        self.assertEqual([c.name for c in kept], ["good"])

    def test_junk_rows_are_skipped(self):
        payload = {"platforms": ["not a dict", {"name": "", "url_template": ""}, None]}
        with patch.object(discovery.cache, "get", return_value=payload):
            self.assertEqual(discovery.discover("Nigeria", "plumbing", resolve=False), [])


class JsonLdParsingTests(unittest.TestCase):
    """The generic reader must handle the shapes boards actually publish."""

    def setUp(self):
        from job_agent.sources.boards import DiscoveredPlatforms
        self.source = DiscoveredPlatforms()
        self.candidate = candidate("https://board.example.com/jobs?q={query}",
                                   name="board", label="Board", country="Nigeria")

    def page(self, payload: str) -> str:
        return f'<html><script type="application/ld+json">{payload}</script></html>'

    def test_a_job_posting_is_read(self):
        payload = """{"@type": "JobPosting", "title": "Plumber",
            "description": "<p>Fix pipes</p>", "datePosted": "2026-08-17",
            "employmentType": "FULL_TIME",
            "hiringOrganization": {"@type": "Organization", "name": "Lagos Plumbing"},
            "jobLocation": {"address": {"addressLocality": "Lagos",
                                        "addressCountry": "NG"}}}"""
        jobs = self.source._from_jsonld(self.candidate, self.page(payload),
                                        "https://board.example.com/x")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].title, "Plumber")
        self.assertEqual(jobs[0].company, "Lagos Plumbing")
        self.assertIn("Lagos", jobs[0].location_raw)
        self.assertIn("Fix pipes", jobs[0].description)

    def test_postings_nested_in_a_graph_are_found(self):
        payload = """{"@context": "https://schema.org",
            "@graph": [{"@type": "WebSite"},
                       {"@type": "JobPosting", "title": "Gas Engineer"}]}"""
        jobs = self.source._from_jsonld(self.candidate, self.page(payload), "https://x/y")
        self.assertEqual([j.title for j in jobs], ["Gas Engineer"])

    def test_a_list_of_postings_is_read(self):
        payload = """[{"@type": "JobPosting", "title": "Welder"},
                      {"@type": "JobPosting", "title": "Fitter"}]"""
        jobs = self.source._from_jsonld(self.candidate, self.page(payload), "https://x/y")
        self.assertEqual(sorted(j.title for j in jobs), ["Fitter", "Welder"])

    def test_an_itemlist_with_links_is_followed(self):
        payload = """{"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "item": {"url": "https://board.example.com/j/1",
                                           "name": "Plumber"}}]}"""
        jobs = self.source._from_jsonld(self.candidate, self.page(payload), "https://x/y")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].url, "https://board.example.com/j/1")
        self.assertTrue(jobs[0].extra["truncated_description"])

    def test_an_itemlist_without_links_yields_nothing(self):
        # Jobberman publishes exactly this: names with no URLs. There is no
        # posting in the structured data, and inventing one would be worse.
        payload = """{"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "item": {"@type": "Thing", "name": "Plumber"}}]}"""
        self.assertEqual(
            self.source._from_jsonld(self.candidate, self.page(payload), "https://x/y"), [])

    def test_broken_json_does_not_raise(self):
        self.assertEqual(
            self.source._from_jsonld(self.candidate, self.page("{not json"), "https://x/y"), [])

    def test_a_page_without_structured_data_yields_nothing(self):
        self.assertEqual(
            self.source._from_jsonld(self.candidate, "<html>no markup</html>", "https://x/y"), [])

    def test_a_posting_without_a_title_is_skipped(self):
        payload = '{"@type": "JobPosting", "description": "no title"}'
        self.assertEqual(
            self.source._from_jsonld(self.candidate, self.page(payload), "https://x/y"), [])


class EmployerResolutionTests(unittest.TestCase):
    """schema.org lets the employer be a reference, or a paywalled placeholder."""

    def setUp(self):
        from job_agent.sources.boards import JsonLdBoard
        self.source = JsonLdBoard()
        self.candidate = candidate("https://board.example.com/jobs?q={query}",
                                   name="board", label="Board")

    def page(self, payload: str) -> str:
        return f'<html><script type="application/ld+json">{payload}</script></html>'

    def test_an_id_reference_resolves_to_the_named_employer(self):
        # Jobberman publishes exactly this shape.
        payload = """{"@graph": [
            {"@type": "Organization", "@id": "https://b/#/org/1", "name": "Greenbox Facilities"},
            {"@type": "JobPosting", "title": "Plumber",
             "hiringOrganization": {"@id": "https://b/#/org/1"}}]}"""
        jobs = self.source._from_jsonld(self.candidate, self.page(payload), "https://b/x")
        self.assertEqual(jobs[0].company, "Greenbox Facilities")

    def test_an_inline_employer_is_used_directly(self):
        payload = """{"@type": "JobPosting", "title": "Plumber",
            "hiringOrganization": {"@type": "Organization", "name": "Acme Ltd"}}"""
        jobs = self.source._from_jsonld(self.candidate, self.page(payload), "https://b/x")
        self.assertEqual(jobs[0].company, "Acme Ltd")

    def test_an_unresolvable_reference_becomes_undisclosed(self):
        payload = """{"@type": "JobPosting", "title": "Plumber",
            "hiringOrganization": {"@id": "https://b/#/org/missing"}}"""
        jobs = self.source._from_jsonld(self.candidate, self.page(payload), "https://b/x")
        self.assertEqual(jobs[0].company, "Undisclosed")

    def test_paywalled_employer_names_are_not_shown(self):
        # DailyRemote publishes "[Unlock with Premium]" as the company name.
        for hidden in ("[Unlock with Premium]", "Sign in to view", "Confidential",
                       "(hidden)", "N/A"):
            with self.subTest(hidden=hidden):
                self.assertEqual(self.source._clean_company(hidden, "Board"), "Undisclosed")

    def test_a_real_name_survives(self):
        for real in ("Greenbox Facilities Limited", "E-direct", "Kenex Konsults"):
            with self.subTest(real=real):
                self.assertEqual(self.source._clean_company(real, "Board"), real)


class DetailFollowingTests(unittest.TestCase):
    """Most boards put JobPosting on the advert page, not the results page."""

    def setUp(self):
        from job_agent.sources.boards import JsonLdBoard
        self.source = JsonLdBoard()

    def test_same_host_advert_links_are_collected(self):
        html = ('<a href="https://b.example.com/listings/plumber-x1">a</a>'
                '<a href="https://b.example.com/jobs/welder-x2">b</a>')
        links = self.source._detail_links(html, "https://b.example.com/search?q=x")
        self.assertEqual(len(links), 2)

    def test_off_host_links_are_not_followed(self):
        html = '<a href="https://evil.example.com/jobs/x1">a</a>'
        self.assertEqual(
            self.source._detail_links(html, "https://b.example.com/search"), [])

    def test_non_https_links_are_not_followed(self):
        html = '<a href="http://b.example.com/jobs/x1">a</a>'
        self.assertEqual(
            self.source._detail_links(html, "https://b.example.com/search"), [])

    def test_section_indexes_are_skipped(self):
        html = '<a href="https://b.example.com/jobs/">all jobs</a>'
        self.assertEqual(
            self.source._detail_links(html, "https://b.example.com/search"), [])

    def test_the_number_of_detail_pages_is_capped(self):
        html = "".join(f'<a href="https://b.example.com/jobs/role-{i}">x</a>'
                       for i in range(60))
        links = self.source._detail_links(html, "https://b.example.com/search")
        self.assertLessEqual(len(links), self.source.MAX_DETAIL_PAGES)

    def test_duplicate_links_collapse(self):
        html = ('<a href="https://b.example.com/jobs/x1">a</a>'
                '<a href="https://b.example.com/jobs/x1?utm=1">a</a>')
        self.assertEqual(
            len(self.source._detail_links(html, "https://b.example.com/search")), 1)


if __name__ == "__main__":
    unittest.main()
