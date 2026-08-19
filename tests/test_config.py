"""Tests for environment and path resolution."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def run_in(cwd: Path, snippet: str, env: dict | None = None) -> str:
    """Import config in a fresh interpreter and print what it resolved."""
    environment = {**os.environ, "PYTHONPATH": str(PROJECT), **(env or {})}
    for key in list(environment):
        if key.startswith(("JOOBLE", "ADZUNA", "REED", "ANTHROPIC", "JOBFINDER")):
            if not env or key not in env:
                environment.pop(key)
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(snippet)],
        cwd=cwd, env=environment, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class DotEnvResolutionTests(unittest.TestCase):

    def test_a_dotenv_in_the_working_directory_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ".env").write_text("JOOBLE_API_KEY=from-cwd\n")
            out = run_in(work, "from job_agent import config; print(config.JOOBLE_API_KEY)")
        self.assertEqual(out, "from-cwd")

    def test_a_real_environment_variable_beats_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ".env").write_text("JOOBLE_API_KEY=from-file\n")
            out = run_in(work, "from job_agent import config; print(config.JOOBLE_API_KEY)",
                         env={"JOOBLE_API_KEY": "from-shell"})
        self.assertEqual(out, "from-shell")

    def test_an_explicit_path_wins_over_the_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ".env").write_text("JOOBLE_API_KEY=from-cwd\n")
            elsewhere = work / "custom.env"
            elsewhere.write_text("JOOBLE_API_KEY=from-explicit\n")
            out = run_in(work, "from job_agent import config; print(config.JOOBLE_API_KEY)",
                         env={"JOBFINDER_ENV": str(elsewhere)})
        self.assertEqual(out, "from-explicit")

    def test_every_candidate_file_contributes_its_own_keys(self):
        """The files combine; they are not a first-match-wins single choice.

        A shared file can hold most keys while a project file adds one. It also
        means a variable unset in the shell is still supplied by any file that
        defines it, which is easy to mistake for a key not being set at all.
        """
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ".env").write_text("JOOBLE_API_KEY=from-cwd\nREED_API_KEY=only-in-cwd\n")
            named = work / "custom.env"
            named.write_text("JOOBLE_API_KEY=from-explicit\n")
            out = run_in(
                work,
                "from job_agent import config; "
                "print(config.JOOBLE_API_KEY, config.REED_API_KEY)",
                env={"JOBFINDER_ENV": str(named)},
            )
        chosen, only_in_cwd = out.split()
        self.assertEqual(chosen, "from-explicit", "the named file wins the shared key")
        self.assertEqual(only_in_cwd, "only-in-cwd", "the other file still contributes")

    def test_a_missing_dotenv_is_not_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_in(Path(tmp), "from job_agent import config; print('imported')")
        self.assertEqual(out, "imported")

    def test_quotes_and_export_prefixes_are_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ".env").write_text('export JOOBLE_API_KEY="quoted-value"\n')
            out = run_in(work, "from job_agent import config; print(config.JOOBLE_API_KEY)")
        self.assertEqual(out, "quoted-value")


class WorkspaceResolutionTests(unittest.TestCase):

    def test_a_source_checkout_keeps_its_own_directories(self):
        from job_agent import config
        self.assertTrue((config.ROOT / "pyproject.toml").is_file())
        self.assertEqual(config.WORKSPACE, config.ROOT)
        self.assertEqual(config.REPORTS_DIR, config.ROOT / "reports")

    def test_the_workspace_can_be_pointed_elsewhere(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_in(Path(tmp),
                         "from job_agent import config; print(config.REPORTS_DIR)",
                         env={"JOBFINDER_HOME": tmp})
        self.assertEqual(out, str(Path(tmp) / "reports"))

    def test_bundled_data_resolves_beside_the_code_not_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = run_in(Path(tmp), """
                from job_agent import config
                print(config.SAMPLE_JOBS_FILE.is_file(),
                      config.COMPANY_BOARDS_FILE.is_file())
                """, env={"JOBFINDER_HOME": tmp})
        self.assertEqual(out, "True True")


if __name__ == "__main__":
    unittest.main()


class OutputLocationTests(unittest.TestCase):
    """Finished workbooks go somewhere the user will actually see them."""

    def setUp(self):
        self._env = os.environ.get("JOBFINDER_OUTPUT_DIR")

    def tearDown(self):
        if self._env is None:
            os.environ.pop("JOBFINDER_OUTPUT_DIR", None)
        else:
            os.environ["JOBFINDER_OUTPUT_DIR"] = self._env

    def test_an_override_wins(self):
        from job_agent import config
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["JOBFINDER_OUTPUT_DIR"] = tmp
            self.assertEqual(config.output_dir(), Path(tmp))

    def test_the_override_expands_a_home_relative_path(self):
        from job_agent import config
        os.environ["JOBFINDER_OUTPUT_DIR"] = "~/somewhere"
        self.assertEqual(config.output_dir(), Path.home() / "somewhere")

    def test_it_lands_on_the_desktop_when_there_is_one(self):
        from job_agent import config
        os.environ.pop("JOBFINDER_OUTPUT_DIR", None)
        chosen = config.output_dir()
        if (Path.home() / "Desktop").is_dir():
            self.assertEqual(chosen, Path.home() / "Desktop" / "job finder")
        else:
            self.assertEqual(chosen, config.REPORTS_DIR)


class ReportNameTests(unittest.TestCase):
    """The filename says what was searched for and when."""

    def test_the_label_reads_as_a_name(self):
        from dataclasses import replace
        from job_agent import profile
        from job_agent.__main__ import report_label
        search = replace(profile.active(), label="Plumbing — Manchester")
        self.assertEqual(report_label(search), "Plumbing Manchester Jobs")

    def test_it_falls_back_to_the_query_then_to_a_default(self):
        from dataclasses import replace
        from job_agent import profile
        from job_agent.__main__ import report_label
        self.assertEqual(report_label(replace(profile.active(), label="", query="welder")),
                         "welder Jobs")
        self.assertEqual(report_label(replace(profile.active(), label="", query="")),
                         "Job Jobs")


class TierTests(unittest.TestCase):
    """Tiers decide how many requests a run is willing to make.

    Every test here goes through `config.tier`, because `use_tier` mutates
    module globals and quick also shrinks the fetch budgets — restoring only
    the flags would leave the budgets shrunk for every later test.
    """

    def test_quick_switches_every_extra_stage_off(self):
        from job_agent import config
        with config.tier("quick"):
            self.assertFalse(config.DISCOVER_EMPLOYER_BOARDS)
            self.assertFalse(config.DISCOVER_PLATFORMS)
            self.assertFalse(config.FETCH_EMPLOYER_CONTACTS)

    def test_quick_also_shrinks_the_fetch_budgets(self):
        from job_agent import config
        before = config.LINKEDIN_MAX_DETAILS
        with config.tier("quick"):
            self.assertLess(config.LINKEDIN_MAX_DETAILS, before)

    def test_normal_discovers_employer_boards_but_not_the_costly_stages(self):
        from job_agent import config
        with config.tier("normal"):
            self.assertTrue(config.DISCOVER_EMPLOYER_BOARDS)
            self.assertFalse(config.DISCOVER_PLATFORMS)
            self.assertFalse(config.FETCH_EMPLOYER_CONTACTS)

    def test_deep_switches_everything_on(self):
        from job_agent import config
        with config.tier("deep"):
            self.assertTrue(config.DISCOVER_EMPLOYER_BOARDS)
            self.assertTrue(config.DISCOVER_PLATFORMS)
            self.assertTrue(config.FETCH_EMPLOYER_CONTACTS)
            self.assertTrue(config.CHECK_COMPANY_SIZE_ONLINE)

    def test_an_unknown_tier_changes_nothing(self):
        from job_agent import config
        before = config.DISCOVER_PLATFORMS
        with config.tier("nonsense"):
            self.assertEqual(config.DISCOVER_PLATFORMS, before)

    def test_the_settings_come_back_afterwards(self):
        from job_agent import config
        before = (config.DISCOVER_PLATFORMS, config.LINKEDIN_MAX_DETAILS)
        with config.tier("quick"):
            pass
        self.assertEqual((config.DISCOVER_PLATFORMS, config.LINKEDIN_MAX_DETAILS), before)

    def test_an_exception_inside_still_restores(self):
        from job_agent import config
        before = config.LINKEDIN_MAX_DETAILS
        with self.assertRaises(RuntimeError), config.tier("quick"):
            raise RuntimeError("boom")
        self.assertEqual(config.LINKEDIN_MAX_DETAILS, before)


class TimezoneResolutionTests(unittest.TestCase):
    """Windows ships no IANA database, so this is the one import that can fail there."""

    def test_a_real_zone_resolves(self):
        from job_agent import config
        self.assertEqual(str(config.zone("Europe/London")), "Europe/London")

    def test_a_missing_database_names_the_package_that_fixes_it(self):
        from job_agent import config
        with self.assertRaises(RuntimeError) as caught:
            config.zone("Not/AZone")
        self.assertIn("tzdata", str(caught.exception))


class ProfileTimezoneTests(unittest.TestCase):
    """Dates are read in the candidate's timezone, not a fixed one.

    The freshness cutoff was already built from the profile's zone while the
    advert was converted to the default one, so a posting near midnight could
    fall a day either side of the window it belonged in.
    """

    def tearDown(self):
        from job_agent import profile
        profile.reset()

    def _under(self, zone: str):
        from dataclasses import replace
        from job_agent import profile
        profile.set_active(replace(profile.active(), timezone=zone))

    def test_the_profile_zone_is_used_when_set(self):
        from job_agent.utils import local_timezone
        for zone in ("Africa/Lagos", "Australia/Sydney", "America/New_York"):
            with self.subTest(zone=zone):
                self._under(zone)
                self.assertEqual(str(local_timezone()), zone)

    def test_an_unset_zone_falls_back_to_the_default(self):
        from job_agent import config
        from job_agent.utils import local_timezone
        self._under("")
        self.assertEqual(local_timezone(), config.TIMEZONE)

    def test_an_unknown_zone_falls_back_rather_than_raising(self):
        from job_agent import config
        from job_agent.utils import local_timezone
        self._under("Not/AZone")
        self.assertEqual(local_timezone(), config.TIMEZONE)

    def test_the_window_and_the_freshness_check_agree_on_the_boundary(self):
        # freshness_window builds the cutoff in the candidate's zone, so
        # check_freshness has to read the advert in that same zone.
        from datetime import timedelta
        from job_agent import filters
        from job_agent.models import RawJob
        from job_agent.utils import freshness_window

        def advert(when):
            return RawJob(source="t", source_id="1", title="Fitter", company="Acme",
                          url="https://e.com/1", description="x",
                          location_raw="anywhere", posted_at=when)

        for zone in ("Africa/Lagos", "Australia/Sydney", "America/New_York"):
            with self.subTest(zone=zone):
                self._under(zone)
                cutoff, _, _ = freshness_window(7)
                self.assertTrue(filters.check_freshness(advert(cutoff + timedelta(hours=1)),
                                                        cutoff).passed)
                self.assertFalse(filters.check_freshness(advert(cutoff - timedelta(hours=1)),
                                                         cutoff).passed)
