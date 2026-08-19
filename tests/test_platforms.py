"""Tests for the platform and credential registry.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from pathlib import Path

from job_agent import __main__ as cli
from job_agent import platforms


class RegionFilteringTests(unittest.TestCase):
    """Setup shows the keys that matter here, not every key that exists."""

    def names(self, country=""):
        return {p.name for p in platforms.for_region(country)}

    def test_worldwide_platforms_appear_everywhere(self):
        for country in ("Nigeria", "India", "United Kingdom", ""):
            self.assertIn("adzuna", self.names(country), country)
            self.assertIn("anthropic", self.names(country), country)

    def test_reed_only_appears_for_the_uk(self):
        self.assertIn("reed", self.names("United Kingdom"))
        self.assertNotIn("reed", self.names("Nigeria"))

    def test_ziprecruiter_only_appears_for_its_markets(self):
        self.assertIn("ziprecruiter", self.names("United States"))
        self.assertNotIn("ziprecruiter", self.names("Germany"))

    def test_local_platforms_appear_for_their_country(self):
        self.assertIn("jobberman", self.names("Nigeria"))
        self.assertIn("naukri", self.names("India"))
        self.assertIn("stepstone", self.names("Germany"))

    def test_no_region_shows_everything(self):
        self.assertEqual(self.names(""), {p.name for p in platforms.ALL})

    def test_the_country_match_is_case_insensitive(self):
        self.assertIn("reed", self.names("united kingdom"))


class CredentialStateTests(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.get("REED_API_KEY")
        os.environ.pop("REED_API_KEY", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("REED_API_KEY", None)
        else:
            os.environ["REED_API_KEY"] = self._saved

    def reed(self):
        return next(p for p in platforms.ALL if p.name == "reed")

    def test_a_missing_key_is_reported_by_name(self):
        self.assertEqual(self.reed().missing(), ("REED_API_KEY",))
        self.assertFalse(self.reed().configured)

    def test_a_present_key_counts_as_configured(self):
        os.environ["REED_API_KEY"] = "something"
        self.assertTrue(self.reed().configured)

    def test_whitespace_does_not_count_as_a_key(self):
        os.environ["REED_API_KEY"] = "   "
        self.assertFalse(self.reed().configured)

    def test_a_platform_with_no_credential_is_always_configured(self):
        jobberman = next(p for p in platforms.ALL if p.name == "jobberman")
        self.assertTrue(jobberman.configured)


class EnvVarNamingTests(unittest.TestCase):
    """Discovered platforms need a predictable, documentable variable name."""

    def test_names_are_derived_deterministically(self):
        self.assertEqual(platforms.env_var_for("jobberman"), "JOBFINDER_JOBBERMAN_API_KEY")
        self.assertEqual(platforms.env_var_for("Naukri"), "JOBFINDER_NAUKRI_API_KEY")

    def test_punctuation_becomes_underscores(self):
        self.assertEqual(platforms.env_var_for("my-board.co"), "JOBFINDER_MY_BOARD_CO_API_KEY")


class SaveKeyTests(unittest.TestCase):
    def test_a_key_is_appended_to_what_is_already_there(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("EXISTING=1\n", encoding="utf-8")
            platforms.save_key("JOBFINDER_TEST_API_KEY", "secret-value", env_path=path)
            body = path.read_text(encoding="utf-8")
            self.assertIn("EXISTING=1", body)
            self.assertIn("JOBFINDER_TEST_API_KEY=secret-value", body)

    # Windows has no POSIX mode bits — chmod there sets the read-only flag and
    # nothing else, so the file keeps whatever the directory ACL granted it.
    @unittest.skipIf(os.name == "nt", "POSIX mode bits do not exist on Windows")
    def test_the_key_file_is_readable_only_by_its_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            platforms.save_key("JOBFINDER_TEST_API_KEY", "secret-value", env_path=path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_file_without_a_trailing_newline_is_not_corrupted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("EXISTING=1", encoding="utf-8")
            platforms.save_key("SECOND_KEY", "two", env_path=path)
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(lines, ["EXISTING=1", "SECOND_KEY=two"])

    def test_it_creates_the_file_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            platforms.save_key("FIRST_KEY", "one", env_path=path)
            self.assertIn("FIRST_KEY=one", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class PromptTests(unittest.TestCase):
    """Keys are optional: skipping is always allowed and never blocks a run.

    The prompt lives in the CLI, not the registry, so importing the registry
    from the MCP server cannot write into the protocol stream.
    """

    def platform(self):
        return platforms.Platform("demo", "Demo Board", env=("DEMO_KEY",),
                                  signup="https://example.com/signup")

    def test_nothing_is_asked_when_nothing_is_missing(self):
        self.assertEqual(cli.prompt_for_keys(()), ())

    def test_the_registry_itself_never_prompts(self):
        # The registry is imported by the MCP server, where stdout is the
        # protocol stream. It must hold no interactive code at all.
        self.assertFalse(hasattr(platforms, "prompt_for_keys"))
        source = pathlib.Path(platforms.__file__).read_text()
        self.assertNotIn("print(", source)
        self.assertNotIn("getpass", source)

    def test_nothing_is_asked_without_a_terminal(self):
        from unittest.mock import patch
        with patch("sys.stdin.isatty", return_value=False):
            self.assertEqual(cli.prompt_for_keys((self.platform(),)), ())

    def test_an_empty_answer_skips_without_saving(self):
        from unittest.mock import patch
        with patch("sys.stdin.isatty", return_value=True), \
             patch.object(cli.getpass, "getpass", return_value=""), \
             patch.object(cli.platforms, "save_key") as save:
            self.assertEqual(cli.prompt_for_keys((self.platform(),)), ())
        save.assert_not_called()

    def test_an_answer_is_saved_and_reported_by_variable_name(self):
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch
        buffer = io.StringIO()
        with patch("sys.stdin.isatty", return_value=True), \
             patch.object(cli.getpass, "getpass", return_value="super-secret"), \
             patch.object(cli.platforms, "save_key", return_value="/tmp/.env") as save, \
             patch.dict(cli.os.environ, {}, clear=False), \
             redirect_stdout(buffer):
            saved = cli.prompt_for_keys((self.platform(),))
        self.assertEqual(saved, ("DEMO_KEY",))
        save.assert_called_once()
        self.assertNotIn("super-secret", buffer.getvalue(),
                         "the key itself must never be echoed")
        self.assertIn("DEMO_KEY", buffer.getvalue())

    def test_an_interrupt_stops_asking_without_crashing(self):
        from unittest.mock import patch
        with patch("sys.stdin.isatty", return_value=True), \
             patch.object(cli.getpass, "getpass", side_effect=KeyboardInterrupt):
            self.assertEqual(cli.prompt_for_keys((self.platform(),)), ())
