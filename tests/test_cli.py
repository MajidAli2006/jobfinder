"""Smoke tests for the command line entry point."""

from __future__ import annotations

import io
import re
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from job_agent import __main__ as cli
from job_agent import profile

from .fixtures import flutter_uk_profile


class ParserTests(unittest.TestCase):

    def test_every_subcommand_is_wired_to_a_function(self):
        parser = cli.build_parser()
        for argv in (["daily"], ["sources"], ["status"],
                     ["check", "--title", "x"]):
            args = parser.parse_args(argv)
            self.assertTrue(callable(getattr(args, "func", None)), argv)

    def test_the_documented_flags_parse(self):
        parser = cli.build_parser()
        args = parser.parse_args([
            "daily", "--query", "ios", "--region", "USA, UK", "--cv", "/tmp/cv.txt",
            "--quick", "--days", "14", "--min-salary", "80000", "--offline",
            "--no-llm", "--no-verify", "--no-open",
        ])
        self.assertEqual(args.query, "ios")
        self.assertEqual(args.region, "USA, UK")
        self.assertTrue(args.quick)
        self.assertEqual(args.days, 14)
        self.assertEqual(args.min_salary, 80000)


class DailyRunTests(unittest.TestCase):
    """A full offline run through the real entry point."""

    def setUp(self):
        profile.set_active(flutter_uk_profile())

    def tearDown(self):
        profile.reset()

    def _run(self, extra=()):
        """Run daily offline and return (exit code, stdout, files written)."""
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["daily", "--offline", "--no-llm", "--no-verify",
                    "--no-open", "--output-dir", tmp, *extra]
            args = cli.build_parser().parse_args(argv)
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = args.func(args)
            written = {p.suffix for p in Path(tmp).iterdir()}
        return code, buffer.getvalue(), written

    def test_it_completes_and_writes_every_report(self):
        code, _, written = self._run()
        self.assertEqual(code, 0)
        self.assertEqual(written, {".xlsx", ".html", ".csv", ".json"})

    def test_the_report_sections_are_printed(self):
        _, output, _ = self._run()
        for heading in ("Job hunt", "Funnel", "QUALIFIED",
                        "Breakdown", "Reports", "Completed in"):
            self.assertIn(heading, output, heading)

    def test_a_narrower_window_is_reflected_in_the_output(self):
        _, output, _ = self._run(["--days", "7"])
        self.assertIn("last 7 calendar days", output)

    def test_quick_mode_is_announced(self):
        from job_agent import config
        saved = {name: getattr(config, name) for name, _ in config._QUICK_FIELDS}
        try:
            _, output, _ = self._run(["--quick"])
        finally:
            config.restore_budgets(saved)
        self.assertIn("quick", output)


class OtherCommandsTests(unittest.TestCase):

    def _capture(self, argv):
        args = cli.build_parser().parse_args(argv)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = args.func(args)
        return code, buffer.getvalue()

    def test_sources_lists_every_connector(self):
        code, output = self._capture(["sources"])
        self.assertEqual(code, 0)
        for name in ("RemoteOK", "LinkedIn", "Adzuna"):
            self.assertIn(name, output)

    def test_status_reports_the_tracker(self):
        code, output = self._capture(["status"])
        self.assertEqual(code, 0)
        self.assertTrue(output.strip())

    def test_check_explains_one_posting(self):
        # The exit code is the filter verdict, so it only means anything under a
        # profile that is actually looking for this role.
        with profile.using(flutter_uk_profile()):
            code, output = self._capture([
                "check", "--title", "Senior Flutter Engineer",
                "--description", "Fully remote, UK. Flutter and Dart required.",
            ])
        self.assertEqual(code, 0)
        self.assertTrue(output.strip())

    def test_check_rejects_a_posting_the_profile_is_not_looking_for(self):
        with profile.using(flutter_uk_profile()):
            code, output = self._capture([
                "check", "--title", "Head Chef",
                "--description", "On site in Leeds, five days a week.",
            ])
        self.assertEqual(code, 1)
        self.assertIn("REJECT", output)


class CvReadingTests(unittest.TestCase):

    def test_a_missing_cv_stops_with_a_clear_message(self):
        with self.assertRaises(SystemExit) as caught:
            cli.read_cv("/nonexistent/cv.pdf")
        self.assertIn("No CV at", str(caught.exception))

    def test_a_near_empty_cv_is_reported_rather_than_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cv.txt"
            path.write_text("Anna")
            with self.assertRaises(SystemExit) as caught:
                cli.read_cv(str(path))
            self.assertIn("almost no text", str(caught.exception))

    def test_a_real_text_cv_is_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cv.txt"
            path.write_text("Senior engineer. " * 40)
            self.assertGreater(len(cli.read_cv(str(path))), 200)


if __name__ == "__main__":
    unittest.main()


class NoSearchGuardTests(unittest.TestCase):
    """A run with nothing to search for must ask, not screen against nothing."""

    def tearDown(self):
        profile.reset()

    def _bare_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = cli.build_parser().parse_args(
                ["daily", "--offline", "--no-llm", "--no-verify", "--no-open",
                 "--output-dir", tmp])
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                code = args.func(args)
            written = {p.suffix for p in Path(tmp).iterdir()}
        return code, buffer.getvalue(), written

    def test_a_run_with_no_vocabulary_stops(self):
        profile.set_active(replace(flutter_uk_profile(), key="builtin", core_terms=()))
        code, output, written = self._bare_run()
        self.assertEqual(code, 1)
        self.assertIn("do not know what kind of work", output)

    def test_it_writes_no_report_it_cannot_stand_behind(self):
        profile.set_active(replace(flutter_uk_profile(), key="builtin", core_terms=()))
        _, _, written = self._bare_run()
        self.assertEqual(written, set())

    def test_it_says_how_to_fix_it(self):
        profile.set_active(replace(flutter_uk_profile(), key="builtin", core_terms=()))
        _, output, _ = self._bare_run()
        self.assertIn("--query", output)
        self.assertIn("--cv", output)


class AutoOpenTests(unittest.TestCase):
    """The workbook opens for a person, never for a test or a cron job.

    Auto-opening unconditionally pointed a viewer at the temporary directory
    these very tests write into, which is deleted as soon as the test ends —
    the user got a "couldn't find that file" dialog from a passing test run.
    """

    def setUp(self):
        profile.set_active(flutter_uk_profile())

    def tearDown(self):
        profile.reset()

    def _run(self, *, isatty: bool, extra=()):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            argv = ["daily", "--offline", "--no-llm", "--no-verify",
                    "--no-prompt", "--output-dir", tmp, *extra]
            args = cli.build_parser().parse_args(argv)
            with patch.object(cli, "open_file") as opener, \
                 patch.object(cli, "interactive", return_value=isatty), \
                 redirect_stdout(io.StringIO()):
                args.func(args)
            return opener

    def test_a_piped_run_does_not_open_anything(self):
        self.assertFalse(self._run(isatty=False).called)

    def test_an_interactive_run_opens_the_workbook(self):
        opener = self._run(isatty=True)
        self.assertTrue(opener.called)
        self.assertTrue(str(opener.call_args[0][0]).endswith(".xlsx"))

    def test_no_open_wins_even_when_interactive(self):
        self.assertFalse(self._run(isatty=True, extra=("--no-open",)).called)


class ConsoleEncodingTests(unittest.TestCase):
    """Windows consoles default to cp1252, which cannot encode a box rule.

    Forcing the codec reproduces that on any platform, so this stays a real
    test rather than something only CI can exercise.
    """

    def _run_under(self, encoding: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": encoding}
        with tempfile.TemporaryDirectory() as tmp:
            return subprocess.run(
                [sys.executable, "-m", "job_agent", "daily", "--offline", "--no-llm",
                 "--no-open", "--query", "warehouse jobs in Leeds", "--output-dir", tmp],
                capture_output=True, env=env, timeout=300,
                # The child is made to emit UTF-8 whatever its console claims, so
                # decode it as UTF-8 here too. Left to `text=True` this reads back
                # through the parent's own locale — cp1252 on Windows — and turns
                # correct output into mojibake.
                text=True, encoding="utf-8", errors="replace",
            )

    def test_a_cp1252_console_does_not_crash_the_run(self):
        # The header is printed before anything can fail, so the rule reaching
        # stdout is the whole claim. Whether the run then finds a search
        # depends on keys this test has no business needing.
        result = self._run_under("cp1252")
        self.assertNotIn("UnicodeEncodeError", result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("Job hunt", result.stdout)

    def test_the_header_still_draws_its_rule_on_a_utf8_console(self):
        result = self._run_under("utf-8")
        self.assertIn("╭─ Job hunt", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


class CompileFailureExitTests(unittest.TestCase):
    """A run that writes no report must not report success.

    Answering a question that was never a job search is a success. Being
    unable to build a search at all is not — a scheduled run has only the
    exit code to tell those apart, and it used to get 0 for both.
    """

    def _stop_code(self, compiled):
        from unittest.mock import patch
        args = cli.build_parser().parse_args(["daily", "--query", "anything"])
        with patch.object(cli.profile, "compile_profile", return_value=compiled):
            with redirect_stdout(io.StringIO()):
                _, stop = cli.compile_search(args)
        return stop

    def test_a_search_that_could_not_be_built_stops_with_one(self):
        failed = cli.profile.Compiled(
            answer="A custom search needs the Claude judgement layer", failed=True)
        self.assertEqual(self._stop_code(failed), 1)

    def test_a_question_that_was_answered_stops_with_zero(self):
        answered = cli.profile.Compiled(answer="Most welders are paid hourly.")
        self.assertEqual(self._stop_code(answered), 0)

    def test_a_real_search_does_not_stop_at_all(self):
        ok = cli.profile.Compiled(profile=flutter_uk_profile())
        self.assertIsNone(self._stop_code(ok))


class RegionNarrowsStandingSearchTests(unittest.TestCase):
    """`--region` points a standing search somewhere; it does not erase it.

    It is documented alongside `--days` and `--min-salary` as a modifier, but
    it used to force a recompile, so a user with a `default_search` who added
    `--region` was told to add a `default_search` — the one thing they had
    already done.
    """

    def tearDown(self):
        profile.reset()

    def _compile(self, argv, active=None):
        profile.set_active(active if active is not None else flutter_uk_profile())
        args = cli.build_parser().parse_args(["daily", *argv])
        with redirect_stdout(io.StringIO()) as buffer:
            search, stop = cli.compile_search(args)
        return search, stop, buffer.getvalue()

    def test_region_alone_keeps_the_standing_search(self):
        search, stop, _ = self._compile(["--region", "Nigeria"])
        self.assertIsNone(stop, "a standing search must not be abandoned")
        self.assertEqual(search.core_terms, flutter_uk_profile().core_terms)

    def test_region_alone_repoints_the_standing_search(self):
        search, _, _ = self._compile(["--region", "Nigeria"])
        self.assertEqual(search.home_country, "Nigeria")
        self.assertIn("Nigeria", search.target_regions)

    def test_it_names_the_search_it_settled_on(self):
        _, _, output = self._compile(["--region", "Nigeria"])
        self.assertIn("Search", output)

    def test_several_regions_are_all_targeted(self):
        search, _, _ = self._compile(["--region", "Nigeria, Kenya"])
        self.assertEqual(len(search.target_regions), 2)

    def test_no_standing_search_still_compiles(self):
        """Without vocabulary to narrow there is nothing to preserve."""
        from unittest.mock import patch
        empty = replace(flutter_uk_profile(), key="builtin", core_terms=())
        with patch.object(cli.profile, "compile_profile",
                          return_value=cli.profile.Compiled(
                              answer="need a key", failed=True)) as compile_profile:
            _, stop, _ = self._compile(["--region", "Nigeria"], active=empty)
        self.assertEqual(stop, 1)
        compile_profile.assert_called_once()

    def test_a_query_still_compiles_even_with_a_standing_search(self):
        from unittest.mock import patch
        with patch.object(cli.profile, "compile_profile",
                          return_value=cli.profile.Compiled(
                              profile=flutter_uk_profile())) as compile_profile:
            _, stop, _ = self._compile(["--query", "welder jobs", "--region", "Nigeria"])
        self.assertIsNone(stop)
        compile_profile.assert_called_once()


class InputValidationTests(unittest.TestCase):
    """Nonsense arguments are refused at the door, not carried into a run.

    A negative freshness window used to be accepted and printed back as
    "last -5 calendar days" while quietly discarding adverts, and a mistyped
    `--sources` name matched no source at all, producing an empty run with
    nothing said about why.
    """

    def parse(self, *argv):
        return cli.build_parser().parse_args(["daily", *argv])

    def test_a_freshness_window_must_be_at_least_a_day(self):
        for bad in ("-5", "0"):
            with self.subTest(days=bad), self.assertRaises(SystemExit):
                with redirect_stderr(io.StringIO()):
                    self.parse("--days", bad)

    def test_a_freshness_window_must_be_a_number(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            self.parse("--days", "abc")

    def test_a_sensible_window_is_accepted(self):
        self.assertEqual(self.parse("--days", "7").days, 7)

    def test_a_pay_floor_cannot_be_negative(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            self.parse("--min-salary", "-100")

    def test_a_pay_floor_of_zero_is_allowed(self):
        self.assertEqual(self.parse("--min-salary", "0").min_salary, 0.0)

    def test_a_mistyped_source_stops_the_run(self):
        args = self.parse("--offline", "--no-llm", "--no-open",
                          "--sources", "nosuchsource")
        with redirect_stdout(io.StringIO()) as buffer:
            code = args.func(args)
        self.assertEqual(code, 1)
        self.assertIn("No such source", buffer.getvalue())

    def test_it_names_the_sources_that_do_exist(self):
        from job_agent.sources import known_source_names
        args = self.parse("--offline", "--no-llm", "--no-open",
                          "--sources", "nosuchsource")
        with redirect_stdout(io.StringIO()) as buffer:
            args.func(args)
        self.assertIn("linkedin", buffer.getvalue())
        self.assertIn("linkedin", known_source_names())

    def test_a_real_source_name_is_accepted(self):
        from job_agent.sources import known_source_names
        for name in ("linkedin", "remoteok", "himalayas"):
            with self.subTest(source=name):
                self.assertIn(name, known_source_names())


class ReadmeMatchesTheParserTests(unittest.TestCase):
    """The README is the user's manual, so it has to describe this program.

    A flag that exists but is undocumented is invisible; a flag the README
    promises but the parser lacks is a broken promise. Both drift silently,
    so both are checked here rather than by eye.
    """

    README = Path(__file__).resolve().parent.parent / "README.md"

    def setUp(self):
        if not self.README.exists():
            self.skipTest("README.md is not part of this checkout")
        self.text = self.README.read_text(encoding="utf-8")

    def _subcommands(self):
        parser = cli.build_parser()
        for action in parser._actions:
            choices = getattr(action, "choices", None)
            if choices and hasattr(choices, "items"):
                yield from choices.items()

    def _flags(self):
        for name, sub in self._subcommands():
            for action in sub._actions:
                for option in action.option_strings:
                    if option not in ("-h", "--help"):
                        yield name, option

    def test_every_flag_is_documented(self):
        undocumented = sorted({opt for _cmd, opt in self._flags()
                               if opt not in self.text})
        self.assertEqual(undocumented, [], "flags missing from README.md")

    def test_every_command_is_documented(self):
        undocumented = sorted(name for name, _sub in self._subcommands()
                              if f"jobfinder {name}" not in self.text)
        self.assertEqual(undocumented, [], "commands missing from README.md")

    def test_the_readme_promises_no_flag_that_does_not_exist(self):
        real = {opt for _cmd, opt in self._flags()} | {"-h", "--help"}
        # `claude mcp add --scope user` is a different program's flag.
        external = {"--scope"}
        named = set(re.findall(r"--[a-z][a-z0-9-]+", self.text)) - external
        self.assertEqual(sorted(named - real), [], "README names flags that do not exist")
