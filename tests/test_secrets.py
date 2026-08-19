"""Credentials must not reach logs, funnels or reports.

Some boards take their key in the URL path rather than a header. A failed
request then raises an exception carrying that whole URL, and anything that
logs the exception writes the key to the terminal and to any log file.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import logging
import unittest
from unittest.mock import patch

from job_agent import config, utils


class ScrubTests(unittest.TestCase):
    def test_a_configured_key_is_replaced(self):
        with patch.object(config, "JOOBLE_API_KEY", "sekrit-value-1234"):
            self.assertEqual(utils.scrub("GET /api/sekrit-value-1234"), "GET /api/***")

    def test_every_credential_is_covered(self):
        names = ("ANTHROPIC_API_KEY", "ADZUNA_APP_ID", "ADZUNA_APP_KEY",
                 "REED_API_KEY", "JOOBLE_API_KEY", "CAREERJET_API_KEY",
                 "INDEED_PUBLISHER_ID", "ZIPRECRUITER_API_KEY")
        for name in names:
            with self.subTest(name=name):
                with patch.object(config, name, "unique-secret-abcdef"):
                    self.assertNotIn("unique-secret-abcdef",
                                     utils.scrub("url unique-secret-abcdef here"))

    def test_short_values_are_not_scrubbed(self):
        # A two-character key would blank out ordinary text.
        with patch.object(config, "JOOBLE_API_KEY", "ab"):
            self.assertEqual(utils.scrub("ab normal text"), "ab normal text")

    def test_an_exception_object_is_accepted(self):
        with patch.object(config, "JOOBLE_API_KEY", "sekrit-value-1234"):
            error = RuntimeError("failed for url /api/sekrit-value-1234")
            self.assertNotIn("sekrit-value-1234", utils.scrub(error))

    def test_text_without_secrets_is_untouched(self):
        self.assertEqual(utils.scrub("nothing to hide"), "nothing to hide")


class SourceFailureLoggingTests(unittest.TestCase):
    """A failing keyed source must not log its key."""

    def test_the_key_never_reaches_the_log(self):
        from job_agent.sources.base import Source

        class Exploding(Source):
            name = "boom"
            label = "Boom"

            def fetch(self):
                raise RuntimeError("Max retries exceeded with url: /api/sekrit-value-1234")

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        log = logging.getLogger("job_agent.sources")
        log.addHandler(handler)
        try:
            with patch.object(config, "JOOBLE_API_KEY", "sekrit-value-1234"):
                with self.assertRaises(RuntimeError):
                    Exploding().collect()
        finally:
            log.removeHandler(handler)

        written = stream.getvalue()
        self.assertNotIn("sekrit-value-1234", written)
        self.assertIn("***", written)


class FunnelReportingTests(unittest.TestCase):
    """The funnel prints each source's error, so those are scrubbed too."""

    def test_a_failing_source_reports_without_its_key(self):
        from job_agent import pipeline
        from job_agent.sources.base import Source

        class Exploding(Source):
            name = "boom"
            label = "Boom"

            def fetch(self):
                raise RuntimeError(
                    "Max retries exceeded with url: /api/sekrit-value-1234")

        with patch.object(config, "JOOBLE_API_KEY", "sekrit-value-1234"), \
             patch.object(pipeline, "build_sources", return_value=[Exploding()]):
            _raws, stats = pipeline.collect()

        self.assertEqual(len(stats), 1)
        self.assertFalse(stats[0].ok)
        self.assertNotIn("sekrit-value-1234", stats[0].error)
        self.assertIn("***", stats[0].error)


if __name__ == "__main__":
    unittest.main()
